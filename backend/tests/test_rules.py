"""룰 필터 검증 — API 키 없이 돌아갑니다.

필터는 비용을 가장 크게 좌우하는 곳이라, 기준을 바꿀 때마다 무엇이
떨어지고 무엇이 통과하는지 여기서 확인합니다.
"""

from datetime import timedelta

import pytest

from app.collector.rules import evaluate
from app.collector.youtube import Candidate, duration_bucket, parse_duration
from app.db.models import Keyword
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
        min_duration_sec=900,
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
        ({"duration_sec": 720}, "길이 미달"),
        ({"duration_sec": 20000}, "길이 초과"),
        ({"view_count": 300}, "조회수 미달"),
        ({"published_at": now_kst() - timedelta(days=1000)}, "오래됨"),
        ({"title": "무료특강 신청하세요"}, "홍보성 제목"),
        ({"title": "쿠버네티스 쇼츠 모음"}, "홍보성 제목"),
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


@pytest.mark.parametrize("min_sec,bucket", [(0, "any"), (240, "long"), (900, "long"), (3600, "long")])
def test_검색_길이_구간(min_sec, bucket):
    """medium 을 쓰면 안 됩니다 — 20분에서 잘려 긴 강의가 통째로 사라집니다.
    실제로 그렇게 넣었다가 50건 전부 탈락했습니다."""
    assert duration_bucket(min_sec) == bucket
    assert duration_bucket(min_sec) != "medium"
