"""due 판정 — 크론 대신 키워드가 자기 차례를 안다.

여기가 틀리면 조용히 안 돌거나 하루에 여러 번 돕니다. 둘 다 한참 뒤에야
알아차리게 되는 종류라 테스트로 못박아 둡니다.
"""

from datetime import datetime

import pytest

from app.collector.schedule import INTERVAL_DAYS, is_due, next_due_at
from app.db.models import Keyword

NOW = datetime(2026, 8, 1, 10, 0)  # 화요일 오전 10시


def kw(**over) -> Keyword:
    base = dict(
        id="kw_t", term="테스트", status="active", language="ko", schedule="daily",
        min_duration_sec=1200, max_duration_sec=14400, min_expert_score=75,
        max_per_run=10, run_hour=4,
    )
    base.update(over)
    return Keyword(**base)


def test_등록_직후는_기다리지_않는다():
    assert is_due(kw(status="pending", last_run_at=None), NOW)
    assert next_due_at(kw(status="pending", last_run_at=None)) is None


def test_한_번도_안_돌았으면_즉시():
    assert is_due(kw(status="active", last_run_at=None), NOW)


def test_오늘_이미_돌았으면_내일_run_hour():
    k = kw(last_run_at=datetime(2026, 8, 1, 4, 2))
    assert next_due_at(k) == datetime(2026, 8, 2, 4, 0)
    assert not is_due(k, NOW)


def test_어제_돌았고_오늘_시각을_지났으면_due():
    k = kw(last_run_at=datetime(2026, 7, 31, 4, 5))
    assert is_due(k, NOW)


def test_어제_돌았지만_아직_시각_전이면_아직():
    k = kw(last_run_at=datetime(2026, 7, 31, 4, 5))
    assert not is_due(k, datetime(2026, 8, 1, 3, 30))


@pytest.mark.parametrize("schedule,days", list(INTERVAL_DAYS.items()))
def test_주기별_간격(schedule, days):
    k = kw(schedule=schedule, last_run_at=datetime(2026, 8, 1, 4, 0))
    assert next_due_at(k) == datetime(2026, 8, 1 + days, 4, 0)


def test_키워드마다_다른_시각():
    k = kw(run_hour=12, last_run_at=datetime(2026, 7, 31, 12, 0))
    assert not is_due(k, datetime(2026, 8, 1, 11, 59))
    assert is_due(k, datetime(2026, 8, 1, 12, 0))


def test_시각이_밀리지_않는다():
    """last_run_at + 24시간으로 하면 실행에 걸린 시간만큼 매일 뒤로 밀립니다.
    절대 시각으로 잡으면 몇 시에 끝났든 다음 차례는 같습니다."""
    early = kw(last_run_at=datetime(2026, 8, 1, 4, 0))
    late = kw(last_run_at=datetime(2026, 8, 1, 4, 47))  # 47분 걸린 실행
    assert next_due_at(early) == next_due_at(late) == datetime(2026, 8, 2, 4, 0)


@pytest.mark.parametrize("status", ["paused", "quota_wait", "archived"])
def test_멈춘_키워드는_대상이_아니다(status):
    assert not is_due(kw(status=status, last_run_at=None), NOW)


def test_run_hour_범위를_벗어나도_깨지지_않는다():
    k = kw(run_hour=99, last_run_at=datetime(2026, 7, 31, 4, 0))
    assert next_due_at(k) == datetime(2026, 8, 1, 23, 0)
