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


def test_지나간_시각은_쉬는_중이_아니다(db):
    pace.back_off(db, "claude", "쿼터")
    from app.db import state

    state.set_time(db, "review.resume_at:claude", now_kst() - timedelta(minutes=1))
    assert pace.resume_at(db, "claude") is None


def test_상한을_넘은_것은_시각을_적어_두지_않는다(db):
    """**여기가 놓쳤던 부분입니다.** 처음엔 "창이 바뀌는 16:00 까지 쉰다"고
    적어 두었습니다. 그런데 상한은 화면에서 언제든 올릴 수 있어서, 13:45 에
    올려도 적어 둔 16:00 이 그대로 남아 두 시간을 놀았습니다.

    적어 둔 것은 결정의 캐시인데 그 입력(상한·사용량)이 바뀌는 값이었습니다."""
    pace.mark_capped(db, "claude", "상한 초과")
    assert pace.capped(db, "claude") is True
    # 타이머는 걸리지 않습니다 — 다음 틱에 장부를 다시 봅니다
    assert pace.resume_at(db, "claude") is None

    pace.clear_capped(db, "claude")
    assert pace.capped(db, "claude") is False


def test_상한을_넘었다는_말은_한_번만(db, caplog):
    """매 틱 같은 줄을 찍으면 실행 로그가 그것으로 덮입니다."""
    with caplog.at_level("INFO"):
        pace.mark_capped(db, "claude", "상한 초과")
        first = len(caplog.records)
        pace.mark_capped(db, "claude", "상한 초과")
        assert len(caplog.records) == first


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


# ── 부르는 쪽 ────────────────────────────────────────────────


def test_막혔으면_실행_기록을_만들지_않는다():
    """예전에는 상한에 닿은 뒤에도 매 틱 실행 기록이 하나씩 생겼습니다.
    아무것도 안 했는데요 — 정작 무슨 일이 있었는지 보려는 화면이 같은
    줄로 덮였습니다. `review_due` 가 먼저 걸러 내야 합니다."""
    from app.collector import jobs

    due = inspect.getsource(jobs.review_due)
    assert "pace.resume_at(" in due, "회사가 안 받는 중인지 봐야 합니다"
    assert "usage.check(db)" in due, "우리 상한도 여기서 봐야 합니다"

    # 실행 기록을 만드는 곳에는 상한 판단이 남아 있으면 안 됩니다 —
    # 두 군데서 보면 한쪽만 고쳤을 때 조용히 어긋납니다.
    assert "usage.check(db)" not in inspect.getsource(jobs.review_job)


def test_상한은_매번_장부에서_다시_본다():
    """**상한을 올리면 다음 틱에 바로 재개돼야 합니다.** 시각을 적어 두면
    그 사이 상한을 올려도 적어 둔 시각이 남아 계속 놉니다 — 실제로 그렇게
    설계했다가 두 시간을 놀 뻔했습니다."""
    from app.collector import jobs
    from app.llm import pace, runner

    # 상한 때문에 멈출 때 타이머를 거는 코드가 없어야 합니다
    assert not hasattr(pace, "rest_until"), "상한에는 타이머를 두지 않습니다"
    for src in (inspect.getsource(jobs.review_due), inspect.getsource(runner.review_pending)):
        assert "window_end()" not in src

    due = inspect.getsource(jobs.review_due)
    assert "pace.mark_capped(" in due and "pace.clear_capped(" in due


def test_회사가_안_받으면_점점_길게_쉰다():
    from app.llm import runner

    src = inspect.getsource(runner.review_pending)
    assert "pace.back_off(" in src
    assert "pace.clear(" in src, "잘 되면 누적을 지워야 합니다"


def test_상한을_올리면_곧바로_재개된다(db):
    """**사용자가 짚어 준 구멍입니다.**

    "지금 대시보드에서 상한을 늘렸어. 이러면 요약이 재개되어야 할 것 같은데."

    맞습니다. 예전 설계는 막힌 순간 "창이 바뀌는 16:00 까지 쉰다"고 시각을
    적어 두어서, 13:45 에 상한을 올려도 두 시간을 그대로 놀았습니다.
    """
    from app.collector import jobs
    from app.db.models import Keyword, UsageWindow, Video, VideoKeyword
    from app.llm import usage

    # 요약 대기 한 편과, 상한을 이미 넘긴 사용량을 만듭니다
    k = Keyword(term="테스트", status="active")
    db.add(k)
    db.flush()
    db.add(Video(id="vid_pace", title="t", state="TRANSCRIBED", channel_title="c"))
    db.flush()
    db.add(VideoKeyword(video_id="vid_pace", keyword_id=k.id))
    db.add(
        UsageWindow(
            start=usage.window_start(), provider=settings.review_provider,
            input_tokens=900, output_tokens=100,
        )
    )
    usage.set_limit(db, 500, settings.review_provider)
    db.commit()

    go, waiting, why = jobs.review_due(db)
    assert go is False and "상한" in why, (go, why)
    assert pace.capped(db, settings.review_provider) is True

    # 주인이 화면에서 상한을 올립니다
    usage.set_limit(db, 5_000, settings.review_provider)

    go, waiting, why = jobs.review_due(db)
    assert go is True, f"상한을 올렸으면 곧바로 재개돼야 합니다 — {why}"
    assert pace.capped(db, settings.review_provider) is False
