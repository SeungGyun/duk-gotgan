"""막혔을 때 쉬는 자리.

실행 로그가 이렇게 덮여 있었습니다 — 아무것도 안 하면서요.

    10:56:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
    10:57:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
    10:58:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
"""

import inspect
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.llm import pace
from config.settings import settings
from config.time import now_kst


@pytest.fixture
def db():
    url = settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    engine.dispose()


def test_쉬는_중이_아니면_None(db):
    assert pace.resume_at(db, "claude") is None


def test_아는_시각까지는_그대로_쉰다(db):
    """창의 토큰을 다 썼을 때입니다. 창이 5시간 단위로 딱 떨어져서
    어림잡을 이유가 없습니다."""
    until = now_kst() + timedelta(hours=2)
    pace.rest_until(db, "claude", until, "상한")
    at = pace.resume_at(db, "claude")
    assert at is not None and abs((at - until).total_seconds()) < 2


def test_지나간_시각은_쉬는_중이_아니다(db):
    pace.rest_until(db, "claude", now_kst() - timedelta(minutes=1), "지난 것")
    assert pace.resume_at(db, "claude") is None


def test_모르면_점점_길게_쉰다(db):
    """쿼터가 떨어진 상대에게 1분마다 두드리는 것은 풀리는 데 도움이 안
    되고, 상대가 더 세게 막는 빌미가 됩니다."""
    waits = []
    for _ in range(len(pace.BACKOFF_MIN) + 2):
        until = pace.back_off(db, "antigravity", "쿼터")
        waits.append(round((until - now_kst()).total_seconds() / 60))
    assert waits[:4] == [1, 2, 4, 8], waits
    # 끝에서 멈춥니다 — 더 늘리면 풀린 뒤에도 반 시간씩 놉니다
    assert max(waits) == pace.BACKOFF_MIN[-1]
    assert waits[-1] == pace.BACKOFF_MIN[-1]


def test_한_건이라도_되면_처음으로(db):
    """누적을 안 지우면 어제 몇 번 막혔던 것 때문에 오늘 첫 실패가
    곧바로 30분짜리가 됩니다."""
    for _ in range(4):
        pace.back_off(db, "antigravity", "쿼터")
    pace.clear(db, "antigravity")
    assert pace.resume_at(db, "antigravity") is None

    until = pace.back_off(db, "antigravity", "다시")
    assert round((until - now_kst()).total_seconds() / 60) == pace.BACKOFF_MIN[0]


def test_회사끼리_따로_쉰다(db):
    """한쪽이 쉬는 동안 다른 쪽은 그대로 돌아야 합니다 — 같이 재우면
    회사를 나눈 의미가 없습니다."""
    pace.back_off(db, "antigravity", "쿼터")
    assert pace.resume_at(db, "antigravity") is not None
    assert pace.resume_at(db, "claude") is None


def test_같은_시각으로_다시_세우면_조용히_넘긴다(db, caplog):
    """안 그러면 1분마다 "쉽니다" 한 줄씩 쌓여, 줄이려던 소음이 문구만
    바뀐 채 그대로 남습니다."""
    until = now_kst() + timedelta(hours=1)
    with caplog.at_level("INFO"):
        pace.rest_until(db, "claude", until, "상한")
        first = len(caplog.records)
        pace.rest_until(db, "claude", until, "상한")
        assert len(caplog.records) == first, "같은 휴식을 두 번 알리면 안 됩니다"


# ── 부르는 쪽 ────────────────────────────────────────────────


def test_쉬는_중이면_실행_기록을_만들지_않는다():
    """예전에는 상한에 닿은 뒤에도 매 틱 실행 기록이 하나씩 생겼습니다.
    아무것도 안 했는데요 — 정작 무슨 일이 있었는지 보려는 화면이 같은
    줄로 덮였습니다."""
    from app.collector import jobs

    due = inspect.getsource(jobs.review_due)
    assert "pace.resume_at(" in due, "돌릴지 정할 때 쉬는 중인지 봐야 합니다"

    job = inspect.getsource(jobs.review_job)
    # 상한 확인이 `_start`(실행 기록 생성)보다 앞에 있어야 합니다
    assert "usage.check(db)" in job
    assert job.index("usage.check(db)") < job.index("_start(db,")


def test_상한은_창이_바뀔_때까지_쉰다():
    """언제 풀리는지 정확히 아는 경우입니다 — 어림잡을 이유가 없습니다."""
    from app.collector import jobs

    assert "pace.rest_until(" in inspect.getsource(jobs.review_job)
    assert "usage.window_end()" in inspect.getsource(jobs.review_job)


def test_회사가_안_받으면_점점_길게_쉰다():
    from app.llm import runner

    src = inspect.getsource(runner.review_pending)
    assert "pace.back_off(" in src
    assert "pace.clear(" in src, "잘 되면 누적을 지워야 합니다"
