"""자막 전처리 — 네트워크 없이 돕니다.

병합은 토큰 수에 직접 영향을 줍니다. 원본 2~3초 세그먼트를 그대로 쓰면
타임스탬프 줄만 수천 개가 되고, 그 자체가 입력 토큰입니다.
"""

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
