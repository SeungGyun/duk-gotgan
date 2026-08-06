"""사람 고르기 — 선택 화면이 쓰는 API.

이 파일의 라우트 중 **`GET /users` 와 `POST /session` 만 로그인 없이** 열려
있습니다. 선택 화면 자체가 로그인 전 화면이라 그렇습니다. 나머지는 전부
`current_user` 를 지납니다.
"""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import check_pin, close_session, current_user, open_session
from app.api.errors import ApiError
from app.db.models import Keyword, User, UserKeyword
from app.db.session import get_db
from app.security import hash_pin, is_valid_pin, verify_pin
from config.settings import settings

router = APIRouter(tags=["users"])

# 처음 만들어 두는 관리자 비밀번호. 화면이 이 값 그대로인지 알아보고
# "바꾸세요" 를 띄웁니다.
DEFAULT_PIN = "0000"


class Pick(BaseModel):
    userId: str
    pin: str | None = None


class NewUser(BaseModel):
    name: str
    pin: str | None = None
    # 처음 들어온 사람은 무엇을 볼지 모릅니다. 있는 키워드에서 고르게 하면
    # 빈 곳간 대신 이미 모아 둔 강의가 바로 채워집니다.
    keywordIds: list[str] = Field(default_factory=list)


class PinChange(BaseModel):
    current: str | None = None
    next: str | None = None


class Rename(BaseModel):
    name: str


def _lecture_counts(db: Session, ids: list[str]) -> dict[str, int]:
    """사람별로 몇 편이 보이는지. 선택 화면에서 이름 밑에 붙습니다 —
    누르기 전에 무엇이 있을지 보이는 편이 낫습니다.

    **목록 화면과 같은 함수로 셉니다.** 한 번에 묶어 세는 편이 빠르지만,
    그러면 제외한 것과 막은 채널이 빠지지 않아 숫자가 어긋납니다 —
    실제로 "334편" 이라고 써 놓고 들어가면 260편이었습니다. 사람 수가
    한 자리라 질의가 몇 개 더 나가는 것은 문제가 되지 않습니다.
    """
    from app.api.routes.lectures import Filters, _filtered

    out: dict[str, int] = {}
    for uid in ids:
        stmt, _ = _filtered(Filters(uid))
        out[uid] = int(db.scalar(stmt.with_only_columns(func.count()).order_by(None)) or 0)
    return out


def _keyword_count(db: Session, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(UserKeyword)
            .where(UserKeyword.user_id == user_id, UserKeyword.archived_at.is_(None))
        )
        or 0
    )


def user_out(u: User, lecture_count: int = 0) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "isOwner": bool(u.is_owner),
        # 비밀번호를 걸었는지만 알려 줍니다. 선택 화면이 자물쇠를 그리고,
        # 누른 뒤에 입력칸을 띄울지 정하는 데 씁니다.
        "hasPin": bool(u.password_hash),
        "lectureCount": lecture_count,
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """선택 화면. **로그인 없이 열립니다** — 이게 로그인 전 화면입니다."""
    rows = db.scalars(
        # 관리자가 앞에, 나머지는 만든 순서대로. 매번 자리가 바뀌면 누르는
        # 위치를 외울 수 없습니다.
        select(User).order_by(User.is_owner.desc(), User.created_at)
    ).all()
    counts = _lecture_counts(db, [u.id for u in rows])
    return [user_out(u, counts.get(u.id, 0)) for u in rows]


@router.post("/session")
def pick_user(pick: Pick, response: Response, db: Session = Depends(get_db)):
    """이 사람으로 들어갑니다."""
    user = db.get(User, pick.userId)
    if user is None:
        raise ApiError(404, "USER_NOT_FOUND", "그 사람을 찾을 수 없습니다.")

    if user.password_hash:
        if not pick.pin:
            raise ApiError(401, "PIN_REQUIRED", "비밀번호 네 자리를 입력해 주세요.")
        check_pin(user, pick.pin)  # 틀리면 여기서 끝납니다

    open_session(db, user, response)
    return user_out(user, _lecture_counts(db, [user.id])[user.id])


@router.delete("/session", status_code=204)
def switch_user(request: Request, response: Response, db: Session = Depends(get_db)):
    """사용자 바꾸기. 이 기기만 나갑니다."""
    close_session(db, request, response)


@router.post("/users", status_code=201)
def create_user(draft: NewUser, response: Response, db: Session = Depends(get_db)):
    """새 사람. 만들고 나면 바로 그 사람으로 들어갑니다."""
    name = draft.name.strip()
    if not name:
        raise ApiError(400, "NAME_REQUIRED", "이름을 입력해 주세요.")
    if len(name) > 40:
        raise ApiError(400, "NAME_TOO_LONG", "이름은 40자까지입니다.")
    if db.scalar(select(User).where(User.name == name)) is not None:
        raise ApiError(409, "NAME_DUPLICATE", f'"{name}" 은(는) 이미 있습니다.')

    if draft.pin is not None and not is_valid_pin(draft.pin):
        raise ApiError(400, "PIN_FORMAT", "비밀번호는 숫자 네 자리입니다.")

    ids = list(dict.fromkeys(draft.keywordIds))  # 순서를 지키며 중복 제거
    if len(ids) > settings.max_keywords_per_user:
        raise ApiError(
            400,
            "KEYWORD_LIMIT",
            f"키워드는 {settings.max_keywords_per_user}개까지 고를 수 있습니다.",
        )
    if ids:
        found = set(
            db.scalars(
                select(Keyword.id).where(Keyword.id.in_(ids), Keyword.status != "archived")
            ).all()
        )
        missing = [i for i in ids if i not in found]
        if missing:
            raise ApiError(404, "KEYWORD_NOT_FOUND", "고른 키워드 중 없는 것이 있습니다.")

    user = User(
        name=name,
        password_hash=hash_pin(draft.pin) if draft.pin else None,
        is_owner=False,
    )
    db.add(user)
    db.flush()
    for kid in ids:
        db.add(UserKeyword(user_id=user.id, keyword_id=kid))
    db.commit()

    open_session(db, user, response)
    return user_out(user, _lecture_counts(db, [user.id])[user.id])


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = user_out(user, _lecture_counts(db, [user.id])[user.id])
    out.update(
        {
            "keywordCount": _keyword_count(db, user.id),
            "keywordLimit": (
                # 관리자는 상한을 넘겨 쓰고 계실 수 있습니다 — 상한을 넣었다고
                # 지금 쓰는 것을 지우라고 할 수는 없어서 예외로 둡니다.
                0 if user.is_owner else settings.max_keywords_per_user
            ),
            # 첫 비밀번호(0000) 그대로면 화면이 바꾸라고 띄웁니다. 선택
            # 화면에 관리자가 그냥 떠 있으므로, 이게 그대로면 "관리자만" 이라는
            # 제한이 잠금이 아니라 표시가 됩니다.
            "pinIsDefault": verify_pin(DEFAULT_PIN, user.password_hash),
        }
    )
    return out


@router.patch("/me")
def rename_me(patch: Rename, user: User = Depends(current_user), db: Session = Depends(get_db)):
    name = patch.name.strip()
    if not name:
        raise ApiError(400, "NAME_REQUIRED", "이름을 입력해 주세요.")
    if db.scalar(select(User).where(User.name == name, User.id != user.id)) is not None:
        raise ApiError(409, "NAME_DUPLICATE", f'"{name}" 은(는) 이미 있습니다.')
    user.name = name
    db.commit()
    return user_out(user, _lecture_counts(db, [user.id])[user.id])


@router.put("/me/pin", status_code=204)
def set_pin(
    body: PinChange, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """비밀번호를 걸거나 바꾸거나 풉니다 (`next` 를 비우면 풀림).

    **관리자는 풀 수 없습니다.** 선택 화면에 관리자가 그냥 떠 있어서, 비밀번호가
    없으면 같은 공유기에 붙은 누구나 눌러서 관리자가 됩니다 — 그러면
    "관리자만 지금 실행" 이 잠금이 아니라 그냥 표시가 됩니다.
    """
    if user.password_hash:
        if not body.current:
            raise ApiError(400, "PIN_REQUIRED", "지금 비밀번호를 입력해 주세요.")
        check_pin(user, body.current)

    if body.next is None or body.next == "":
        if user.is_owner:
            raise ApiError(
                400,
                "OWNER_NEEDS_PIN",
                "관리자는 비밀번호를 비울 수 없습니다. 선택 화면에서 누구나 관리자로 들어가게 됩니다.",
            )
        user.password_hash = None
    else:
        if not is_valid_pin(body.next):
            raise ApiError(400, "PIN_FORMAT", "비밀번호는 숫자 네 자리입니다.")
        user.password_hash = hash_pin(body.next)
    db.commit()
