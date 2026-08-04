"""자막 수집 — 파이프라인 3단계 (SPEC §4.3).

수동 자막을 최우선으로 씁니다. 한국어 자동 자막은 구두점이 없는 경우가
있고, 그러면 문장 경계가 뭉개져 요약 품질이 떨어집니다.

**문장 분리는 아직 안 합니다.** 구두점 없는 한국어를 문장으로 쪼개려면
형태소 분석기가 필요한데, 실측해 보니 요즘 한국어 자동 자막은 대부분
구두점이 붙어 나옵니다. 대신 `quality.has_punctuation` 을 재서 남기고,
AI 단계에서 "이 자막은 문장 경계가 불확실하다"를 알고 쓰게 합니다.
구두점 없는 자막의 요약이 실제로 나쁘면 그때 형태소 분석기를 붙입니다.

수집한 원문은 30일만 보관합니다 (ROADMAP §3-3). 요약이 끝나면 원문은
용량만 차지하고, 저작권 측면에서도 무기한 쌓아 둘 이유가 없습니다.
"""

import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

from app.collector import queue
from app.db import state
from app.db.models import AppState, PipelineEvent, Transcript, Video
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

# 원문 보관 기간 (ROADMAP §3-3)
TTL_DAYS = 30

# 세그먼트 병합 단위. 원본 그대로 나열하면 줄 수가 많아 토큰이 낭비되고,
# 너무 크게 묶으면 타임스탬프 링크가 부정확해집니다.
MERGE_WINDOW_SEC = 15

# 요청 간 지연. 공식 API 가 아니라 빠르게 연속 호출하면 IP 가 막힙니다.
# 3~5초로 두었다가 실제로 차단당했습니다(자막 30여 건 뒤). 넉넉히 쉬는 쪽이
# 훨씬 쌉니다 — 20건에 10분 걸려도 하루 1~2건 규모에선 문제가 안 됩니다.
DELAY_RANGE = (15.0, 30.0)
MAX_RETRY = 3

# 한국어 토큰 추정치. 실제 값은 M4 에서 실측해 바로잡습니다.
CHARS_PER_TOKEN = 1.7

# 이보다 짧으면 요약을 시도하지 않습니다.
#
# 쇼츠를 받기로 하면서 6초짜리 광고까지 들어왔는데, 자막이 20자 남짓이라
# AI 가 요약을 못 내놓고 실패로 남았습니다. 편당 6만 토큰을 버린 셈입니다.
# 실제로 요약에 성공한 것들의 최소 자막이 207자였습니다.
#
# 길이가 아니라 **글자 수**로 겁니다. 51초짜리가 1,280자로 멀쩡히 요약된
# 반면 10분짜리가 8자만 나온 경우도 있어서, 영상 길이는 기준이 못 됩니다.
MIN_SUMMARY_CHARS = 200


# 자막 출처 표시. 화면과 판정 근거에 그대로 남습니다 — 어느 경로로 받은
# 글인지 모르면 요약이 이상할 때 원인을 좁힐 수 없습니다.
LOCAL_ASR = "local_asr"


class TranscriptUnavailable(Exception):
    """이 영상에서는 자막을 얻을 수 없습니다. 사유는 사람 말로 씁니다."""


class Blocked(Exception):
    """유튜브가 우리 IP 를 막았습니다. 실행 전체를 멈춰야 합니다."""


@dataclass
class Fetched:
    source: str  # youtube_manual | youtube_auto | local_asr
    language: str
    segments: list[dict]  # [{start, dur, text}]


# 언어가 아닌 언어 코드들. 유튜브가 실제로 내려보냅니다.
#
#   zxx  "언어적 내용 없음" (음악·효과음만 있는 영상)
#   und  미정   mul  다국어   mis  분류 안 됨
#
# 두 글자로 자르면 `zxx` 가 `zx` 가 되어 진짜 언어 코드처럼 보입니다.
# 이걸 위스퍼에 넘겨 `ValueError: Unsupported language: zx` 로 받아쓰기
# 잡이 통째로 죽었습니다 — 한 영상 때문에 그 사이클의 나머지까지 멈췄습니다.
NOT_A_LANGUAGE = {"zx", "zxx", "un", "und", "mul", "mu", "mis", "mi"}


def _pick_languages(video: Video) -> list[str]:
    """어느 언어 자막을 먼저 찾을지. 영상 언어를 알면 그것부터."""
    langs = ["ko", "en"]
    raw = (video.default_language or "").lower()
    if raw.split("-")[0] in NOT_A_LANGUAGE:
        return langs
    lang = raw[:2]
    if lang and lang not in langs:
        langs.insert(0, lang)
    elif lang == "en":
        langs = ["en", "ko"]
    return langs


def fetch(video: Video) -> Fetched:
    """수동 → 자동 순으로 자막을 찾습니다.

    라이브러리의 `fetch()` 는 수동/자동을 구분하지 않고 아무거나 줍니다.
    품질 차이가 커서 **어느 쪽을 받았는지 알아야** 하므로, `list()` 로
    목록을 먼저 받아 직접 고릅니다.
    """
    api = YouTubeTranscriptApi()
    langs = _pick_languages(video)

    # 차단은 **목록 조회와 본문 다운로드 양쪽에서** 납니다. 한쪽만 감싸면
    # 다른 쪽에서 새어 나가 "차단"으로 인식되지 않고, 그러면 백오프가 걸리지
    # 않아 워커가 1분마다 계속 두드립니다 — 차단이 더 심해집니다.
    try:
        try:
            available = api.list(video.id)
        except (TranscriptsDisabled, NoTranscriptFound) as e:
            raise TranscriptUnavailable("자막이 제공되지 않는 영상입니다.") from e
        except VideoUnavailable as e:
            raise TranscriptUnavailable("영상을 볼 수 없습니다(비공개·삭제).") from e

        # 수동 자막 우선. 언어 선호 순서대로.
        for finder, source in (
            (available.find_manually_created_transcript, "youtube_manual"),
            (available.find_generated_transcript, "youtube_auto"),
        ):
            for lang in langs:
                try:
                    found = finder([lang])
                except NoTranscriptFound:
                    continue
                data = found.fetch().to_raw_data()
                if data:
                    return Fetched(source=source, language=lang, segments=data)

        raise TranscriptUnavailable(f"쓸 수 있는 자막이 없습니다(찾은 언어: {langs}).")

    except (RequestBlocked, IpBlocked) as e:
        raise Blocked(
            "유튜브가 자막 요청을 차단했습니다. 요청 간격을 늘리거나 "
            "잠시 뒤에 다시 시도합니다."
        ) from e
    except CouldNotRetrieveTranscript as e:
        raise TranscriptUnavailable(f"자막을 가져오지 못했습니다. ({type(e).__name__})") from e


# ── 폴백: yt-dlp ─────────────────────────────────────────────
# 자막에는 **공식 무료 경로가 없습니다.** Data API 의 captions.download 는
# OAuth + 영상 소유권이 필요해 남의 영상은 못 받습니다.
#
# ⚠️ **IP 차단에는 답이 아닙니다.** 두 라이브러리 모두 결국 같은
# `youtube.com/api/timedtext` 를 부릅니다. 실측해 보니 그 엔드포인트가 429 를
# 주는 동안에는 클라이언트를 바꿔도, 포맷을 json3→srv1→vtt 로 바꿔도 전부
# 막힙니다. 차단 대책은 요청 간격(DELAY_RANGE)과 냉각(BLOCK_COOLDOWN_MIN)입니다.
#
# 그래도 두는 이유는 **고장의 종류가 다르기 때문**입니다. 유튜브가 내부 구조를
# 바꾸면 youtube-transcript-api 는 며칠씩 깨져 있는 반면 yt-dlp 는 보통 하루
# 안에 고쳐집니다. 파싱이 깨진 경우에는 이쪽이 살립니다.


def _ytdlp_pick(tracks: dict, langs: list[str]) -> tuple[str, str] | None:
    """(자막 URL, 언어). **원본 음성인식을 우선**합니다.

    자동 자막 목록에는 원본(`ko-orig`)과 157개 언어로 기계번역된 것(`ko`,
    `en`, …)이 섞여 있습니다. 번역본을 집으면 기계번역을 요약하게 됩니다.
    """
    for lang in langs:
        for key in (f"{lang}-orig", lang):
            for track in tracks.get(key) or []:
                if track.get("ext") == "json3" and track.get("url"):
                    return track["url"], key
    return None


def _ytdlp_parse(payload: dict) -> list[dict]:
    """json3 → [{start, dur, text}]."""
    out = []
    for ev in payload.get("events") or []:
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        out.append(
            {
                "start": (ev.get("tStartMs") or 0) / 1000,
                "dur": (ev.get("dDurationMs") or 0) / 1000,
                "text": text,
            }
        )
    return out


def fetch_via_ytdlp(video: Video) -> Fetched:
    import json as _json

    import yt_dlp

    langs = _pick_languages(video)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video.id}", download=False
            )

            for tracks, source in (
                (info.get("subtitles") or {}, "youtube_manual"),
                (info.get("automatic_captions") or {}, "youtube_auto"),
            ):
                picked = _ytdlp_pick(tracks, langs)
                if not picked:
                    continue
                url, lang = picked
                # **yt-dlp 의 세션으로 받습니다.** 맨몸 urlopen 은 User-Agent 가
                # "Python-urllib" 이라 봇으로 보여 429 를 맞습니다 (실제로 맞았습니다).
                # ydl 은 브라우저 헤더와 쿠키를 이미 들고 있습니다.
                raw = ydl.urlopen(url).read().decode("utf-8")
                segments = _ytdlp_parse(_json.loads(raw))
                if segments:
                    return Fetched(
                        source=source, language=lang.replace("-orig", ""), segments=segments
                    )
    except Exception as e:  # noqa: BLE001 — 라이브러리가 예외를 세분화하지 않습니다
        raise TranscriptUnavailable(f"yt-dlp 로도 받지 못했습니다. ({type(e).__name__})") from e

    raise TranscriptUnavailable(f"yt-dlp 에도 쓸 수 있는 자막이 없습니다(찾은 언어: {langs}).")


# ── 전처리 ───────────────────────────────────────────────────

# 자동 자막에 섞이는 소리 표시. 요약에 도움이 안 되고 토큰만 씁니다.
_NOISE = re.compile(r"\[(음악|박수|웃음|Music|Applause|Laughter)[^\]]*\]", re.I)
_SPACES = re.compile(r"\s+")


def merge_segments(segments: list[dict], window: int = MERGE_WINDOW_SEC) -> list[dict]:
    """15초 단위로 묶습니다.

    원본 세그먼트는 2~3초짜리라, 그대로 `[MM:SS] 한 줄`로 쓰면 타임스탬프
    줄만 수천 개가 됩니다. 그 자체가 입력 토큰입니다.
    """
    out: list[dict] = []
    for seg in segments:
        text = _SPACES.sub(" ", _NOISE.sub("", seg.get("text", ""))).strip()
        if not text:
            continue
        start = float(seg.get("start", 0))
        if out and start - out[-1]["start"] < window:
            out[-1]["text"] += " " + text
        else:
            out.append({"start": start, "text": text})
    return out


def to_markdown(merged: list[dict]) -> str:
    """`[MM:SS] 본문` 형태. AI 워크스페이스에 그대로 쓰는 형식입니다."""
    lines = []
    for m in merged:
        sec = int(m["start"])
        stamp = f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"
        lines.append(f"[{stamp}] {m['text']}")
    return "\n".join(lines)


def quality_of(merged: list[dict], source: str) -> dict:
    """요약 전에 "이 자막을 믿어도 되는가"를 재 둡니다.

    구두점 비율이 낮으면 자동 자막이고, 그러면 문장 경계가 뭉개져 있어
    요약이 흔들립니다. AI 가 커버리지 주석을 달 근거로도 씁니다.
    """
    text = " ".join(m["text"] for m in merged)
    n = len(text) or 1
    punct = sum(text.count(c) for c in ".?!。？！")
    spans = [merged[i + 1]["start"] - merged[i]["start"] for i in range(len(merged) - 1)]
    return {
        "source": source,
        "has_punctuation": punct / n > 0.002,
        "punct_per_1k": round(punct / n * 1000, 1),
        "avg_segment_sec": round(sum(spans) / len(spans), 1) if spans else 0,
        "line_count": len(merged),
    }


# ── 적재 ─────────────────────────────────────────────────────


def store(db: Session, video: Video, fetched: Fetched) -> Transcript:
    merged = merge_segments(fetched.segments)
    body = to_markdown(merged)

    row = db.get(Transcript, video.id)
    if row is None:
        row = Transcript(video_id=video.id)
        db.add(row)

    row.source = fetched.source
    row.language = fetched.language
    row.content = body
    row.segments = merged
    row.char_count = len(body)
    row.est_tokens = int(len(body) / CHARS_PER_TOKEN)
    row.quality = quality_of(merged, fetched.source)
    row.expires_at = now_kst() + timedelta(days=TTL_DAYS)
    row.created_at = now_kst()
    return row


# 차단당한 뒤 다시 시도하기까지 쉬는 시간. 차단은 분 단위가 아니라 시간
# 단위 문제라, 1분 뒤에 다시 두드리면 차단만 길어집니다.
#
# **연속으로 막히면 배로 늘립니다.** 60분 고정으로 두고 관찰해 보니, 매시간
# 정확히 한 번 두드리고 매번 429 를 받았습니다 — 5시간 동안 성공 0건.
# 풀리지 않는 차단에 규칙적으로 노크하는 것은 차단을 갱신시킬 뿐입니다.
# 성공하면 다시 기본값으로 돌아갑니다.
BLOCK_COOLDOWN_MIN = 60
BLOCK_COOLDOWN_MAX_MIN = 8 * 60

# **DB 에 남깁니다.** 전역 변수로 두었더니 워커가 재시작할 때마다 냉각이
# 풀린 것처럼 되어 곧바로 차단된 문을 다시 두드렸고(로그에 10:04·10:05·
# 10:10 세 번 연속), 백오프가 한 번도 쌓이지 못했습니다. 화면 쪽은 API
# 프로세스의 전역을 읽어서 값이 아예 없었습니다.
COOLDOWN_KEY = "transcript.blocked_until"
STREAK_KEY = "transcript.block_streak"


def cooldown_minutes(streak: int) -> int:
    """연속 차단 횟수에 따른 대기 시간 — 60 · 120 · 240 · 480분에서 멈춥니다."""
    return min(BLOCK_COOLDOWN_MIN * 2 ** max(0, streak - 1), BLOCK_COOLDOWN_MAX_MIN)


def blocked_until(db: Session) -> datetime | None:
    """차단 냉각이 걸려 있으면 언제까지인지. 워커와 화면이 같은 값을 봅니다."""
    return state.get_time(db, COOLDOWN_KEY)


def _streak(db: Session) -> int:
    row = db.get(AppState, STREAK_KEY)
    return int(row.value) if row is not None and row.value.isdigit() else 0


def _set_streak(db: Session, n: int) -> None:
    row = db.get(AppState, STREAK_KEY)
    if row is None:
        row = AppState(key=STREAK_KEY)
        db.add(row)
    row.value = str(n)
    row.updated_at = now_kst()
    db.commit()


def transcribe_pending(db: Session, limit: int = 20, run_id: str | None = None) -> dict:
    """자막 대기 중인 영상을 순서대로 처리합니다.

    **일부러 순차 처리합니다.** 동시에 던지면 IP 가 막히고, 한 번 막히면
    그날 수집 전체가 멈춥니다. 20건에 1~2분 걸리는 편이 훨씬 쌉니다.
    """
    result = {"attempted": 0, "ok": 0, "failed": 0, "blocked": False, "asr": 0, "rows": []}

    # 냉각 중이라고 손을 놓지 않습니다. **자막 경로만 쉬게 두고** 받아쓰기로
    # 갑니다 — 어차피 막힌 문을 영상마다 25초씩 두드릴 이유가 없습니다.
    cooling = blocked_until(db)
    skip_youtube = bool(cooling and now_kst() < cooling)
    if skip_youtube:
        logger.info(
            "[transcript] 자막 경로 냉각 중(%s 재개) — 받아쓰기로 갑니다", f"{cooling:%H:%M}"
        )

    # 키워드끼리 번갈아 집습니다. 먼저 온 순서대로 하면 첫 키워드가 줄의
    # 앞을 통째로 차지해 나머지는 영원히 차례가 안 옵니다 (queue.py 참고).
    ids = queue.next_ids(db, "TRANSCRIPT_PENDING", limit)
    videos = [db.get(Video, i) for i in ids]
    videos = [v for v in videos if v is not None]

    spent = 0.0  # 이번 사이클에 받아쓰기로 쓴 시간
    for i, video in enumerate(videos):
        if spent > settings.asr_budget_sec:
            logger.info(
                "[transcript] 받아쓰기 시간 상한(%d분) — 남은 %d건은 다음 사이클로",
                settings.asr_budget_sec // 60, len(videos) - i,
            )
            break
        if i:
            # 받아쓰기 경로는 자막 엔드포인트를 건드리지 않아서 길게 쉴
            # 이유가 없습니다. 오디오 호스트에 대한 예의만 지킵니다.
            time.sleep(random.uniform(2.0, 5.0) if skip_youtube else random.uniform(*DELAY_RANGE))
        result["attempted"] += 1
        started = time.time()

        # **지금 붙들고 있다는 표시를 남깁니다.** 받아쓰기 한 편이 2~7분인데
        # 그동안 아무 기록이 없으면, 화면에서는 "자막 대기 107" 이 멈춘
        # 건지 도는 건지 구분되지 않습니다. 워커가 죽으면 좀비 회수가
        # 대기로 되돌립니다.
        video.state = "TRANSCRIBING"
        video.updated_at = now_kst()
        db.commit()

        try:
            fetched = _fetch_with_retry(video, skip_youtube=skip_youtube)
        except Blocked as e:
            video.state = "TRANSCRIPT_PENDING"  # 붙들고 있던 표시를 풉니다
            db.commit()
            streak = _streak(db) + 1
            _set_streak(db, streak)
            wait = cooldown_minutes(streak)
            until = now_kst() + timedelta(minutes=wait)
            state.set_time(db, COOLDOWN_KEY, until)
            logger.error(
                "[transcript] 차단 %d회 연속 — 남은 %d건 중단, %d분간 쉽니다 (%s 재개)",
                streak, len(videos) - i, wait, f"{until:%H:%M}",
            )
            result["blocked"] = True
            result["error"] = f"{e} ({wait}분 후 재개)"
            break
        except TranscriptUnavailable as e:
            video.state = "FAILED_TRANSCRIPT"
            video.state_reason = f"자막 없음 · {e}"
            _event(db, video, run_id, "TRANSCRIBING", video.state, False, str(e))
            db.commit()
            result["failed"] += 1
            result["rows"].append((video.title, "✕", str(e)))
            continue

        row = store(db, video, fetched)

        if row.char_count < MIN_SUMMARY_CHARS:
            # 요약할 내용이 없습니다. 사유에 길이와 글자 수를 같이 적어
            # 두면, 짧은 영상이라 그런지 받아쓰기가 헛돈 것인지 화면에서
            # 바로 갈립니다.
            video.state = "FAILED_TRANSCRIPT"
            video.state_reason = (
                f"요약할 내용이 없습니다 · {video.duration_sec}초 영상에 {row.char_count}자"
            )
            _event(db, video, run_id, "TRANSCRIBING", video.state, False, video.state_reason)
            db.commit()
            result["failed"] += 1
            result["rows"].append((video.title, "✕", video.state_reason))
            continue

        video.state = "TRANSCRIBED"
        video.state_reason = None
        _event(db, video, run_id, "TRANSCRIBING", video.state, True, fetched.source)
        db.commit()
        result["ok"] += 1
        result["rows"].append(
            (video.title, "○", f"{fetched.source} {fetched.language} · {row.est_tokens:,} 토큰")
        )

        if fetched.source == LOCAL_ASR:
            result["asr"] += 1
            spent += time.time() - started
            if not skip_youtube:
                # 자막 경로가 죽어서 받아쓰기로 넘어온 것입니다. 남은 영상은
                # 같은 문을 다시 두드리지 않게 이 사이클부터 바로 우회합니다.
                streak = _streak(db) + 1
                _set_streak(db, streak)
                until = now_kst() + timedelta(minutes=cooldown_minutes(streak))
                state.set_time(db, COOLDOWN_KEY, until)
                skip_youtube = True
                logger.warning(
                    "[transcript] 자막 경로 실패 %d회 — %s 까지 받아쓰기로 돕니다",
                    streak, f"{until:%H:%M}",
                )
        else:
            # 자막을 받았으면 차단이 풀린 것 — 대기를 되돌립니다
            _set_streak(db, 0)
            state.set_time(db, COOLDOWN_KEY, None)

    return result


def _fetch_with_retry(video: Video, *, skip_youtube: bool = False) -> Fetched:
    """세 경로를 순서대로 시도합니다.

      1. youtube-transcript-api   공짜·즉시 — 있으면 언제나 이걸 씁니다
      2. yt-dlp                   같은 엔드포인트, 다른 클라이언트
      3. 로컬 받아쓰기             소리를 받아 직접 — 느리지만 안 막힙니다

    **1·2 가 먼저인 이유**는 순전히 비용입니다. 이미 만들어진 자막을 받는
    데는 1초가 걸리고, 받아쓰기는 36분짜리 한 편에 3분이 걸립니다. 자막이
    살아 있는 동안 GPU 를 돌릴 이유가 없습니다.

    차단은 재시도로 풀리지 않습니다 — 같은 경로로 다시 두드리면 차단만
    길어집니다. 그래서 짧게 두 번만 더 시도해 보고, 안 되면 **경로를 바꿉니다.**
    """
    if not skip_youtube:
        delay = 5.0
        for attempt in range(MAX_RETRY):
            try:
                return fetch(video)
            except Blocked:
                if attempt == MAX_RETRY - 1:
                    break
                logger.warning("[transcript] 차단 감지 — %.0f초 후 재시도", delay)
                time.sleep(delay)
                delay *= 2

        logger.warning("[transcript] %s — 1차 경로 차단, yt-dlp 로 시도합니다", video.id)
        try:
            fetched = fetch_via_ytdlp(video)
        except TranscriptUnavailable:
            pass  # 아래 받아쓰기로 넘어갑니다
        else:
            logger.info("[transcript] %s — yt-dlp 폴백 성공", video.id)
            return fetched

    return fetch_via_asr(video)


def fetch_via_asr(video: Video) -> Fetched:
    """소리를 받아 직접 받아씁니다. 자막 경로가 전부 막혔을 때만."""
    from app.collector import asr

    langs = _pick_languages(video)
    try:
        r = asr.transcribe(video.id, video.duration_sec or 0, language=langs[0] if langs else "ko")
    except asr.AudioUnavailable as e:
        # 이 영상만의 문제입니다. 자막 없음으로 적고 다음 영상으로 넘어갑니다 —
        # 차단으로 다루면 멀쩡한 나머지까지 60분씩 멈춥니다.
        raise TranscriptUnavailable(str(e)) from None
    except asr.AsrUnavailable as e:
        # 받아쓰기까지 못 하면 **차단으로 다룹니다.** 이 영상만의 문제인지
        # IP 문제인지 구분할 수 없는데, 자막 없음으로 기록해 버리면 나중에
        # 차단이 풀려도 다시 시도하지 않습니다.
        raise Blocked(f"자막 경로가 모두 막혔고 받아쓰기도 못 했습니다 — {e}") from None
    return Fetched(source=LOCAL_ASR, language=r.language, segments=r.segments)


def _event(db: Session, video: Video, run_id, frm: str, to: str, ok: bool, detail: str) -> None:
    db.add(
        PipelineEvent(
            video_id=video.id,
            run_id=run_id,
            from_state=frm,
            to_state=to,
            stage="transcript",
            ok=ok,
            detail={"detail": detail} if detail else None,
        )
    )
