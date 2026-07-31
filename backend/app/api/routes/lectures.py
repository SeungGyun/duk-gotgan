"""강의 — docs/API.md §2.

읽기는 전부 `lectures`(정식 층)에서 끝납니다. 임시 층(videos)의 탈락분은 조인
대상이 아니라, UI 쿼리에 상태 조건을 걸 필요가 없습니다.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.serializers import lecture_detail_out, lecture_summary_out
from app.db.models import Evaluation, Lecture, Transcript, Video, VideoKeyword
from app.db.session import get_db

router = APIRouter(prefix="/lectures", tags=["lectures"])

SORTS = {
    "score": (Lecture.expert_score.desc(), Lecture.published_at.desc()),
    "recent": (Lecture.published_at.desc(),),
    "duration": (Lecture.duration_sec.desc(),),
}


class LecturePatch(BaseModel):
    isFavorite: bool | None = None


def _keyword_map(db: Session, video_ids: list[str]) -> dict[str, list[str]]:
    """영상별 키워드 목록. 한 번에 읽어 N+1 을 피합니다."""
    if not video_ids:
        return {}
    rows = db.execute(
        select(VideoKeyword.video_id, VideoKeyword.keyword_id).where(
            VideoKeyword.video_id.in_(video_ids)
        )
    ).all()
    out: dict[str, list[str]] = {}
    for video_id, keyword_id in rows:
        out.setdefault(video_id, []).append(keyword_id)
    return out


@router.get("")
def list_lectures(
    keyword_ids: str | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0, le=100),
    min_duration_sec: int | None = Query(default=None, ge=0),
    max_duration_sec: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None),
    favorites_only: bool = Query(default=False),
    sort: str = Query(default="score"),
    db: Session = Depends(get_db),
):
    if sort not in SORTS:
        raise ApiError(
            400, "INVALID_VALUE", f"sort 값이 올바르지 않습니다. 가능한 값: {', '.join(SORTS)}"
        )

    stmt = select(Lecture).join(Video, Video.id == Lecture.video_id).where(
        Lecture.is_hidden.is_(False)
    )

    if keyword_ids:
        ids = [x.strip() for x in keyword_ids.split(",") if x.strip()]
        if ids:
            # 하나라도 걸리면 통과(OR). EXISTS 로 쓰면 조인 중복이 생기지 않습니다.
            stmt = stmt.where(
                select(VideoKeyword.video_id)
                .where(
                    VideoKeyword.video_id == Lecture.video_id,
                    VideoKeyword.keyword_id.in_(ids),
                )
                .exists()
            )
    if min_score is not None:
        stmt = stmt.where(Lecture.expert_score >= min_score)
    if min_duration_sec is not None:
        stmt = stmt.where(Lecture.duration_sec >= min_duration_sec)
    if max_duration_sec is not None:
        stmt = stmt.where(Lecture.duration_sec <= max_duration_sec)
    if favorites_only:
        stmt = stmt.where(Lecture.is_favorite.is_(True))

    term = (q or "").strip()
    if term:
        # ngram FULLTEXT 는 2글자부터 걸립니다. 한 글자 질의는 매칭이 안 되므로
        # 그때만 LIKE 로 떨어뜨립니다.
        if len(term) >= 2:
            stmt = stmt.where(
                or_(
                    text("MATCH (lectures.search_text) AGAINST (:kw IN BOOLEAN MODE)"),
                    Lecture.search_text.like(f"%{term}%"),
                )
            ).params(kw=f"{term}*")
        else:
            stmt = stmt.where(Lecture.search_text.like(f"%{term}%"))

    stmt = stmt.order_by(*SORTS[sort])

    rows = db.scalars(stmt).unique().all()
    kmap = _keyword_map(db, [r.video_id for r in rows])
    return [lecture_summary_out(r, kmap.get(r.video_id, [])) for r in rows]


@router.get("/{video_id}")
def get_lecture(video_id: str, db: Session = Depends(get_db)):
    lec = db.scalar(
        select(Lecture)
        .where(Lecture.video_id == video_id, Lecture.is_hidden.is_(False))
        # 재요약본이 있으면 최신 것만 보여줍니다
        .order_by(Lecture.version.desc())
        .limit(1)
    )
    if lec is None:
        raise ApiError(404, "LECTURE_NOT_FOUND", "해당 강의를 찾을 수 없습니다.")

    ev = db.scalar(
        select(Evaluation)
        .where(Evaluation.video_id == video_id)
        .order_by(Evaluation.created_at.desc())
        .limit(1)
    )
    transcript = db.get(Transcript, video_id)
    kmap = _keyword_map(db, [video_id])
    return lecture_detail_out(lec, kmap.get(video_id, []), ev, transcript)


@router.patch("/{video_id}", status_code=204)
def patch_lecture(video_id: str, patch: LecturePatch, db: Session = Depends(get_db)):
    if patch.isFavorite is None:
        return
    # 재요약본이 여러 개여도 즐겨찾기는 영상 단위 개념이라 전부 맞춥니다
    updated = db.execute(
        Lecture.__table__.update()
        .where(Lecture.video_id == video_id)
        .values(is_favorite=patch.isFavorite)
    ).rowcount
    if not updated:
        raise ApiError(404, "LECTURE_NOT_FOUND", "해당 강의를 찾을 수 없습니다.")
    db.commit()


__all__ = ["router", "func"]
