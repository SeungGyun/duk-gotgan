"""사용량 가드 — 구독 인증이라 **비용 가드가 아닙니다**.

청구액은 정액이고 실제 병목은 구독의 사용량 한도입니다. 그래서 돈이 아니라
**토큰을 셉니다.** `total_cost_usd` 도 기록하지만 청구액이 아니라 사용량
프록시일 뿐이고, 화면에 "비용"이라고 쓰지 않습니다 (SPEC §8.1).
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import state
from app.db.models import UsageLedger, UsageWindow
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)


class UsageExceeded(Exception):
    def __init__(self, used: int, limit: int, resets_at: datetime):
        self.used, self.limit, self.resets_at = used, limit, resets_at
        super().__init__(
            f"이번 창의 토큰 상한에 닿았습니다 — {used:,}/{limit:,}. "
            f"진행 중인 건만 마치고 {resets_at:%H:%M} 에 재개합니다."
        )


# 창을 자르는 기준점. 자정에 맞추면 24가 5로 나눠떨어지지 않아 마지막
# 칸만 짧아집니다. 고정 기준점에서 일정하게 끊으면 그런 일이 없습니다.
_ANCHOR = datetime(2026, 1, 1)

# 상한은 **화면에서 바꿉니다.** .env 를 고치고 프로세스를 재시작해야
# 한다면, 쓰다가 "조금만 올려 보자"를 할 수 없습니다.
LIMIT_KEY = "usage.token_limit_per_window"


def limit(db: Session, provider: str | None = None) -> int:
    """이번 창의 토큰 상한. 0 이면 무제한.

    회사별로 따로 걸 수 있습니다 — `usage.token_limit_per_window:antigravity`
    같은 키가 있으면 그쪽이 우선합니다. 없으면 공용 값을 씁니다. 화면에서
    바꾸는 것은 공용 값이라, 회사를 늘리기 전에는 예전과 똑같이 돕니다.
    """
    if provider:
        per = state.get_int(db, f"{LIMIT_KEY}:{provider}")
        if per is not None:
            return per
    stored = state.get_int(db, LIMIT_KEY)
    return settings.token_limit_per_window if stored is None else stored


def set_limit(db: Session, value: int | None, provider: str | None = None) -> None:
    state.set_int(db, f"{LIMIT_KEY}:{provider}" if provider else LIMIT_KEY, value)


def window_start(at: datetime | None = None) -> datetime:
    at = at or now_kst()
    hours = int((at - _ANCHOR).total_seconds() // 3600)
    span = settings.token_window_hours
    return _ANCHOR + timedelta(hours=(hours // span) * span)


def window_end(at: datetime | None = None) -> datetime:
    return window_start(at) + timedelta(hours=settings.token_window_hours)


def _window(db: Session, provider: str) -> UsageWindow:
    start = window_start()
    row = db.scalar(
        select(UsageWindow)
        .where(UsageWindow.start == start, UsageWindow.provider == provider)
        .with_for_update()
    )
    if row is None:
        row = UsageWindow(start=start, provider=provider)
        db.add(row)
        db.flush()
    return row


def window_totals(db: Session) -> tuple[int, int]:
    """이번 창에 **회사를 다 합쳐** 얼마나 썼나. 화면 미터가 쓰는 값입니다."""
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageWindow.input_tokens), 0),
            func.coalesce(func.sum(UsageWindow.output_tokens), 0),
        ).where(UsageWindow.start == window_start())
    ).one()
    return int(row[0]), int(row[1])


def _today(db: Session, day: date | None = None) -> UsageLedger:
    day = day or now_kst().date()
    row = db.scalar(select(UsageLedger).where(UsageLedger.day == day).with_for_update())
    if row is None:
        row = UsageLedger(day=day)
        db.add(row)
        db.flush()
    return row


def check(db: Session, est_tokens: int = 0, provider: str | None = None) -> None:
    """호출 전에 확인합니다. 상한이 0 이면 무제한으로 봅니다.

    **회사별로 셉니다.** 합쳐서 세면 한쪽이 많이 쓴 것 때문에 아직 여유가
    있는 쪽까지 멈춥니다 — 토큰이 모자라서 회사를 늘렸는데 정반대가 됩니다.
    """
    provider = provider or settings.review_provider
    cap = limit(db, provider)
    if not cap:
        return
    row = _window(db, provider)
    used = row.input_tokens + row.output_tokens
    if used + est_tokens > cap:
        raise UsageExceeded(used, cap, window_end())


def record(
    db: Session,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float = 0.0,
    early_exit: bool = False,
    saved_input_tokens: int = 0,
    provider: str | None = None,
) -> None:
    """호출 1건의 사용량을 장부에 더합니다.

    `early_exit` 은 **조기 종료가 실제로 작동하는지**를 추적하는 값입니다.
    통합 호출 구조의 비용 이점이 전적으로 여기 달려 있어서, 화면에서 볼 수
    있어야 합니다 (AI-PIPELINE §2.1).
    """
    provider = provider or settings.review_provider
    win = _window(db, provider)
    win.input_tokens += input_tokens
    win.output_tokens += output_tokens
    win.llm_calls += 1

    # 일일 장부에도 같이 쌓습니다 — 유튜브 쿼터와 하루 단위 보고가
    # 여기 붙어 있어서, 창만 쓰면 "오늘 얼마나 했나"를 못 냅니다.
    # **여기는 회사를 가르지 않습니다.** "오늘 얼마나 했나"를 보는 곳이고,
    # 유튜브 유닛·받아쓰기 초처럼 회사와 무관한 값이 같이 있습니다.
    row = _today(db)
    row.input_tokens += input_tokens
    row.output_tokens += output_tokens
    row.llm_calls += 1
    if early_exit:
        row.early_exit_count += 1
        row.early_exit_saved_input_tokens += max(0, saved_input_tokens)
    db.commit()
    used = win.input_tokens + win.output_tokens
    logger.info(
        "[usage] %s +%s/%s 토큰 (이번 창 %s/%s · %s 까지)",
        provider,
        f"{input_tokens:,}",
        f"{output_tokens:,}",
        f"{used:,}",
        f"{limit(db, provider):,}",
        f"{window_end():%H:%M}",
    )
