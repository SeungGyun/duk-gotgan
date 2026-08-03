"""키워드 — docs/API.md §1.

**키워드 행은 사람별로 쪼개지 않습니다.** `UNIQUE(term)` 이 "같은 검색어는
한 번만 수집한다"는 뜻이고, 그것이 사람이 늘어도 유튜브 호출과 요약 비용이
늘지 않는 이유입니다. 대신 `user_keywords` 가 누가 무엇을 구독하는지를
들고 있고, 이 파일의 모든 조회는 그걸 지납니다.

그래서 "삭제"의 뜻이 바뀝니다 — **내 구독을 끊는 것**이고, 마지막 구독자가
빠졌을 때만 키워드가 보관됩니다. 안 그러면 보는 사람이 0명인 키워드를 매일
수집하게 됩니다.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.api.errors import ApiError
from app.api.serializers import keyword_out
from app.collector.youtube import YouTubeError, resolve_channel
from app.db.models import Keyword, Lecture, User, UserKeyword, VideoKeyword
from app.db.session import get_db
from config.settings import settings
from config.time import now_kst, to_utc_iso

router = APIRouter(prefix="/keywords", tags=["keywords"])

STATUSES = {"pending", "active", "quota_wait", "paused", "archived"}
SOURCES = {"search", "channel"}
LANGUAGES = {"ko", "en", "any"}
SCHEDULES = {"daily", "twice_weekly", "weekly"}


class KeywordDraft(BaseModel):
    term: str
    # search  — 검색어로 찾기 (검색 1회 100유닛)
    # channel — 관심 채널 구독. term 에 @핸들을 넣습니다 (1유닛, 50배 쌈)
    sourceType: str = "search"
    language: str = "ko"
    schedule: str = "daily"
    minDurationSec: int = Field(default=900, ge=0)
    minExpertScore: int = Field(default=75, ge=0, le=100)
    maxPerRun: int = Field(default=10, ge=1, le=50)


class KeywordPatch(BaseModel):
    term: str | None = None
    status: str | None = None
    language: str | None = None
    schedule: str | None = None
    minDurationSec: int | None = Field(default=None, ge=0)
    minExpertScore: int | None = Field(default=None, ge=0, le=100)
    maxPerRun: int | None = Field(default=None, ge=1, le=50)


def lecture_counts(db: Session) -> dict[str, int]:
    """키워드별 공개된 강의 수.

    N+1 을 피하려고 한 번에 세어 둡니다. 숨김 처리된 것은 사용자에게 없는 것과
    같으므로 세지 않습니다.
    """
    rows = db.execute(
        select(VideoKeyword.keyword_id, func.count(func.distinct(Lecture.video_id)))
        .join(Lecture, Lecture.video_id == VideoKeyword.video_id)
        .where(Lecture.is_hidden.is_(False))
        .group_by(VideoKeyword.keyword_id)
    ).all()
    return {kid: cnt for kid, cnt in rows}


def subscriber_counts(db: Session) -> dict[str, int]:
    """키워드마다 몇 명이 **지금** 보는가 (끊은 사람은 빼고).

    화면에 "3명이 함께 보는 키워드" 를 띄우는 데 씁니다. 수집 설정(주기·
    길이·언어)은 키워드에 붙어 있어서 **고치면 같이 보는 사람 모두에게**
    적용됩니다 — 그게 맞는 동작이지만, 모르고 바꾸면 남의 것을 건드린 셈이
    되므로 미리 보여 줍니다.
    """
    rows = db.execute(
        select(UserKeyword.keyword_id, func.count())
        .where(UserKeyword.archived_at.is_(None))
        .group_by(UserKeyword.keyword_id)
    ).all()
    return {kid: n for kid, n in rows}


def _my_subs(db: Session, user_id: str, archived: bool) -> set[str]:
    """내가 보고 있는 것 / 내가 끊은 것."""
    stmt = select(UserKeyword.keyword_id).where(UserKeyword.user_id == user_id)
    stmt = stmt.where(
        UserKeyword.archived_at.isnot(None) if archived else UserKeyword.archived_at.is_(None)
    )
    return set(db.scalars(stmt).all())


def _sub(db: Session, user_id: str, keyword_id: str) -> UserKeyword | None:
    return db.get(UserKeyword, {"user_id": user_id, "keyword_id": keyword_id})


def _check_room(db: Session, user: User) -> None:
    """상한에 걸리면 여기서 막습니다.

    **주인은 예외입니다.** 상한을 넣은 시점에 이미 13개를 쓰고 계셨는데,
    상한이 생겼다고 셋을 지우라고 할 수는 없습니다.
    """
    if user.is_owner:
        return
    n = int(
        db.scalar(
            select(func.count())
            .select_from(UserKeyword)
            .where(UserKeyword.user_id == user.id, UserKeyword.archived_at.is_(None))
        )
        or 0
    )
    if n >= settings.max_keywords_per_user:
        raise ApiError(
            409,
            "KEYWORD_LIMIT",
            f"키워드는 {settings.max_keywords_per_user}개까지입니다. "
            "쓰지 않는 것을 먼저 빼 주세요.",
        )


def _retire_if_empty(db: Session, kw: Keyword) -> None:
    """마지막 구독자가 빠졌으면 수집을 멈춥니다.

    안 그러면 **보는 사람이 0명인 키워드를 매일 수집합니다** — 유튜브
    유닛도 자막도 요약 토큰도 아무도 안 읽을 것에 씁니다.
    """
    left = int(
        db.scalar(
            select(func.count())
            .select_from(UserKeyword)
            .where(UserKeyword.keyword_id == kw.id, UserKeyword.archived_at.is_(None))
        )
        or 0
    )
    if left == 0 and kw.status != "archived":
        kw.status = "archived"
        kw.archived_at = now_kst()


def _wake(kw: Keyword) -> None:
    """누가 다시 구독했으니 수집을 재개합니다."""
    if kw.status == "archived":
        kw.status = "pending" if kw.last_run_at is None else "active"
        kw.archived_at = None


def _out(db: Session, k: Keyword, user_id: str) -> dict:
    sub = _sub(db, user_id, k.id)
    out = keyword_out(k, lecture_counts(db).get(k.id, 0))
    out["isMine"] = sub is not None and sub.archived_at is None
    out["subscriberCount"] = subscriber_counts(db).get(k.id, 0)
    # 삭제 영역이 "언제 지웠는지" 를 보여 줍니다. 키워드 행의 값이 아니라
    # **내가 끊은 시각**입니다 — 남이 언제 끊었는지는 내 화면과 무관합니다.
    if sub is not None and sub.archived_at is not None:
        out["archivedAt"] = to_utc_iso(sub.archived_at)
    return out


def _validate(value: str | None, allowed: set[str], field: str) -> None:
    if value is not None and value not in allowed:
        raise ApiError(
            400,
            "INVALID_VALUE",
            f"{field} 값이 올바르지 않습니다. 가능한 값: {', '.join(sorted(allowed))}",
        )


@router.get("")
def list_keywords(
    archived: bool = Query(default=False, description="true 면 삭제(보관)된 것만"),
    mine: bool = Query(default=True, description="false 면 남이 보는 것까지"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """`mine=false` 면 **아직 구독하지 않은 것까지** 돌려줍니다.

    새로 들어온 사람이 고를 목록이고, 이미 쓰던 사람에게도 "다른 사람은
    이런 걸 보는구나" 가 됩니다 — 남이 이미 등록해 둔 키워드를 구독하면
    수집 비용이 전혀 늘지 않으므로, 새로 만드는 것보다 이쪽이 낫습니다.
    """
    counts = lecture_counts(db)
    subs = subscriber_counts(db)

    if archived:
        # **내가 끊은 것**만입니다. 키워드가 아직 살아 있어도(남이 보고 있어도)
        # 내 삭제 영역에는 있어야 되살릴 수 있습니다.
        gone = db.scalars(
            select(UserKeyword)
            .where(UserKeyword.user_id == user.id, UserKeyword.archived_at.isnot(None))
            # 방금 지운 것을 가장 자주 찾습니다
            .order_by(UserKeyword.archived_at.desc())
        ).all()
        out = []
        for s in gone:
            k = db.get(Keyword, s.keyword_id)
            if k is None:
                continue
            d = keyword_out(k, counts.get(k.id, 0))
            d["isMine"] = False
            d["subscriberCount"] = subs.get(k.id, 0)
            d["archivedAt"] = to_utc_iso(s.archived_at)
            out.append(d)
        return out

    owned = _my_subs(db, user.id, archived=False)
    rows = db.scalars(
        select(Keyword).where(Keyword.status != "archived").order_by(Keyword.created_at)
    ).all()
    if mine:
        rows = [k for k in rows if k.id in owned]

    out = []
    for k in rows:
        d = keyword_out(k, counts.get(k.id, 0))
        d["isMine"] = k.id in owned
        d["subscriberCount"] = subs.get(k.id, 0)
        out.append(d)
    return out


@router.post("", status_code=201)
def create_keyword(
    draft: KeywordDraft,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """새 키워드. **주인이 아니어도 만들 수 있습니다.**

    비용을 쓰는 일이라 주인만으로 막을까 했지만, 그러면 두 번째 사람에게는
    곳간이 "남이 고른 것만 읽는 곳" 이 됩니다. 1인 10개 상한이 이미 피해를
    묶고 있어서 그쪽으로 충분합니다.
    """
    term = draft.term.strip()
    if not term:
        raise ApiError(400, "TERM_REQUIRED", "검색어를 입력해 주세요.")

    _validate(draft.language, LANGUAGES, "language")
    _validate(draft.schedule, SCHEDULES, "schedule")
    _validate(draft.sourceType, SOURCES, "sourceType")
    _check_room(db, user)

    # 채널 구독이면 핸들을 지금 해석해 둡니다(1유닛). 등록 시점에 확인해야
    # 오타를 바로 알려줄 수 있고, 수집할 때마다 다시 찾지 않아도 됩니다.
    channel = None
    if draft.sourceType == "channel":
        try:
            channel = resolve_channel(term)
        except YouTubeError as e:
            raise ApiError(404, "CHANNEL_NOT_FOUND", str(e)) from e
        term = f"@{term.lstrip('@')}"

    existing = db.scalar(select(Keyword).where(Keyword.term == term))
    if existing is not None:
        mine = _sub(db, user.id, existing.id)
        if mine is not None and mine.archived_at is None:
            raise ApiError(409, "KEYWORD_DUPLICATE", f'"{term}" 은(는) 이미 등록되어 있습니다.')
        # 남이 이미 등록해 둔 것이거나, 내가 지웠던 것입니다. 어느 쪽이든
        # **새로 만들지 않고 구독만 붙입니다** — 새로 만들면 지금까지 이
        # 키워드로 모은 강의와의 연결이 끊기고, 수집도 두 번 돌게 됩니다.
        _wake(existing)
        if mine is None:
            db.add(UserKeyword(user_id=user.id, keyword_id=existing.id))
        else:
            mine.archived_at = None
        db.commit()
        return _out(db, existing, user.id)

    kw = Keyword(
        term=term,
        source_type=draft.sourceType,
        channel_id=channel.channel_id if channel else None,
        channel_title=channel.title if channel else None,
        uploads_playlist_id=channel.uploads_playlist_id if channel else None,
        status="pending",  # 이 상태가 수집 스케줄러의 트리거입니다
        language=draft.language,
        schedule=draft.schedule,
        min_duration_sec=draft.minDurationSec,
        min_expert_score=draft.minExpertScore,
        max_per_run=draft.maxPerRun,
    )
    db.add(kw)
    db.flush()
    db.add(UserKeyword(user_id=user.id, keyword_id=kw.id))
    db.commit()
    return _out(db, kw, user.id)


@router.post("/{keyword_id}/subscribe", status_code=201)
def subscribe(
    keyword_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """이미 있는 키워드를 내 것으로. **수집 비용이 전혀 늘지 않습니다.**"""
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")
    mine = _sub(db, user.id, keyword_id)
    if mine is not None and mine.archived_at is None:
        return _out(db, kw, user.id)

    _check_room(db, user)
    _wake(kw)
    if mine is None:
        db.add(UserKeyword(user_id=user.id, keyword_id=keyword_id))
    else:
        mine.archived_at = None
    db.commit()
    return _out(db, kw, user.id)


@router.patch("/{keyword_id}")
def update_keyword(
    keyword_id: str,
    patch: KeywordPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """**구독한 사람만 고칠 수 있습니다.**

    고친 결과는 같이 보는 사람 모두에게 적용됩니다 — 수집이 공유라 그게
    맞습니다. 화면이 `subscriberCount` 로 그 사실을 미리 알립니다.
    """
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")
    mine = _sub(db, user.id, keyword_id)
    if mine is None or mine.archived_at is not None:
        raise ApiError(403, "NOT_SUBSCRIBED", "구독한 키워드만 고칠 수 있습니다.")

    _validate(patch.status, STATUSES, "status")
    _validate(patch.language, LANGUAGES, "language")
    _validate(patch.schedule, SCHEDULES, "schedule")

    if patch.term is not None:
        term = patch.term.strip()
        if not term:
            raise ApiError(400, "TERM_REQUIRED", "검색어를 입력해 주세요.")
        clash = db.scalar(select(Keyword).where(Keyword.term == term, Keyword.id != keyword_id))
        if clash is not None:
            raise ApiError(409, "KEYWORD_DUPLICATE", f'"{term}" 은(는) 이미 등록되어 있습니다.')
        kw.term = term

    for field, column in (
        ("status", "status"),
        ("language", "language"),
        ("schedule", "schedule"),
        ("minDurationSec", "min_duration_sec"),
        ("minExpertScore", "min_expert_score"),
        ("maxPerRun", "max_per_run"),
    ):
        value = getattr(patch, field)
        if value is not None:
            setattr(kw, column, value)

    db.commit()
    return _out(db, kw, user.id)


@router.delete("/{keyword_id}", status_code=204)
def delete_keyword(
    keyword_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """내 구독을 끊습니다.

    **마지막 구독자일 때만 키워드가 보관됩니다.** 아니면 남은 사람이 계속
    보고 있는데 수집이 멈춥니다.

    거꾸로, 마지막 사람이 빠졌는데도 `active` 로 두면 **보는 사람이 0명인
    키워드를 매일 수집합니다** — 유튜브 유닛도 자막도 요약 토큰도 아무도
    안 읽을 것에 씁니다.

    모아 둔 강의와 연결(`video_keywords`)은 건드리지 않습니다. 되살렸을 때
    "몇 편 모았는지" 가 이어져야 복구가 복구다워집니다.
    """
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")

    mine = _sub(db, user.id, keyword_id)
    if mine is None or mine.archived_at is not None:
        raise ApiError(404, "NOT_SUBSCRIBED", "구독하지 않은 키워드입니다.")
    # 행을 지우지 않고 시각만 찍습니다 — 지우면 내 삭제 영역에서도 사라져
    # 되살릴 방법이 없어집니다.
    mine.archived_at = now_kst()
    db.flush()
    _retire_if_empty(db, kw)
    db.commit()


@router.post("/{keyword_id}/restore")
def restore_keyword(
    keyword_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """삭제 영역에서 되살립니다.

    돌아갈 상태를 UI 가 고르게 하지 않습니다. 한 번도 안 돌아본 키워드는
    `pending` 으로 보내 첫 수집을 받게 하고, 이미 돌던 것은 `active` 로
    되돌려 주기를 이어갑니다.
    """
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")

    mine = _sub(db, user.id, keyword_id)
    if mine is not None and mine.archived_at is None:
        raise ApiError(409, "NOT_ARCHIVED", "삭제된 키워드가 아닙니다.")

    _check_room(db, user)
    if mine is None:
        db.add(UserKeyword(user_id=user.id, keyword_id=keyword_id))
    else:
        mine.archived_at = None
    _wake(kw)
    db.commit()
    return _out(db, kw, user.id)
