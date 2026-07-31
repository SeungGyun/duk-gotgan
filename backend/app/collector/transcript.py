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
from datetime import timedelta

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

from app.db.models import PipelineEvent, Transcript, Video
from config.time import now_kst

logger = logging.getLogger(__name__)

# 원문 보관 기간 (ROADMAP §3-3)
TTL_DAYS = 30

# 세그먼트 병합 단위. 원본 그대로 나열하면 줄 수가 많아 토큰이 낭비되고,
# 너무 크게 묶으면 타임스탬프 링크가 부정확해집니다.
MERGE_WINDOW_SEC = 15

# 요청 간 지연. youtube-transcript-api 는 공식 API 가 아니라, 빠르게
# 연속 호출하면 IP 가 막힙니다. 한 번 막히면 그날 수집이 통째로 멈추므로
# 넉넉히 쉬는 편이 쌉니다.
DELAY_RANGE = (3.0, 5.0)
MAX_RETRY = 3

# 한국어 토큰 추정치. 실제 값은 M4 에서 실측해 바로잡습니다.
CHARS_PER_TOKEN = 1.7


class TranscriptUnavailable(Exception):
    """이 영상에서는 자막을 얻을 수 없습니다. 사유는 사람 말로 씁니다."""


class Blocked(Exception):
    """유튜브가 우리 IP 를 막았습니다. 실행 전체를 멈춰야 합니다."""


@dataclass
class Fetched:
    source: str  # youtube_manual | youtube_auto
    language: str
    segments: list[dict]  # [{start, dur, text}]


def _pick_languages(video: Video) -> list[str]:
    """어느 언어 자막을 먼저 찾을지. 영상 언어를 알면 그것부터."""
    langs = ["ko", "en"]
    lang = (video.default_language or "").lower()[:2]
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

    try:
        available = api.list(video.id)
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        raise TranscriptUnavailable("자막이 제공되지 않는 영상입니다.") from e
    except (RequestBlocked, IpBlocked) as e:
        raise Blocked(
            "유튜브가 자막 요청을 차단했습니다. 잠시 후 다시 시도하거나 "
            "요청 간격을 늘려 주세요."
        ) from e
    except VideoUnavailable as e:
        raise TranscriptUnavailable("영상을 볼 수 없습니다(비공개·삭제).") from e
    except CouldNotRetrieveTranscript as e:
        raise TranscriptUnavailable(f"자막을 가져오지 못했습니다. ({type(e).__name__})") from e

    # 수동 자막 우선. 언어 선호 순서대로.
    for finder, source in (
        (available.find_manually_created_transcript, "youtube_manual"),
        (available.find_generated_transcript, "youtube_auto"),
    ):
        for lang in langs:
            try:
                t = finder([lang])
            except NoTranscriptFound:
                continue
            data = t.fetch().to_raw_data()
            if data:
                return Fetched(source=source, language=lang, segments=data)

    raise TranscriptUnavailable(f"쓸 수 있는 자막이 없습니다(찾은 언어: {langs}).")


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


def transcribe_pending(db: Session, limit: int = 20, run_id: str | None = None) -> dict:
    """자막 대기 중인 영상을 순서대로 처리합니다.

    **일부러 순차 처리합니다.** 동시에 던지면 IP 가 막히고, 한 번 막히면
    그날 수집 전체가 멈춥니다. 20건에 1~2분 걸리는 편이 훨씬 쌉니다.
    """
    videos = db.scalars(
        select(Video)
        .where(Video.state == "TRANSCRIPT_PENDING")
        .order_by(Video.discovered_at)
        .limit(limit)
    ).all()

    result = {"attempted": 0, "ok": 0, "failed": 0, "blocked": False, "rows": []}

    for i, video in enumerate(videos):
        if i:
            time.sleep(random.uniform(*DELAY_RANGE))
        result["attempted"] += 1
        try:
            fetched = _fetch_with_retry(video)
        except Blocked as e:
            logger.error("[transcript] 차단 — 남은 %d건 중단", len(videos) - i)
            result["blocked"] = True
            result["error"] = str(e)
            break
        except TranscriptUnavailable as e:
            video.state = "FAILED_TRANSCRIPT"
            video.state_reason = f"자막 없음 · {e}"
            _event(db, video, run_id, "TRANSCRIPT_PENDING", video.state, False, str(e))
            db.commit()
            result["failed"] += 1
            result["rows"].append((video.title, "✕", str(e)))
            continue

        row = store(db, video, fetched)
        video.state = "TRANSCRIBED"
        video.state_reason = None
        _event(db, video, run_id, "TRANSCRIPT_PENDING", video.state, True, fetched.source)
        db.commit()
        result["ok"] += 1
        result["rows"].append(
            (video.title, "○", f"{fetched.source} {fetched.language} · {row.est_tokens:,} 토큰")
        )

    return result


def _fetch_with_retry(video: Video) -> Fetched:
    """차단은 재시도로 못 풉니다 — 간격을 벌려야 합니다."""
    delay = 5.0
    for attempt in range(MAX_RETRY):
        try:
            return fetch(video)
        except Blocked:
            if attempt == MAX_RETRY - 1:
                raise
            logger.warning("[transcript] 차단 감지 — %.0f초 후 재시도", delay)
            time.sleep(delay)
            delay *= 2
    raise Blocked("반복 차단으로 중단했습니다.")


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
