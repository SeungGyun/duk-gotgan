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


# ── 차단 냉각 중의 사이클 판단 ──────────────────────────────
#
# 실제로 있었던 일: 대기 28건이 남은 채 유튜브가 막히자, 워커가 1분마다
# "할 일 있음"으로 판단해 실행 기록을 만들고 곧바로 "차단으로 쉬는 중"으로
# 실패 처리했습니다. 6분 만에 실패 6줄이 쌓였습니다. 냉각은 실패가 아닙니다.


def test_냉각_중에는_자막_대기를_할_일로_세지_않는다(monkeypatch):
    from datetime import timedelta

    from app.collector import cycle
    from app.collector import transcript as T
    from config.time import now_kst

    monkeypatch.setattr(T, "_blocked_until", now_kst() + timedelta(minutes=30))
    assert cycle.workable_states() == ["TRANSCRIBED"]


def test_냉각이_풀리면_다시_집어간다(monkeypatch):
    from datetime import timedelta

    from app.collector import cycle
    from app.collector import transcript as T
    from config.time import now_kst

    monkeypatch.setattr(T, "_blocked_until", now_kst() - timedelta(minutes=1))
    assert "TRANSCRIPT_PENDING" in cycle.workable_states()


def test_차단은_실패가_아니라_보류로_쌓인다():
    """상태 판정의 근거가 되는 자리를 갈라 둡니다 — notes 는 실패, paused 는 대기."""
    from app.collector.cycle import CycleResult

    r = CycleResult()
    r.paused.append("유튜브 차단으로 자막 수집을 쉬는 중입니다")
    assert not r.notes  # 실패로 새지 않아야 합니다


def test_연속_차단이면_대기가_배로_늘어난다():
    """실측: 60분 고정으로 5시간 동안 매시간 두드려 전부 429 를 받았습니다.
    풀리지 않는 차단에 규칙적으로 노크하면 차단만 갱신됩니다."""
    from app.collector.transcript import cooldown_minutes

    assert [cooldown_minutes(n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert cooldown_minutes(9) == 480  # 8시간에서 멈춥니다
