"""키워드 — docs/API.md §1.

UI 가 실제 테이블에 값을 넣는 지점입니다. 등록된 키워드의 `status='pending'` 이
나중에 붙을 수집 스케줄러의 트리거가 됩니다.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.serializers import keyword_out
from app.db.models import Keyword, Lecture, VideoKeyword
from app.db.session import get_db

router = APIRouter(prefix="/keywords", tags=["keywords"])

STATUSES = {"pending", "active", "quota_wait", "paused", "archived"}
LANGUAGES = {"ko", "en", "any"}
SCHEDULES = {"daily", "twice_weekly", "weekly"}


class KeywordDraft(BaseModel):
    term: str
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
def list_keywords(db: Session = Depends(get_db)):
    counts = lecture_counts(db)
    rows = db.scalars(
        select(Keyword).where(Keyword.status != "archived").order_by(Keyword.created_at)
    ).all()
    return [keyword_out(k, counts.get(k.id, 0)) for k in rows]


@router.post("", status_code=201)
def create_keyword(draft: KeywordDraft, db: Session = Depends(get_db)):
    term = draft.term.strip()
    if not term:
        raise ApiError(400, "TERM_REQUIRED", "검색어를 입력해 주세요.")

    _validate(draft.language, LANGUAGES, "language")
    _validate(draft.schedule, SCHEDULES, "schedule")

    existing = db.scalar(select(Keyword).where(Keyword.term == term))
    if existing is not None:
        if existing.status == "archived":
            # 보관된 것을 되살립니다. 새로 만들면 예전에 이 키워드로 모은
            # 강의와의 연결이 끊깁니다.
            existing.status = "pending"
            db.commit()
            return keyword_out(existing, lecture_counts(db).get(existing.id, 0))
        raise ApiError(409, "KEYWORD_DUPLICATE", f'"{term}" 은(는) 이미 등록되어 있습니다.')

    kw = Keyword(
        term=term,
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
    """수집된 강의는 지우지 않습니다 — 보관 처리입니다."""
    kw = db.get(Keyword, keyword_id)
    if kw is None:
        raise ApiError(404, "KEYWORD_NOT_FOUND", "해당 키워드를 찾을 수 없습니다.")
    kw.status = "archived"
    db.commit()
