"""키워드 — docs/API.md §1.

UI 가 실제 테이블에 값을 넣는 지점입니다. 등록된 키워드의 `status='pending'` 이
나중에 붙을 수집 스케줄러의 트리거가 됩니다.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.serializers import keyword_out
from app.collector.youtube import YouTubeError, resolve_channel
from app.db.models import Keyword, Lecture, VideoKeyword
from app.db.session import get_db
from config.time import now_kst

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
    db: Session = Depends(get_db),
):
    counts = lecture_counts(db)
    if archived:
        # 최근에 지운 것이 위로 — 삭제 영역에서는 방금 지운 것을 가장 자주 찾습니다
        stmt = (
            select(Keyword)
            .where(Keyword.status == "archived")
            .order_by(Keyword.archived_at.desc(), Keyword.created_at.desc())
        )
    else:
        stmt = select(Keyword).where(Keyword.status != "archived").order_by(Keyword.created_at)
    return [keyword_out(k, counts.get(k.id, 0)) for k in db.scalars(stmt).all()]


@router.post("", status_code=201)
def create_keyword(draft: KeywordDraft, db: Session = Depends(get_db)):
    term = draft.term.strip()
    if not term:
        raise ApiError(400, "TERM_REQUIRED", "검색어를 입력해 주세요.")

    _validate(draft.language, LANGUAGES, "language")
    _validate(draft.schedule, SCHEDULES, "schedule")
    _validate(draft.sourceType, SOURCES, "sourceType")

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
        if existing.status == "archived":
            # 삭제 영역에 있던 것을 되살립니다. 새로 만들면 예전에 이 키워드로
            # 모은 강의와의 연결이 끊깁니다.
            existing.status = "pending" if existing.last_run_at is None else "active"
            existing.archived_at = None
            db.commit()
            return keyword_out(existing, lecture_counts(db).get(existing.id, 0))
        raise ApiError(409, "KEYWORD_DUPLICATE", f'"{term}" 은(는) 이미 등록되어 있습니다.')

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
    db.commit()
    return keyword_out(kw, 0)


@router.patch("/{keyword_id}")
def update_keyword(keyword_id: str, patch: KeywordPatch, db: Session = Depends(get_db)):
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")

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
    return keyword_out(kw, lecture_counts(db).get(kw.id, 0))


@router.delete("/{keyword_id}", status_code=204)
def delete_keyword(keyword_id: str, db: Session = Depends(get_db)):
    """지우지 않고 보관합니다.

    수집된 강의도, 이 키워드가 데려왔다는 연결도 그대로 둡니다. 되살렸을 때
    "몇 편 모았는지"가 이어져야 복구가 복구다워집니다.
    """
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")
    kw.status = "archived"
    kw.archived_at = now_kst()
    db.commit()


@router.post("/{keyword_id}/restore")
def restore_keyword(keyword_id: str, db: Session = Depends(get_db)):
    """삭제 영역에서 되살립니다.

    돌아갈 상태를 UI 가 고르게 하지 않습니다. 한 번도 안 돌아본 키워드는
    `pending` 으로 보내 첫 수집을 받게 하고, 이미 돌던 것은 `active` 로
    되돌려 주기를 이어갑니다.
    """
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")
    if kw.status != "archived":
        raise ApiError(409, "NOT_ARCHIVED", "삭제된 키워드가 아닙니다.")

    kw.status = "pending" if kw.last_run_at is None else "active"
    kw.archived_at = None
    db.commit()
    return keyword_out(kw, lecture_counts(db).get(kw.id, 0))
