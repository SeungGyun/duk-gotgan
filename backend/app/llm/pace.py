"""요약을 **언제 다시 부를까** — 막혔을 때 쉬는 자리.

실행 로그가 이렇게 덮여 있었습니다.

    10:56:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
    10:57:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …
    10:58:34 [review] 이번 창의 토큰 상한에 닿았습니다 — 3,011,926/3,000,000 …

**막힌 줄 알면서 1분마다 다시 두드렸습니다.** 실행 기록도 매번 하나씩
쌓여서, 정작 무슨 일이 있었는지 보려는 화면이 같은 줄로 덮였습니다.
자막 트랙은 이미 냉각을 두고 있었는데(`transcript.COOLDOWN_KEY`) 요약만
빠져 있었습니다.

**막힌 이유에 따라 다루는 방식이 다릅니다.**

  우리 상한을 넘음   장부를 **매번 다시 봅니다** — 타이머를 두지 않습니다
  회사가 안 받아 줌   1·2·4·8·16·30분으로 늘려 가며 쉽니다

왜 우리 상한에는 타이머를 두지 않는가. 처음엔 "창이 바뀌는 16:00 까지
쉰다"고 적어 두었습니다. 그런데 **상한은 화면에서 언제든 올릴 수 있습니다.**
13:45 에 상한을 올려도 적어 둔 16:00 이 그대로 남아, 여유가 생겼는데도
두 시간을 놀았습니다.

적어 둔 시각은 **결정을 캐시한 것**인데, 그 결정의 입력(상한·사용량)이
바뀌는 값이었습니다. 장부를 읽는 것은 인덱스 한 번이라 매 틱 다시 봐도
쌉니다 — 캐시할 이유가 없었습니다.

회사 쪽 사정은 다릅니다. 풀렸는지 알아보려면 **불러 보는 수밖에** 없어서,
그때는 타이머가 맞습니다. 쿼터가 떨어진 상대에게 1분마다 두드리는 것은
풀리는 데 도움이 안 되고, 상대가 더 세게 막는 빌미가 됩니다.

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


def _capped_key(provider: str) -> str:
    return f"review.capped_since:{provider}"


def resume_at(db: Session, provider: str) -> datetime | None:
    """언제까지 쉬기로 했나. 지나갔으면 None — 쉬는 중이 아닙니다."""
    at = state.get_time(db, _resume_key(provider))
    return at if at is not None and at > now_kst() else None


def capped(db: Session, provider: str) -> bool:
    """우리 상한을 넘어 멈춰 있는 상태인가. 화면이 이유를 말해 줄 때 씁니다."""
    return state.get_time(db, _capped_key(provider)) is not None


def mark_capped(db: Session, provider: str, why: str) -> None:
    """우리 상한을 넘었습니다. **타이머를 두지 않습니다** — 다음 틱에
    장부를 다시 보고, 상한이 올라갔으면 그대로 재개합니다.

    여기서 하는 일은 **한 번만 말하기**뿐입니다. 매 틱 같은 줄을 찍으면
    실행 로그가 그것으로 덮입니다.
    """
    if capped(db, provider):
        return
    state.set_time(db, _capped_key(provider), now_kst())
    logger.info("[review] %s 는 상한을 넘어 멈춥니다 — %s", provider, why)


def clear_capped(db: Session, provider: str) -> None:
    """여유가 생겼습니다. 멈춰 있었다면 재개한다고 한 번 알립니다."""
    if not capped(db, provider):
        return
    state.set_time(db, _capped_key(provider), None)
    logger.info("[review] %s 재개 — 상한에 여유가 생겼습니다", provider)


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
