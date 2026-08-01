"""룰 필터 검증 — API 키 없이 돌아갑니다.

필터는 비용을 가장 크게 좌우하는 곳이라, 기준을 바꿀 때마다 무엇이
떨어지고 무엇이 통과하는지 여기서 확인합니다.
"""

from datetime import timedelta

import pytest

from app.collector.rules import evaluate
from app.collector.youtube import Candidate, duration_bucket, parse_duration
from app.db.models import Keyword
from config.settings import settings
from config.time import now_kst


def make_candidate(**over) -> Candidate:
    base = dict(
        video_id="abc12345678",
        title="쿠버네티스 CNI 플러그인 심층 분석",
        description="",
        channel_id="ch1",
        channel_title="인프라 노트",
        published_at=now_kst() - timedelta(days=100),
        duration_sec=4360,
        view_count=12_000,
        like_count=300,
        comment_count=20,
        thumbnail_url=None,
        default_language="ko",
        has_caption=True,
        search_rank=0,
    )
    base.update(over)
    return Candidate(**base)


def make_keyword(**over) -> Keyword:
    base = dict(
        id="kw_t",
        term="쿠버네티스 네트워킹",
        status="active",
        language="ko",
        schedule="daily",
        min_duration_sec=1200,
        max_duration_sec=14400,
        min_expert_score=75,
        max_per_run=10,
    )
    base.update(over)
    return Keyword(**base)


def test_통과하는_후보():
    assert evaluate(make_candidate(), make_keyword()).ok


@pytest.mark.parametrize(
    "over,expect",
    [
        ({"duration_sec": 900}, "길이 미달"),
        ({"duration_sec": 20000}, "길이 초과"),
        ({"published_at": now_kst() - timedelta(days=1000)}, "오래됨"),
        ({"title": "무료특강 신청하세요"}, "홍보성 제목"),
        ({"default_language": "en"}, "언어 불일치"),
    ],
)
def test_탈락_사유(over, expect):
    v = evaluate(make_candidate(**over), make_keyword())
    assert not v.ok
    assert expect in v.reason
    # 사유에 실제 값이 들어가야 기준 조정 판단이 됩니다
    assert v.reason != expect


def test_언어를_모르면_통과시킨다():
    """유튜브가 언어를 안 주는 경우가 흔합니다. 모른다고 떨어뜨리면
    한국어 강의도 같이 떨어집니다."""
    assert evaluate(make_candidate(default_language=None), make_keyword()).ok


def test_키워드_설정이_전역값보다_우선():
    kw = make_keyword(min_duration_sec=3600)
    assert not evaluate(make_candidate(duration_sec=1800), kw).ok
    assert evaluate(make_candidate(duration_sec=1800), make_keyword()).ok


def test_컨퍼런스_세션이_통과한다():
    """M2 실측에서 나온 사례. 조회수 300~400 대인 한국 기술 컨퍼런스
    발표가 예전 기준(1,000)에 통째로 걸렸습니다."""
    c = make_candidate(title="[CNKCD2025] Cilium과 Istio Ambient 모드", view_count=380)
    assert evaluate(c, make_keyword()).ok


@pytest.mark.parametrize(
    "iso,sec",
    [
        ("PT1H34M5S", 5645),
        ("PT45M", 2700),
        ("PT58S", 58),
        ("P1DT2H", 93600),
        ("", 0),
    ],
)
def test_기간_파싱(iso, sec):
    assert parse_duration(iso) == sec


@pytest.mark.parametrize(
    "min_sec,bucket",
    [(0, "any"), (240, "any"), (900, "any"), (1200, "long"), (3600, "long")],
)
def test_검색_길이_구간(min_sec, bucket):
    """유튜브는 short(4분 미만)·medium(4~20분)·long(20분 초과) 세 칸뿐입니다.

    **medium 은 절대 쓰지 않습니다** — 20분에서 잘려 긴 강의가 통째로
    사라집니다. 실제로 그렇게 넣었다가 50건 전부 탈락했습니다.

    **20분 미만을 원하면 조건을 아예 안 겁니다.** long 을 걸면 5~20분대가
    검색 단계에서 사라지는데, 우리가 직접 거르는 편이 정확합니다.
    """
    assert duration_bucket(min_sec) == bucket
    assert duration_bucket(min_sec) != "medium"


def test_조회수는_기본으로_보지_않는다():
    """300회로 두었더니 룰 탈락 330건 중 157건이 여기서 걸렸고, 걸린 것들이
    하필 틈새 전문 콘텐츠였습니다(287회 24분짜리 인프라 해설 등). 틈새
    강의는 정의상 조회수가 적어서, 품질 신호로 쓰면 목적과 반대로 걸립니다."""
    assert settings.rule_min_view_count == 0
    assert evaluate(make_candidate(view_count=3), make_keyword()).ok


def test_조회수_기준을_켜면_다시_걸린다(monkeypatch):
    """끈 것이지 없앤 것이 아닙니다 — .env 로 되돌릴 수 있어야 합니다."""
    monkeypatch.setattr(settings, "rule_min_view_count", 300)
    v = evaluate(make_candidate(view_count=120), make_keyword())
    assert not v.ok and "조회수 미달" in v.reason


def test_언어_무관이면_영어도_통과한다():
    """룰 탈락 124건이 전부 영어였습니다(en·en-US·en-GB·en-IN·en-CA).
    요약은 어차피 한국어로 쓰므로, 원하면 받을 수 있어야 합니다."""
    kw = make_keyword(language="any")
    assert evaluate(make_candidate(default_language="en-US"), kw).ok
