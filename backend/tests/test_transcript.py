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
