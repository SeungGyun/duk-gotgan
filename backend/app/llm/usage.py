"""사용량 가드 — 구독 인증이라 **비용 가드가 아닙니다**.

청구액은 정액이고 실제 병목은 구독의 사용량 한도입니다. 그래서 돈이 아니라
**토큰을 셉니다.** `total_cost_usd` 도 기록하지만 청구액이 아니라 사용량
프록시일 뿐이고, 화면에 "비용"이라고 쓰지 않습니다 (SPEC §8.1).
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UsageLedger
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)


class UsageExceeded(Exception):
    def __init__(self, used: int, limit: int):
        self.used, self.limit = used, limit
        super().__init__(
            f"오늘 토큰 상한에 닿았습니다 — {used:,}/{limit:,}. "
            "진행 중인 건만 마치고 신규 검토는 내일 재개합니다."
        )


def _today(db: Session, day: date | None = None) -> UsageLedger:
    day = day or now_kst().date()
    row = db.scalar(select(UsageLedger).where(UsageLedger.day == day).with_for_update())
    if row is None:
        row = UsageLedger(day=day)
        db.add(row)
        db.flush()
    return row


def check(db: Session, est_tokens: int = 0) -> None:
    """호출 전에 확인합니다. 상한이 0 이면 무제한으로 봅니다."""
    limit = settings.daily_token_limit
    if not limit:
        return
    row = _today(db)
    used = row.input_tokens + row.output_tokens
    if used + est_tokens > limit:
        raise UsageExceeded(used, limit)


def record(
    db: Session,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float = 0.0,
    early_exit: bool = False,
    saved_input_tokens: int = 0,
) -> None:
    """호출 1건의 사용량을 장부에 더합니다.

    `early_exit` 은 **조기 종료가 실제로 작동하는지**를 추적하는 값입니다.
    통합 호출 구조의 비용 이점이 전적으로 여기 달려 있어서, 화면에서 볼 수
    있어야 합니다 (AI-PIPELINE §2.1).
    """
    row = _today(db)
    row.input_tokens += input_tokens
    row.output_tokens += output_tokens
    row.llm_calls += 1
    if early_exit:
        row.early_exit_count += 1
        row.early_exit_saved_input_tokens += max(0, saved_input_tokens)
    db.commit()
    logger.info(
        "[usage] +%s/%s 토큰 (오늘 %s) %s",
        f"{input_tokens:,}",
        f"{output_tokens:,}",
        f"{row.input_tokens + row.output_tokens:,}",
        f"· 조기종료 {saved_input_tokens:,} 절약" if early_exit else "",
    )
