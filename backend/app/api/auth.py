"""누가 보고 있는지 — 쿠키 하나로 알아냅니다.

**로그인 화면이 아니라 사용자 선택 화면입니다.** 유튜브처럼 들어오면 누구인지
고르고, 비밀번호를 걸어 둔 사람만 네 자리를 한 번 더 묻습니다. 고른 결과가
쿠키에 남아서 다음부터는 바로 들어갑니다.

**쿠키에는 사용자 ID 가 아니라 세션 토큰이 들어갑니다.** 폰 하나만 끊고
싶을 때 그 세션 행만 지우면 되고, 값이 새도 세션 하나를 버리는 것으로
끝납니다.

쿠키에 붙는 조건 두 가지는 둘 다 실제 제약에서 나왔습니다:

- `HttpOnly` — 사파리는 **자바스크립트가 심은 쿠키를 7일 만에 지웁니다.**
  아이폰에서 보고 계시니 그대로 걸려서, 매주 선택 화면이 다시 떴을 겁니다.
  서버가 `Set-Cookie` 로 심고 이 옵션을 붙여야 2년이 유지됩니다.
- `Secure` 는 **뺍니다.** 집 안에서 `http://192.168.…` 로 붙는데 이걸 붙이면
  브라우저가 쿠키를 아예 안 심습니다. 밖으로 열게 되면 그때 TLS 와 함께
  켜야 합니다.
"""

import logging
from datetime import datetime, timedelta

from fastapi import Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.db.models import User, UserSession
from app.db.session import get_db
from app.security import new_token, verify_pin
from config.time import now_kst

logger = logging.getLogger(__name__)

COOKIE = "gotgan"
# 2년. 이보다 짧으면 어느 날 갑자기 "누구세요?" 가 떠서, 고장인지 아닌지
# 구분이 안 됩니다.
COOKIE_MAX_AGE = 2 * 365 * 24 * 3600

# 비밀번호가 네 자리라 만 가지뿐입니다. **짧은 비밀번호를 쓸 수 있게 만드는
# 것이 이 잠금입니다** — 다섯 번 틀리면 5분 쉬게 해서, 만 번을 찍으려면
# 일주일이 넘게 걸리도록 만듭니다.
MAX_TRIES = 5
LOCK_MINUTES = 5

# 사용자 id → (틀린 횟수, 잠금이 풀리는 시각).
# 프로세스 메모리에 둡니다 — API 는 한 프로세스로 돌고(ops/api.sh),
# 재시작으로 초기화되는 것은 사람이 개입해야 하는 일이라 악용되지 않습니다.
_tries: dict[str, tuple[int, datetime | None]] = {}


def _locked_until(user_id: str) -> datetime | None:
    _, until = _tries.get(user_id, (0, None))
    if until is not None and until > now_kst():
        return until
    if until is not None:
        _tries.pop(user_id, None)  # 잠금이 풀렸으면 기록도 지웁니다
    return None


def check_pin(user: User, pin: str) -> None:
    """틀리면 그 자리에서 예외를 냅니다. 통과하면 조용히 돌아옵니다."""
    until = _locked_until(user.id)
    if until is not None:
        left = max(1, int((until - now_kst()).total_seconds() // 60) + 1)
        raise ApiError(
            429, "PIN_LOCKED", f"비밀번호를 여러 번 틀렸습니다. {left}분 뒤에 다시 해 주세요."
        )

    if verify_pin(pin, user.password_hash):
        _tries.pop(user.id, None)
        return

    n = _tries.get(user.id, (0, None))[0] + 1
    if n >= MAX_TRIES:
        _tries[user.id] = (n, now_kst() + timedelta(minutes=LOCK_MINUTES))
        logger.warning("[auth] %s 비밀번호 %d회 실패 — %d분 잠금", user.name, n, LOCK_MINUTES)
        raise ApiError(
            429,
            "PIN_LOCKED",
            f"비밀번호를 {MAX_TRIES}번 틀렸습니다. {LOCK_MINUTES}분 뒤에 다시 해 주세요.",
        )
    _tries[user.id] = (n, None)
    raise ApiError(401, "PIN_WRONG", f"비밀번호가 다릅니다. {MAX_TRIES - n}번 더 틀리면 잠깁니다.")


def open_session(db: Session, user: User, response: Response) -> str:
    """고른 사람으로 들어갑니다. 쿠키를 심고 토큰을 돌려줍니다."""
    token = new_token()
    db.add(UserSession(token=token, user_id=user.id))
    user.last_seen_at = now_kst()
    db.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return token


def close_session(db: Session, request: Request, response: Response) -> None:
    """사용자 바꾸기. 이 기기의 세션만 지웁니다 — 계정은 그대로입니다."""
    token = request.cookies.get(COOKIE)
    if token:
        row = db.get(UserSession, token)
        if row is not None:
            db.delete(row)
            db.commit()
    response.delete_cookie(COOKIE, path="/")


def find_user(db: Session, request: Request) -> User | None:
    """쿠키로 사람을 찾습니다. 없으면 None — 예외를 내지 않습니다.

    선택 화면 자체는 로그인 없이 열려야 하므로, "없음" 이 정상인 자리가
    있습니다. 예외를 내는 것은 아래 `current_user` 쪽 일입니다.
    """
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.get(UserSession, token)
    if row is None:
        return None
    user = db.get(User, row.user_id)
    if user is None:
        return None
    # 마지막으로 본 시각. 매 요청 쓰면 낭비라 하루 한 번만 갱신합니다.
    now = now_kst()
    if (now - row.last_seen_at).total_seconds() > 86_400:
        row.last_seen_at = now
        user.last_seen_at = now
        db.commit()
    return user


def current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """화면이 부르는 거의 모든 API 가 이걸 지납니다.

    401 을 받으면 프론트가 선택 화면으로 보냅니다 — 그 처리는 `http.ts` 의
    `req()` 한 곳에 있어서, 호출하는 자리마다 따로 챙길 필요가 없습니다.
    """
    user = find_user(db, request)
    if user is None:
        raise ApiError(401, "NO_SESSION", "누구인지 먼저 골라 주세요.")
    return user


def require_owner(user: User = Depends(current_user)) -> User:
    """관리자만 누를 수 있는 것 — 수집을 직접 돌리는 버튼 같은 것.

    **보는 것은 막지 않습니다.** 실행 로그와 대기열은 식구도 봅니다 —
    "왜 아직 안 올라왔지"를 스스로 확인할 수 있어야 물어볼 일이 줍니다.
    """
    if not user.is_owner:
        raise ApiError(403, "OWNER_ONLY", "관리자만 할 수 있습니다.")
    return user


def owner_id(db: Session) -> str | None:
    return db.scalar(select(User.id).where(User.is_owner.is_(True)))


__all__ = [
    "COOKIE",
    "check_pin",
    "close_session",
    "current_user",
    "find_user",
    "open_session",
    "owner_id",
    "require_owner",
]
