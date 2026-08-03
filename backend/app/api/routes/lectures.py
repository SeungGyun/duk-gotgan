"""강의 — docs/API.md §2.

읽기는 전부 `lectures`(정식 층)에서 끝납니다. 임시 층(videos)의 탈락분은 조인
대상이 아니라, UI 쿼리에 상태 조건을 걸 필요가 없습니다.

**보이는 범위는 사람마다 다릅니다.** 요약 본문은 모두가 같은 것을 보지만,
어떤 강의가 목록에 오르는지는 그 사람이 구독한 키워드가 정하고, 읽음·
즐겨찾기·제외는 `user_lectures` 에 따로 쌓입니다.
"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.api.auth import current_user, require_owner
from app.api.errors import ApiError
from app.api.serializers import Marks, lecture_detail_out, lecture_summary_out
from app.collector.channels import consider_block
from app.db.models import (
    Evaluation,
    Lecture,
    Transcript,
    User,
    UserChannelBlock,
    UserKeyword,
    UserLecture,
    Video,
    VideoKeyword,
)
from app.db.session import get_db
from config.time import from_utc_iso, now_kst

router = APIRouter(prefix="/lectures", tags=["lectures"])

# `published_at` 은 **곳간에 들어온 시각**입니다 (영상 공개일이 아니라).
# 같은 실행에서 여러 편이 한꺼번에 올라오면 값이 같아지므로, 뒤에 점수와
# id 를 붙여 순서를 고정합니다 — 안 그러면 새로고침마다 줄이 뒤바뀝니다.
_STABLE = (Lecture.expert_score.desc(), Lecture.id)

# **"최신"은 유튜브에 올라온 날짜입니다** (Video.published_at). 곳간에
# 들어온 날(Lecture.published_at)이 아닙니다 — 어제 수집했더라도 3년 전
# 영상이면 최신이 아니니까요.
_FRESH = Video.published_at.desc()

SORT_KEYS = ("unread", "recent", "added", "score", "duration")

# 기본은 "안 읽은 것부터". 읽은 것이 위에 남아 있으면 새로 온 것을 찾으려고
# 매번 목록을 훑게 됩니다.
DEFAULT_SORT = "unread"


def _sort(name: str, ul) -> tuple:
    """정렬식. **읽음이 사람마다 다르므로** 조인된 별칭을 받아야 합니다.

    `LEFT JOIN` 이라 한 번도 손대지 않은 강의는 `read_at` 이 NULL 로
    나옵니다 — 예전의 "안 읽음" 과 같은 뜻이라 정렬식의 모양은 그대로입니다.
    """
    return {
        # 안 읽은 것이 먼저, 각 묶음 안에서는 유튜브 최신순.
        # MySQL 에서 불리언은 1/0 이라 DESC 면 참(안 읽음)이 앞섭니다.
        "unread": (ul.read_at.is_(None).desc(), _FRESH, *_STABLE),
        "recent": (_FRESH, *_STABLE),
        "added": (Lecture.published_at.desc(), *_STABLE),
        "score": (Lecture.expert_score.desc(), _FRESH, Lecture.id),
        "duration": (Lecture.duration_sec.desc(), *_STABLE),
    }[name]


class LecturePatch(BaseModel):
    isFavorite: bool | None = None
    isRead: bool | None = None
    isExcluded: bool | None = None


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


@dataclass
class Filters:
    """목록과 "새로 온 것" 개수가 **같은 조건**을 봐야 합니다. 조건이 갈리면
    화면에 안 나오는 것을 두고 새로 왔다고 알리게 됩니다."""

    user_id: str
    keyword_ids: str | None = None
    min_score: int | None = None
    min_duration_sec: int | None = None
    max_duration_sec: int | None = None
    q: str | None = None
    favorites_only: bool = False
    # True 면 **제외함**을 봅니다. 목록과 제외함이 같은 함수를 쓰므로
    # 필터가 갈릴 일이 없습니다.
    excluded: bool = False


def _filtered(f: Filters):
    """이 사람에게 보이는 강의. `(Lecture, read_at, is_favorite, excluded_at)` 을 냅니다."""
    ul = aliased(UserLecture)

    stmt = (
        select(Lecture, ul.read_at, ul.is_favorite, ul.excluded_at)
        .join(Video, Video.id == Lecture.video_id)
        # 손댄 적 없는 강의도 목록에 나와야 하므로 바깥 조인입니다.
        .outerjoin(
            ul, and_(ul.user_id == f.user_id, ul.video_id == Lecture.video_id)
        )
        .where(Lecture.is_hidden.is_(False))
    )

    if f.excluded:
        # 제외함은 **구독 여부를 보지 않습니다.** 내가 뺀 것은 그 뒤에
        # 키워드를 끊었더라도 내 제외함에 있어야 되살릴 수 있습니다.
        stmt = stmt.where(ul.excluded_at.isnot(None))
    else:
        stmt = stmt.where(
            ul.excluded_at.is_(None),
            # 내가 구독한 키워드가 데려온 것. **또는 내가 즐겨찾기 한 것** —
            # 이 `OR` 가 없으면 키워드 하나를 끊었을 때 아껴 둔 강의가 통째로
            # 사라집니다. 지운 적이 없는데 없어지는 것이 가장 나쁩니다.
            or_(
                select(VideoKeyword.video_id)
                .join(UserKeyword, UserKeyword.keyword_id == VideoKeyword.keyword_id)
                .where(
                    VideoKeyword.video_id == Lecture.video_id,
                    UserKeyword.user_id == f.user_id,
                    UserKeyword.archived_at.is_(None),
                )
                .exists(),
                ul.is_favorite.is_(True),
            ),
            # 내가 막은 채널은 숨깁니다. **수집을 멈추지는 않습니다** —
            # 그건 모두에게 영향을 주는 결정이라 채널 화면에서 주인이 합니다.
            ~select(UserChannelBlock.channel_id)
            .where(
                UserChannelBlock.user_id == f.user_id,
                UserChannelBlock.channel_id == Video.channel_id,
            )
            .exists(),
        )

    if f.keyword_ids:
        ids = [x.strip() for x in f.keyword_ids.split(",") if x.strip()]
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
    if f.min_score is not None:
        stmt = stmt.where(Lecture.expert_score >= f.min_score)
    if f.min_duration_sec is not None:
        stmt = stmt.where(Lecture.duration_sec >= f.min_duration_sec)
    if f.max_duration_sec is not None:
        stmt = stmt.where(Lecture.duration_sec <= f.max_duration_sec)
    if f.favorites_only:
        stmt = stmt.where(ul.is_favorite.is_(True))

    term = (f.q or "").strip()
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

    return stmt, ul


def _marks(read_at, is_favorite, excluded_at) -> Marks:
    return Marks(
        is_read=read_at is not None,
        is_favorite=bool(is_favorite),
        is_excluded=excluded_at is not None,
    )


@router.get("")
def list_lectures(
    keyword_ids: str | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0, le=100),
    min_duration_sec: int | None = Query(default=None, ge=0),
    max_duration_sec: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None),
    favorites_only: bool = Query(default=False),
    excluded: bool = Query(default=False, description="제외함을 봅니다"),
    sort: str = Query(default=DEFAULT_SORT),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if sort not in SORT_KEYS:
        raise ApiError(
            400,
            "INVALID_VALUE",
            f"sort 값이 올바르지 않습니다. 가능한 값: {', '.join(SORT_KEYS)}",
        )
    stmt, ul = _filtered(
        Filters(
            user.id, keyword_ids, min_score, min_duration_sec, max_duration_sec, q,
            favorites_only, excluded,
        )
    )
    stmt = stmt.order_by(*_sort("added" if excluded else sort, ul))

    rows = db.execute(stmt).unique().all()
    kmap = _keyword_map(db, [r[0].video_id for r in rows])
    return [
        lecture_summary_out(lec, kmap.get(lec.video_id, []), _marks(r, fav, exc))
        for lec, r, fav, exc in rows
    ]


@router.get("/updates")
def count_new(
    since: str = Query(..., description="이 시각 이후에 곳간에 들어온 것만 셉니다 (UTC ISO)"),
    keyword_ids: str | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0, le=100),
    min_duration_sec: int | None = Query(default=None, ge=0),
    max_duration_sec: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None),
    favorites_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """화면을 켜 둔 사이에 새로 들어온 덕질이 몇 편인지.

    **목록 전체를 다시 주지 않습니다.** 1분마다 수십 KB 를 받을 이유가 없고,
    무엇보다 목록을 갈아 끼우면 "안 본 것 먼저" 정렬이 발밑에서 순서를 바꿔
    읽던 글이 화면 밖으로 튑니다. 개수만 알려 주고, 갈아 끼울지는 사용자가
    버튼으로 정합니다.

    기준은 **곳간에 들어온 시각**(lectures.published_at)입니다. 영상 공개일로
    세면 오래된 영상을 새로 수집했을 때 안 세게 됩니다.
    """
    try:
        at = from_utc_iso(since)
    except ValueError as e:
        raise ApiError(400, "INVALID_VALUE", "since 는 ISO 8601 시각이어야 합니다.") from e

    stmt, _ = _filtered(
        Filters(
            user.id, keyword_ids, min_score, min_duration_sec, max_duration_sec, q,
            favorites_only,
        )
    )
    stmt = stmt.where(Lecture.published_at > at)
    n = db.scalar(stmt.with_only_columns(func.count()).order_by(None))
    return {"count": int(n or 0)}


@router.get("/{video_id}")
def get_lecture(
    video_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
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
    mine = db.get(UserLecture, {"user_id": user.id, "video_id": video_id})
    marks = _marks(
        mine.read_at if mine else None,
        mine.is_favorite if mine else False,
        mine.excluded_at if mine else None,
    )
    return lecture_detail_out(lec, kmap.get(video_id, []), ev, transcript, marks)


@router.patch("/{video_id}", status_code=204)
def patch_lecture(
    video_id: str,
    patch: LecturePatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """읽음·즐겨찾기·제외는 **누른 사람 것만** 바뀝니다.

    키가 `video_id` 라서 재요약본이 여러 개여도 한 줄입니다 — 예전에는
    같은 video_id 의 모든 버전을 한꺼번에 고쳐서 이 문제를 피했는데,
    이제 그 손질이 필요 없습니다.
    """
    exists = db.scalar(
        select(func.count()).select_from(Lecture).where(Lecture.video_id == video_id)
    )
    if not exists:
        raise ApiError(404, "LECTURE_NOT_FOUND", "해당 강의를 찾을 수 없습니다.")

    row = db.get(UserLecture, {"user_id": user.id, "video_id": video_id})
    if row is None:
        row = UserLecture(user_id=user.id, video_id=video_id)
        db.add(row)

    if patch.isFavorite is not None:
        row.is_favorite = patch.isFavorite
    if patch.isExcluded is not None:
        row.excluded_at = now_kst() if patch.isExcluded else None
    if patch.isRead is not None:
        # 이미 읽은 것을 다시 읽어도 시각을 덮지 않습니다 — "처음 읽은 때"가
        # 남아야 나중에 "이번 주에 새로 본 것" 같은 걸 셀 수 있습니다.
        if patch.isRead:
            if row.read_at is None:
                row.read_at = now_kst()
        else:
            row.read_at = None

    # 뺀 것이 쌓이면 그 채널 자체가 안 맞는다는 뜻입니다. AI 판정 대신
    # 이 신호로 자동 차단을 판단합니다 — 추정이 아니라 사람이 누른 것이라
    # 훨씬 정확합니다. **누른 사람에게만 적용됩니다.**
    if patch.isExcluded:
        video = db.get(Video, video_id)
        if video is not None:
            db.flush()
            consider_block(db, video, user.id)
    db.commit()


@router.delete("/{video_id}", status_code=204)
def delete_lecture(
    video_id: str, db: Session = Depends(get_db), user: User = Depends(require_owner)
):
    """완전삭제 — 요약을 지웁니다. **주인만.**

    이건 모두에게 영향이 갑니다. 요약 행 하나를 지우면 그것을 구독한 다른
    사람의 곳간에서도 사라지고, 그 사람은 **지운 적이 없는데 없어진 것**을
    보게 됩니다. 되돌릴 방법도 없습니다. 그래서 주인만 누릅니다 — 식구는
    제외함에 두거나 복구하면 됩니다.

    **영상 행은 남깁니다.** `videos` 의 PK 가 유튜브 id 라서, 그 행이
    중복 수집을 막는 장치입니다. 지워 버리면 다음 수집에서 같은 영상을
    처음 본 것처럼 다시 데려와 자막을 받고 AI 를 부릅니다 — 지운 것이
    도로 살아나고 비용까지 다시 듭니다.

    그래서 요약만 지우고 영상은 `EXCLUDED` 로 세워 둡니다. 발견 단계가
    이 상태를 보고 건너뜁니다.
    """
    lectures = db.scalars(select(Lecture).where(Lecture.video_id == video_id)).all()
    if not lectures:
        raise ApiError(404, "LECTURE_NOT_FOUND", "해당 덕질을 찾을 수 없습니다.")
    for lec in lectures:
        db.delete(lec)

    video = db.get(Video, video_id)
    if video is not None:
        video.state = "EXCLUDED"
        video.state_reason = "완전삭제했습니다 — 다시 수집하지 않습니다."
    db.commit()


__all__ = ["router", "func"]
