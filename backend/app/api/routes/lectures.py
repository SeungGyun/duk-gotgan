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
from config.time import now_kst

router = APIRouter(prefix="/lectures", tags=["lectures"])

# `published_at` 은 **곳간에 들어온 시각**입니다 (영상 공개일이 아니라).
# 같은 실행에서 여러 편이 한꺼번에 올라오면 값이 같아지므로, 뒤에 점수와
# id 를 붙여 순서를 고정합니다 — 안 그러면 새로고침마다 줄이 뒤바뀝니다.
_STABLE = (Lecture.expert_score.desc(), Lecture.id)

# **"최신"은 유튜브에 올라온 날짜입니다** (Video.published_at). 곳간에
# 들어온 날(Lecture.published_at)이 아닙니다 — 어제 수집했더라도 3년 전
# 영상이면 최신이 아니니까요.
_FRESH = Video.published_at.desc()

SORTS = {
    # 안 읽은 것이 먼저, 각 묶음 안에서는 유튜브 최신순.
    # MySQL 에서 불리언은 1/0 이라 DESC 면 참(안 읽음)이 앞섭니다.
    "unread": (Lecture.read_at.is_(None).desc(), _FRESH, *_STABLE),
    "recent": (_FRESH, *_STABLE),
    "added": (Lecture.published_at.desc(), *_STABLE),
    "score": (Lecture.expert_score.desc(), _FRESH, Lecture.id),
    "duration": (Lecture.duration_sec.desc(), *_STABLE),
}

# 기본은 "안 읽은 것부터". 읽은 것이 위에 남아 있으면 새로 온 것을 찾으려고
# 매번 목록을 훑게 됩니다.
DEFAULT_SORT = "unread"


class LecturePatch(BaseModel):
    isFavorite: bool | None = None
    isRead: bool | None = None


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
    sort: str = Query(default=DEFAULT_SORT),
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
    values: dict = {}
    if patch.isFavorite is not None:
        values["is_favorite"] = patch.isFavorite
    if patch.isRead is not None:
        # 이미 읽은 것을 다시 읽어도 시각을 덮지 않습니다 — "처음 읽은 때"가
        # 남아야 나중에 "이번 주에 새로 본 것" 같은 걸 셀 수 있습니다.
        values["read_at"] = now_kst() if patch.isRead else None
    if not values:
        return

    # 재요약본이 여러 개여도 읽음·즐겨찾기는 영상 단위 개념이라 전부 맞춥니다
    where = [Lecture.video_id == video_id]
    if "read_at" in values and patch.isRead:
        where.append(Lecture.read_at.is_(None))
    updated = db.execute(
        Lecture.__table__.update().where(*where).values(**values)
    ).rowcount
    if not updated and patch.isRead:
        return  # 이미 읽음으로 되어 있습니다 — 실패가 아닙니다
    if not updated:
        raise ApiError(404, "LECTURE_NOT_FOUND", "해당 강의를 찾을 수 없습니다.")
    db.commit()


__all__ = ["router", "func"]
