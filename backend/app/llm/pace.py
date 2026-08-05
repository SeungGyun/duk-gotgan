"""요약을 **언제 다시 부를까** — 막혔을 때 쉬는 자리.

실행 로그가 이렇게 덮여 있었습니다.

    10:56:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
    10:57:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
    10:58:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …

**막힌 줄 알면서 1분마다 다시 두드렸습니다.** 실행 기록도 매번 하나씩
쌓여서, 정작 무슨 일이 있었는지 보려는 화면이 같은 줄로 덮였습니다.
자막 트랙은 이미 냉각을 두고 있었는데(`transcript.COOLDOWN_KEY`) 요약만
빠져 있었습니다.

**얼마나 쉴지는 상황이 정합니다.**

  창의 토큰을 다 씀   창이 바뀌는 **정확한 시각**을 압니다 → 그때까지
  회사가 안 받아 줌    언제 풀릴지 모릅니다 → 1·2·4·8·16·30분으로 늘려 가며

두 번째가 왜 점점 길어져야 하는가. 쿼터가 떨어진 상대에게 1분마다
두드리는 것은 풀리는 데 도움이 안 되고, 상대가 더 세게 막는 빌미가 됩니다.

**회사마다 따로 셉니다.** 한쪽이 쉬는 동안 다른 쪽은 그대로 돌아야 합니다 —
같이 재우면 회사를 나눈 의미가 없습니다.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db import state
from config.time import now_kst

logger = logging.getLogger(__name__)

# 점점 길어지는 간격(분). 마지막 값에서 멈춥니다 — 더 늘리면 풀린 뒤에도
# 반 시간씩 놀게 되고, 그러면 대기 줄이 줄지 않습니다.
BACKOFF_MIN = (1, 2, 4, 8, 16, 30)


def _resume_key(provider: str) -> str:
    return f"review.resume_at:{provider}"


def _strikes_key(provider: str) -> str:
    return f"review.strikes:{provider}"


def resume_at(db: Session, provider: str) -> datetime | None:
    """언제까지 쉬기로 했나. 지나갔으면 None — 쉬는 중이 아닙니다."""
    at = state.get_time(db, _resume_key(provider))
    return at if at is not None and at > now_kst() else None


def rest_until(db: Session, provider: str, when: datetime, why: str) -> None:
    """**언제 풀리는지 아는 경우.** 그 시각까지 쉽니다.

    창의 토큰을 다 썼을 때가 이 경우입니다 — 창이 5시간 단위로 딱 떨어져서
    어림잡을 이유가 없습니다.
    """
    if _already_resting(db, provider, when):
        return
    state.set_time(db, _resume_key(provider), when)
    logger.info("[review] %s 는 %s 까지 쉽니다 — %s", provider, f"{when:%H:%M}", why)


def back_off(db: Session, provider: str, why: str) -> datetime:
    """**언제 풀릴지 모르는 경우.** 쉬는 시간을 한 칸씩 늘립니다."""
    n = min(_strikes(db, provider), len(BACKOFF_MIN) - 1)
    minutes = BACKOFF_MIN[n]
    until = now_kst() + timedelta(minutes=minutes)
    state.set_time(db, _resume_key(provider), until)
    state.set_int(db, _strikes_key(provider), n + 1)
    logger.warning(
        "[review] %s 가 안 받습니다 — %d분 쉬고 %s 에 다시 봅니다 (%s)",
        provider, minutes, f"{until:%H:%M}", why,
    )
    return until


def clear(db: Session, provider: str) -> None:
    """한 건이라도 잘 되면 처음으로 되돌립니다.

    **누적을 지우지 않으면** 어제 몇 번 막혔던 것 때문에 오늘 첫 실패가
    곧바로 30분짜리가 됩니다.
    """
    if state.get_time(db, _resume_key(provider)) is not None:
        state.set_time(db, _resume_key(provider), None)
    if _strikes(db, provider):
        state.set_int(db, _strikes_key(provider), None)


def _strikes(db: Session, provider: str) -> int:
    return state.get_int(db, _strikes_key(provider)) or 0


def _already_resting(db: Session, provider: str, when: datetime) -> bool:
    """같은 시각으로 다시 세우는 것은 조용히 넘깁니다.

    안 그러면 1분마다 "쉽니다" 한 줄씩 쌓여, 줄이려던 소음이 그대로
    남습니다 — 문구만 바뀐 채로요.
    """
    at = state.get_time(db, _resume_key(provider))
    return at is not None and abs((at - when).total_seconds()) < 60
