"""자막 전처리 — 네트워크 없이 돕니다.

병합은 토큰 수에 직접 영향을 줍니다. 원본 2~3초 세그먼트를 그대로 쓰면
타임스탬프 줄만 수천 개가 되고, 그 자체가 입력 토큰입니다.
"""

import pytest

from app.collector.transcript import (
    CHARS_PER_TOKEN,
    merge_segments,
    quality_of,
    to_markdown,
)


def seg(start, text, dur=3.0):
    return {"start": start, "dur": dur, "text": text}


def test_15초_안쪽은_한_줄로_묶인다():
    merged = merge_segments([seg(0, "안녕하세요"), seg(3, "오늘은"), seg(9, "CNI 를 다룹니다")])
    assert len(merged) == 1
    assert merged[0]["text"] == "안녕하세요 오늘은 CNI 를 다룹니다"


def test_15초를_넘으면_새_줄():
    merged = merge_segments([seg(0, "앞부분"), seg(16, "뒷부분")])
    assert [m["start"] for m in merged] == [0, 16]


def test_소리표시와_빈줄은_버린다():
    """[음악] 같은 표시는 요약에 쓸모가 없고 토큰만 씁니다."""
    merged = merge_segments([seg(0, "[음악]"), seg(1, "  "), seg(2, "본문")])
    assert len(merged) == 1
    assert merged[0]["text"] == "본문"


def test_타임스탬프_형식():
    md = to_markdown([{"start": 0, "text": "가"}, {"start": 65, "text": "나"},
                      {"start": 3725, "text": "다"}])
    assert md.splitlines() == ["[0:00] 가", "[1:05] 나", "[1:02:05] 다"]


def test_한시간_넘으면_시간까지_표시():
    """[65:05] 로 찍히면 사람도 유튜브 링크도 못 읽습니다."""
    assert "[1:02:05]" in to_markdown([{"start": 3725, "text": "다"}])


def test_구두점_없는_자동자막을_표시한다():
    """AI 단계가 "문장 경계를 믿지 말라"를 알아야 합니다."""
    plain = quality_of([{"start": 0, "text": "구두점 없이 쭉 이어지는 문장 " * 20}], "youtube_auto")
    assert plain["has_punctuation"] is False

    normal = quality_of([{"start": 0, "text": "문장입니다. 다음 문장입니다. 또 있습니다."}], "youtube_manual")
    assert normal["has_punctuation"] is True


def test_토큰_추정이_실측_범위_안에_있다():
    """M3 실측: 자막 11건에서 분당 204~322 토큰(평균 250).

    15초 구간의 한국어 발화는 대략 70~90자입니다. 그 길이로 60분짜리를
    만들어, 추정식(글자수 ÷ CHARS_PER_TOKEN)이 실측과 같은 자리에
    떨어지는지 봅니다. 여기가 어긋나면 비용 추정이 통째로 틀어집니다.
    """
    line = (
        "그래서 이 구간에서 무슨 일이 벌어지느냐면요 클라이언트가 재시도를 보내고 "
        "게이트웨이는 그걸 새 요청으로 봅니다 결국 결제가 두 번 일어나는 겁니다"
    )
    assert 75 <= len(line) <= 95, "실측 기준 문장 길이를 벗어난 픽스처입니다"

    body = to_markdown(merge_segments([seg(i * 15, line) for i in range(240)]))
    per_min = int(len(body) / CHARS_PER_TOKEN) / 60
    assert 200 <= per_min <= 330, f"분당 {per_min:.0f} 토큰 — 실측(204~322)을 벗어났습니다"


def test_다운로드_단계_차단도_Blocked_로_잡힌다():
    """목록 조회만 감싸면 다운로드에서 난 차단이 새어 나가고, 그러면
    백오프가 안 걸려 워커가 1분마다 계속 두드립니다."""
    from unittest.mock import patch

    from youtube_transcript_api._errors import IpBlocked

    from app.collector import transcript as T
    from app.db.models import Video

    class FakeFound:
        def fetch(self):
            raise IpBlocked("x")

    class FakeList:
        def find_manually_created_transcript(self, langs):
            return FakeFound()

        def find_generated_transcript(self, langs):
            return FakeFound()

    video = Video(id="x", title="t", channel_title="c", duration_sec=100)
    with patch.object(T.YouTubeTranscriptApi, "list", lambda self, vid: FakeList()):
        with pytest.raises(T.Blocked):
            T.fetch(video)


def test_ytdlp_는_번역본이_아니라_원본을_고른다():
    """자동 자막 목록에는 원본(ko-orig)과 157개 언어 기계번역이 섞여
    있습니다. 번역본을 집으면 기계번역을 요약하게 됩니다."""
    from app.collector.transcript import _ytdlp_pick

    tracks = {
        "en": [{"ext": "json3", "url": "en-translated"}],
        "ko": [{"ext": "json3", "url": "ko-translated"}],
        "ko-orig": [{"ext": "json3", "url": "ko-original"}],
    }
    url, lang = _ytdlp_pick(tracks, ["ko", "en"])
    assert url == "ko-original"
    assert lang == "ko-orig"


def test_ytdlp_json3_파싱():
    from app.collector.transcript import _ytdlp_parse

    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2500, "segs": [{"utf8": "안녕"}, {"utf8": "하세요"}]},
            {"tStartMs": 3000, "dDurationMs": 1000, "segs": [{"utf8": "\n"}]},  # 빈 줄
            {"tStartMs": 4000, "dDurationMs": 2000, "segs": [{"utf8": "본문"}]},
        ]
    }
    out = _ytdlp_parse(payload)
    assert [s["text"] for s in out] == ["안녕하세요", "본문"]
    assert out[0]["start"] == 0 and out[1]["start"] == 4.0


# ── 차단 냉각 ───────────────────────────────────────────────
#
# 실제로 있었던 일: 대기가 남은 채 유튜브가 막히자, 워커가 주기마다
# "할 일 있음"으로 판단해 실행 기록을 만들고 곧바로 실패 처리했습니다.
# 6분 만에 실패 6줄이 쌓였습니다. 냉각은 실패가 아닙니다.
#
# 지금은 냉각 중에도 받아쓰기로 처리하므로 대개 일이 됩니다. 정말 아무것도
# 못 하는 경우에만 기록을 남기지 않습니다.


def test_아무것도_못_하면_기록을_남기지_않는다():
    """30초마다 빈 기록이 쌓이면 실행 로그가 덮입니다."""
    import inspect

    from app.collector import jobs

    src = inspect.getsource(jobs.transcript_job)
    assert "db.delete(run)" in src


def test_차단은_실패가_아니라_보류다():
    """상태 판정의 근거가 되는 자리를 갈라 둡니다."""
    from app.collector.jobs import JobResult

    r = JobResult(job="transcript")
    r.notes.append("")
    r.notes.clear()
    assert not r.notes and not r.did_work


def test_영상_하나의_오디오_실패는_사이클을_죽이지_않는다(monkeypatch):
    """실제로 있었던 일: 영상 한 편의 403 이 yt-dlp DownloadError 로 새어
    나가 사이클 전체를 죽였습니다. 자막도 검토도 못 하고 실행 기록 여섯
    개가 `running` 인 채로 남았습니다.

    영상 하나의 문제는 그 영상만 실패로 적고 넘어가야 합니다 — 차단으로
    다루면 멀쩡한 나머지까지 60분씩 멈춥니다."""
    import pytest

    from app.collector import asr, transcript

    monkeypatch.setattr(
        asr, "transcribe",
        lambda *a, **k: (_ for _ in ()).throw(asr.AudioUnavailable("오디오를 받지 못했습니다 (DownloadError)")),
    )

    class V:
        id, duration_sec, default_language, title = "v", 600, "ko", "t"

    with pytest.raises(transcript.TranscriptUnavailable):
        transcript.fetch_via_asr(V())


def test_받아쓰기_자체가_안_되면_차단으로_다룬다(monkeypatch):
    """ffmpeg 가 없는 것은 모든 영상에 해당합니다. 이걸 '자막 없음'으로
    적으면 나중에 고쳐도 다시 시도하지 않습니다."""
    import pytest

    from app.collector import asr, transcript

    monkeypatch.setattr(
        asr, "transcribe",
        lambda *a, **k: (_ for _ in ()).throw(asr.AsrUnavailable("ffmpeg 가 없습니다.")),
    )

    class V:
        id, duration_sec, default_language, title = "v", 600, "ko", "t"

    with pytest.raises(transcript.Blocked):
        transcript.fetch_via_asr(V())


def test_좀비_회수는_되돌릴_자리를_가른다():
    """검토 중이던 것은 자막이 이미 있고, 받아쓰기 중이던 것은 없습니다.
    한꺼번에 TRANSCRIBED 로 밀면 자막 없는 영상이 검토로 넘어가 AI 를
    자막 없이 부릅니다."""
    import inspect

    from app.llm import runner

    src = inspect.getsource(runner.recover_zombies)
    assert '"REVIEWING": "TRANSCRIBED"' in src
    assert '"TRANSCRIBING": "TRANSCRIPT_PENDING"' in src


def test_냉각은_프로세스_밖에_남는다():
    """전역 변수로 두었더니 워커가 재시작할 때마다 냉각이 풀린 것처럼 되어
    곧바로 차단된 문을 다시 두드렸습니다(로그에 10:04·10:05·10:10 연속).
    화면 쪽은 API 프로세스의 전역을 읽어 값이 아예 없었습니다."""
    import inspect

    from app.collector import transcript as T

    assert not hasattr(T, "_blocked_until"), "전역으로 되돌아가면 안 됩니다"
    src = inspect.getsource(T)
    assert "state.set_time" in src and "COOLDOWN_KEY" in src


def test_요약할_내용이_없으면_AI_를_부르지_않는다():
    """쇼츠를 받기로 하면서 6초짜리 광고까지 들어왔는데, 자막이 20자
    남짓이라 AI 가 요약을 못 내놓고 실패로 남았습니다. 편당 6만 토큰을
    버린 셈입니다. 요약에 성공한 것들의 최소 자막은 207자였습니다."""
    from app.collector.transcript import MIN_SUMMARY_CHARS

    assert 100 <= MIN_SUMMARY_CHARS <= 207


def test_기준은_영상_길이가_아니라_글자_수다():
    """51초짜리가 1,280자로 멀쩡히 요약된 반면 10분짜리가 8자만 나온
    경우도 있습니다. 영상 길이로는 가릴 수 없습니다."""
    import inspect

    from app.collector import transcript

    src = inspect.getsource(transcript.transcribe_pending)
    assert "row.char_count < MIN_SUMMARY_CHARS" in src


def test_언어가_아닌_코드는_위스퍼에_넘기지_않는다():
    """유튜브가 `zxx`("언어적 내용 없음")를 내려보냅니다. 두 글자로 자르면
    `zx` 가 되어 진짜 언어 코드처럼 보이고, 그대로 넘기면
    `ValueError: Unsupported language: zx` 로 받아쓰기 잡이 통째로 죽습니다 —
    영상 한 편 때문에 그 사이클의 나머지까지 멈췄습니다."""
    from app.collector.transcript import _pick_languages

    class V:
        default_language = "zxx"

    assert _pick_languages(V()) == ["ko", "en"], "가짜 코드는 무시하고 기본값으로"

    class V2:
        default_language = "und"

    assert _pick_languages(V2()) == ["ko", "en"]

    # 진짜 언어는 그대로 앞에 섭니다
    class V3:
        default_language = "ja"

    assert _pick_languages(V3())[0] == "ja"


def test_위스퍼가_모르는_언어는_자동_감지로_넘긴다():
    """목록으로 막는 것만으로는 부족합니다 — 우리가 모르는 코드가 새로
    오는 날 또 죽습니다. 위스퍼 자신의 표에 물어봅니다."""
    from app.collector.asr import _whisper_language

    assert _whisper_language("ko") == "ko"
    assert _whisper_language("zx") is None, "모르는 코드는 None (자동 감지)"
    assert _whisper_language("") is None


def test_일시적_다운로드_실패는_탈락이_아니다():
    """403·네트워크로 실패한 36편이 영구 탈락으로 쌓였는데, 나중에 그중
    34편이 그대로 받아졌습니다. 사유에 예외 타입만(`(DownloadError)`)
    적혀 있어서 로그만 봐서는 알 수도 없었습니다.

    요약 쪽에서 두 번 겪은 것과 같은 실수입니다."""
    import inspect

    from app.collector import asr, transcript as T

    src = inspect.getsource(asr._download_audio)
    assert "AudioTemporary" in src, "일시적 실패를 갈라야 합니다"
    assert "_PERMANENT" in src, "영영 안 되는 것만 영구 탈락입니다"
    # 예외 타입만 적으면 원인을 알 수 없습니다
    assert "type(e).__name__" not in src

    fetch = inspect.getsource(T.fetch_via_asr)
    assert "asr.AudioTemporary" in fetch and "TranscriptRetry" in fetch


def test_영영_안_되는_것은_그대로_탈락한다():
    """전부 일시적으로 만들면 멤버십 전용 영상이 큐를 영원히 맴돕니다."""
    from app.collector.asr import _PERMANENT

    for sig in ("members-only", "video is private", "has been removed"):
        assert sig in _PERMANENT


def test_다시_보기에도_상한이_있다():
    """되살리려다 큐를 맴도는 영상을 만들면, 그것 때문에 뒤의 멀쩡한
    것들이 계속 밀립니다."""
    import inspect

    from app.collector import transcript as T

    assert T.MAX_TRANSCRIPT_RETRY >= 2
    src = inspect.getsource(T.transcribe_pending)
    assert "MAX_TRANSCRIPT_RETRY" in src
    assert "_retries(db, video)" in src


@pytest.fixture
def db():
    """시험용 DB. 이 파일의 나머지는 네트워크도 DB 도 안 쓰지만, 아래
    시험만은 **줄 전체가 어떻게 도는지**를 봐야 해서 실제 테이블이 필요합니다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base
    from config.settings import settings

    url = settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def test_오디오가_계속_막히면_줄_전체를_태우지_않는다(db, monkeypatch):
    """**이틀에 129편이 이렇게 죽었습니다.**

    유튜브가 오디오에 403 을 주기 시작하면 다음 영상도 똑같이 403 입니다.
    그런데 예전에는 줄에 있는 20편을 끝까지 두드려서, 한 편도 못 받으면서
    20편의 재시도 횟수만 한 번씩 깎았습니다. 사이클이 30초라 **다섯 번이
    몇 분 만에 소진되고**, 몇 시간이면 풀렸을 차단이 "5번 시도했습니다"
    라는 영구 탈락으로 적혔습니다 — 그 뒤 같은 URL 이 멀쩡히 받아졌습니다.

    셋에서 접고 **오디오 문을 닫아야** 다섯 번이 몇 시간에 걸쳐 쓰입니다.
    """
    from app.collector import asr, transcript as T
    from app.db.models import Video

    for i in range(8):
        db.add(
            Video(
                id=f"vid{i:08d}", title=f"영상{i}", state="TRANSCRIPT_PENDING",
                duration_sec=600, channel_title="채널",
            )
        )
    db.commit()

    monkeypatch.setattr(T.time, "sleep", lambda *_: None)
    # 자막 429 · 오디오 403 — 오늘 실제로 있었던 조합입니다.
    monkeypatch.setattr(T, "fetch", lambda v: (_ for _ in ()).throw(T.Blocked("429")))
    monkeypatch.setattr(
        T, "fetch_via_ytdlp", lambda v: (_ for _ in ()).throw(T.TranscriptUnavailable("없음"))
    )
    monkeypatch.setattr(
        asr, "transcribe",
        lambda *a, **k: (_ for _ in ()).throw(
            asr.AudioTemporary("오디오를 지금 받지 못했습니다 — HTTP Error 403: Forbidden")
        ),
    )

    r = T.transcribe_pending(db, limit=8)

    assert r["attempted"] == T.CONSECUTIVE_TEMP_MAX, (
        f"셋에서 접어야 합니다 — {r['attempted']}편을 두드렸습니다"
    )
    assert r["blocked"] is True and r["failed"] == 0, "일시적 실패는 탈락이 아닙니다"
    assert T.audio_blocked_until(db) is not None, "오디오 문을 닫아야 합니다"

    db.expire_all()
    for i in range(T.CONSECUTIVE_TEMP_MAX, 8):
        assert T._retries(db, db.get(Video, f"vid{i:08d}")) == 0, "손대지 않은 것은 그대로여야"

    # **다음 사이클이 진짜입니다.** 30초 뒤에 다시 와서 같은 403 을 맞으며
    # 나머지의 횟수를 깎던 것이 실제로 벌어진 일입니다.
    before = [T._retries(db, db.get(Video, f"vid{i:08d}")) for i in range(8)]
    again = T.transcribe_pending(db, limit=8)
    db.expire_all()
    after = [T._retries(db, db.get(Video, f"vid{i:08d}")) for i in range(8)]

    assert after == before, "냉각 중에는 재시도 횟수가 깎이면 안 됩니다"
    assert again["failed"] == 0
    assert again.get("deferred", 0) > 0, "손대지 않고 지나쳤다는 표시는 있어야 합니다"
    assert [db.get(Video, f"vid{i:08d}").state for i in range(8)] == ["TRANSCRIPT_PENDING"] * 8


# ── 로그인 쿠키 ────────────────────────────────────────────────


def test_설정이_비어_있으면_쿠키를_쓰지_않는다(monkeypatch):
    """켜지 않은 기능이 조용히 도는 것이 가장 나쁩니다 — 브라우저 쿠키를
    읽는 일은 더더욱 그렇습니다."""
    from app.collector import cookies
    from config.settings import settings

    monkeypatch.setattr(settings, "youtube_cookies_file", "")
    monkeypatch.setattr(settings, "youtube_cookies_browser", "")
    cookies._cache = (0.0, None)

    assert cookies.enabled() is False
    assert cookies.jar() is None
    # 쿠키는 안 붙지만 JS 런타임은 별개입니다 — 그것까지 빠지면 안 됩니다.
    assert "cookiefile" not in cookies.ytdlp_opts()
    assert "cookiesfrombrowser" not in cookies.ytdlp_opts()


def test_JS_런타임이_있으면_알려_준다(monkeypatch):
    """런타임이 없으면 yt-dlp 가 서명 계산이 필요 없는 클라이언트로
    물러서고, 경고대로 **일부 포맷이 빠집니다.** 기본값은 deno 뿐인데
    이 기계에는 node 가 있습니다."""
    from app.collector import cookies

    monkeypatch.setattr(cookies.shutil, "which", lambda n: "/usr/bin/node" if n == "node" else None)
    assert cookies._js_runtime() == {"js_runtimes": {"node": {}}}

    # 하나도 없으면 조용히 빠집니다 — 없는 런타임을 우기면 그때 죽습니다.
    monkeypatch.setattr(cookies.shutil, "which", lambda n: None)
    assert cookies._js_runtime() == {}


def test_파일을_주면_yt_dlp_에_그대로_넘긴다(monkeypatch, tmp_path):
    from app.collector import cookies
    from config.settings import settings

    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(settings, "youtube_cookies_file", str(f))
    monkeypatch.setattr(settings, "youtube_cookies_browser", "")

    assert cookies.enabled() is True
    assert cookies.ytdlp_opts()["cookiefile"] == str(f)


def test_없는_파일이면_쿠키_없이_계속_돈다(monkeypatch, tmp_path):
    """**여기서 예외를 내면 안 됩니다.** 쿠키 설정 오타 하나로 수집이
    통째로 멎으면, 붙이기 전보다 나빠집니다."""
    from app.collector import cookies
    from config.settings import settings

    monkeypatch.setattr(settings, "youtube_cookies_file", str(tmp_path / "없는파일.txt"))
    monkeypatch.setattr(settings, "youtube_cookies_browser", "")
    cookies._cache = (0.0, None)
    cookies._warned = False

    assert "cookiefile" not in cookies.ytdlp_opts(), "없는 파일을 넘기면 yt-dlp 가 죽습니다"
    assert cookies.jar() is None


def test_세_경로가_같은_쿠키를_본다():
    """하나만 로그인 상태면 어느 경로가 왜 되는지 설명할 수 없습니다."""
    import inspect

    from app.collector import asr, transcript as T

    assert "cookies.ytdlp_opts()" in inspect.getsource(asr._download_audio), "오디오"
    assert "cookies.ytdlp_opts()" in inspect.getsource(T.fetch_via_ytdlp), "yt-dlp 자막"
    assert "cookies.jar()" in inspect.getsource(T.fetch), "1차 자막 경로"


def test_브라우저에_유튜브_쿠키가_없으면_알려_준다(monkeypatch):
    """크롬이 실행 중이거나 키체인이 막히면 **예외 없이 0개**가 나옵니다.
    그대로 두면 "쿠키를 붙였는데 왜 그대로냐" 로 남습니다."""
    from app.collector import cookies
    from config.settings import settings

    monkeypatch.setattr(settings, "youtube_cookies_file", "")
    monkeypatch.setattr(settings, "youtube_cookies_browser", "chrome")
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", lambda *a, **k: [])
    cookies._cache = (0.0, None)
    cookies._warned = False

    warned = []
    monkeypatch.setattr(cookies.logger, "warning", lambda msg, *a: warned.append(msg % a))

    assert cookies.jar() is None
    assert warned and "유튜브 쿠키를 찾지 못했습니다" in warned[0]


def test_자막까지_막히면_자막_문도_바로_닫는다(db, monkeypatch):
    """**받아쓰기가 닫혀 있으면 자막 429 는 그걸로 끝입니다.**

    그런데 자막 문을 열어 둔 채로 두면 30초마다 다시 와서 영상마다 세 번씩
    (5초·10초 쉬며) 두드립니다. 차단이 풀릴 이유가 없고 오히려 길어집니다 —
    실제로 그렇게 돌고 있었습니다. 한 사이클에 한 번 확인했으면 닫습니다.
    """
    from app.collector import transcript as T
    from app.db.models import Video
    from config.time import now_kst
    from datetime import timedelta

    for i in range(5):
        db.add(
            Video(
                id=f"cap{i:08d}", title=f"영상{i}", state="TRANSCRIPT_PENDING",
                duration_sec=600, channel_title="채널",
            )
        )
    db.commit()

    # 오디오는 이미 쉬는 중, 자막은 열려 있는 상태 — 지금 곳간이 그랬습니다.
    from app.db import state

    state.set_time(db, T.AUDIO_COOLDOWN_KEY, now_kst() + timedelta(hours=3))
    state.set_time(db, T.COOLDOWN_KEY, None)

    monkeypatch.setattr(T.time, "sleep", lambda *_: None)
    두드린횟수 = {"n": 0}

    def 막힘(v):
        두드린횟수["n"] += 1
        raise T.Blocked("429")

    monkeypatch.setattr(T, "fetch", 막힘)
    monkeypatch.setattr(
        T, "fetch_via_ytdlp", lambda v: (_ for _ in ()).throw(T.TranscriptUnavailable("없음"))
    )

    r = T.transcribe_pending(db, limit=5)

    assert T.blocked_until(db) is not None, "자막 문이 닫혀야 합니다"
    assert r["blocked"] is True
    assert 두드린횟수["n"] <= T.MAX_RETRY * T.CONSECUTIVE_TEMP_MAX, (
        f"셋에서 접어야 합니다 — {두드린횟수['n']}번 두드렸습니다"
    )

    # 다음 사이클은 두 문이 다 닫혀 있으니 아예 손대지 않습니다.
    두드린횟수["n"] = 0
    again = T.transcribe_pending(db, limit=5)
    assert 두드린횟수["n"] == 0, "닫아 놓고 또 두드리면 닫은 의미가 없습니다"
    assert again["attempted"] == 0
    db.expire_all()
    assert [db.get(Video, f"cap{i:08d}").state for i in range(5)] == ["TRANSCRIPT_PENDING"] * 5


# ── 자막이 아예 없는 영상 ──────────────────────────────────────


def test_자막이_없으면_받아쓰기로_넘어간다(monkeypatch):
    """**받아쓰기가 가장 필요한 경우가 바로 이것입니다.**

    그런데 `_fetch_with_retry` 가 `except Blocked` 만 잡고 있어서, 자막이
    없다는 예외(`TranscriptUnavailable`)는 그대로 새어 나가 그 자리에서
    탈락했습니다 — "자막 경로가 전부 막혔을 때만 받아쓰기" 라고 적어 둔
    설계와 달리, 받아쓰기는 **429 일 때만** 돌고 있었습니다.
    """
    from app.collector import transcript as T

    호출 = {"asr": 0}

    monkeypatch.setattr(
        T, "fetch", lambda v: (_ for _ in ()).throw(
            T.TranscriptUnavailable("자막이 제공되지 않는 영상입니다.")
        ),
    )
    monkeypatch.setattr(
        T, "fetch_via_ytdlp", lambda v: (_ for _ in ()).throw(T.TranscriptUnavailable("없음"))
    )

    def 받아쓰기(v):
        호출["asr"] += 1
        return T.Fetched(source=T.LOCAL_ASR, language="ko", segments=[{"start": 0, "dur": 1, "text": "말"}])

    monkeypatch.setattr(T, "fetch_via_asr", 받아쓰기)
    monkeypatch.setattr(T.time, "sleep", lambda *_: None)

    class V:
        id, duration_sec, default_language, title = "v", 600, "ko", "t"

    got = T._fetch_with_retry(V())
    assert got.source == T.LOCAL_ASR
    assert 호출["asr"] == 1, "자막이 없으면 소리로 받아야 합니다"


def test_없는_자막을_다시_묻지_않는다(monkeypatch):
    """없는 자막은 5초 뒤에도 없습니다. 차단(429)과 달리 기다릴 이유가
    없고, 기다리는 만큼 줄이 밀립니다."""
    from app.collector import transcript as T

    물어본횟수 = {"n": 0}

    def 없음(v):
        물어본횟수["n"] += 1
        raise T.TranscriptUnavailable("자막이 제공되지 않는 영상입니다.")

    monkeypatch.setattr(T, "fetch", 없음)
    monkeypatch.setattr(
        T, "fetch_via_ytdlp", lambda v: (_ for _ in ()).throw(T.TranscriptUnavailable("없음"))
    )
    monkeypatch.setattr(T, "fetch_via_asr", lambda v: T.Fetched(T.LOCAL_ASR, "ko", [{"start": 0, "dur": 1, "text": "말"}]))
    monkeypatch.setattr(T.time, "sleep", lambda *_: (_ for _ in ()).throw(AssertionError("쉬면 안 됩니다")))

    class V:
        id, duration_sec, default_language, title = "v", 600, "ko", "t"

    T._fetch_with_retry(V())
    assert 물어본횟수["n"] == 1, f"한 번만 물어야 합니다 — {물어본횟수['n']}번 물었습니다"


def test_영상_자체가_없으면_소리도_받으러_가지_않는다(monkeypatch):
    """자막만 없으면 받아쓰면 되지만, 영상이 없으면 받을 소리도 없습니다.
    뭉뚱그리면 지워진 영상마다 오디오를 받으러 갔다가 실패하는 데 몇 분씩
    씁니다."""
    import pytest

    from app.collector import transcript as T

    monkeypatch.setattr(
        T, "fetch", lambda v: (_ for _ in ()).throw(T.VideoGone("영상을 볼 수 없습니다(비공개·삭제)."))
    )
    monkeypatch.setattr(
        T, "fetch_via_asr", lambda v: (_ for _ in ()).throw(AssertionError("가면 안 됩니다"))
    )

    class V:
        id, duration_sec, default_language, title = "v", 600, "ko", "t"

    with pytest.raises(T.VideoGone):
        T._fetch_with_retry(V())


def test_자막이_살아_있으면_문을_닫지_않는다(db, monkeypatch):
    """**실제로 있었던 일입니다.** 같은 사이클에서 자막 3건을 성공해 놓고,
    네 번째 영상의 429 하나로 문을 한 시간 닫았습니다. 대기 55편이 그대로
    멎었습니다.

    429 는 통째 차단일 때도 나지만 잠깐의 속도 제한으로도 납니다 — 한 번만
    보고는 둘을 못 가립니다. 한 건이라도 받아졌으면 자막 경로는 살아 있는
    것이고, 살아 있는 문을 닫으면 안 됩니다.
    """
    from datetime import timedelta

    from app.collector import transcript as T
    from app.db import state
    from app.db.models import Video
    from config.time import now_kst

    for i in range(5):
        db.add(
            Video(
                id=f"mix{i:08d}", title=f"영상{i}", state="TRANSCRIPT_PENDING",
                duration_sec=600, channel_title="채널",
            )
        )
    db.commit()

    state.set_time(db, T.AUDIO_COOLDOWN_KEY, now_kst() + timedelta(hours=3))
    state.set_time(db, T.COOLDOWN_KEY, None)
    monkeypatch.setattr(T.time, "sleep", lambda *_: None)

    # 하나 걸러 하나씩 막힙니다 — 연속이 아니므로 문은 열려 있어야 합니다.
    #
    # **영상 단위로 갈라야 합니다.** 호출 횟수로 번갈아 두면 막힌 영상이
    # 재시도(3회) 중에 성공해 버려서, 정작 보려던 자리(Deferred)에 한 번도
    # 닿지 않습니다 — 처음에 그렇게 써서 시험이 아무것도 안 잡았습니다.
    def fetch(v):
        if int(v.id[-1]) % 2 == 0:
            raise T.Blocked("429")
        return T.Fetched(source="youtube_auto", language="ko",
                         segments=[{"start": 0, "dur": 1, "text": "말" * 300}])

    monkeypatch.setattr(T, "fetch", fetch)
    monkeypatch.setattr(
        T, "fetch_via_ytdlp", lambda v: (_ for _ in ()).throw(T.TranscriptUnavailable("없음"))
    )

    r = T.transcribe_pending(db, limit=5)

    assert r["ok"] == 2, "받아지는 것은 받아야 합니다"
    assert T.blocked_until(db) is None, "자막이 살아 있는데 문을 닫으면 안 됩니다"
