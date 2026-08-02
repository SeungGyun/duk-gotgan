"""대기 목록 — 앞으로 처리할 영상을 미리 봅니다.

**처리 전에 빼는 것이 이 화면의 값어치입니다.** 한 편당 받아쓰기 2~7분에
검토 6~8만 토큰이 듭니다. 제목만 봐도 아닌 것이 보이면, 일이 벌어지기
전에 빼는 편이 요약을 만들어 놓고 제외하는 것보다 훨씬 쌉니다.

순서는 지어내지 않습니다 — `queue.next_ids()` 가 워커와 **같은 함수**라,
여기 보이는 차례가 실제 처리 차례입니다. 화면과 동작이 갈리면 미리 보는
의미가 없습니다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.collector import queue as q
from app.db.models import Keyword, PipelineEvent, Video, VideoKeyword
from app.db.session import get_db
from config.settings import settings
from config.time import to_utc_iso

router = APIRouter(tags=["queue"])

# 한 칸에서 미리 보여 줄 최대 편수. 106건을 전부 그리면 화면이 목록이
# 아니라 벽이 됩니다. 총 편수는 따로 알려 주므로 정보가 사라지지는 않습니다.
PREVIEW = 40

# 처리 전에 뺀 것. 완전삭제(EXCLUDED)와 **다릅니다** — 저건 다시는 손대지
# 않겠다는 뜻이고, 이건 이번엔 넘어가지만 되돌릴 수 있다는 뜻입니다.
SKIPPED = "SKIPPED"

STAGES = [
    ("review", "검토 대기", "TRANSCRIBED"),
    ("transcript", "자막 대기", "TRANSCRIPT_PENDING"),
    ("discovered", "발견 — 다음 수집에서 올라감", "DISCOVERED"),
]


def _rows(db: Session, video_ids: list[str]) -> dict[str, list[str]]:
    if not video_ids:
        return {}
    # **지운 키워드도 보여 줍니다.** 이 줄의 목적은 "왜 이게 여기 있나"라
    # 답하는 것인데, 지운 키워드가 데려온 영상이 이름 없이 뜨면 답이 안
    # 됩니다. 지운 것은 괄호로 표시해 지금 기준과 구분합니다.
    out: dict[str, list[str]] = {}
    for vid, term, title, archived in db.execute(
        select(
            VideoKeyword.video_id, Keyword.term, Keyword.channel_title, Keyword.archived_at
        )
        .join(Keyword, Keyword.id == VideoKeyword.keyword_id)
        .where(VideoKeyword.video_id.in_(video_ids))
    ).all():
        name = title or term
        out.setdefault(vid, []).append(f"({name})" if archived else name)
    return out


def _item(v: Video, kws: dict[str, list[str]], order: int | None = None) -> dict:
    return {
        "videoId": v.id,
        "title": v.title,
        "channelTitle": v.channel_title,
        "durationSec": v.duration_sec,
        "publishedAt": to_utc_iso(v.published_at),
        "keywords": kws.get(v.id, []),
        "order": order,
        "reason": v.state_reason or "",
    }


@router.get("/queue")
def get_queue(db: Session = Depends(get_db)):
    stages = []
    for key, label, state in STAGES:
        total, raw = db.execute(
            select(func.count(), func.coalesce(func.sum(Video.duration_sec), 0)).where(
                Video.state == state
            )
        ).one()
        # MySQL 의 SUM 은 Decimal 로 옵니다. 그대로 float 와 나누면 터집니다.
        total, secs = int(total), int(raw or 0)

        # 자막·검토는 번갈아 처리하므로 그 순서를 그대로 씁니다. 발견은
        # 다음 수집 때 키워드별 상한에 따라 올라가서 차례가 정해지지
        # 않습니다 — 최신순으로만 보여 줍니다.
        if state in ("TRANSCRIBED", "TRANSCRIPT_PENDING"):
            ids = q.next_ids(db, state, PREVIEW)
        else:
            ids = list(
                db.scalars(
                    select(Video.id)
                    .where(Video.state == state)
                    .order_by(Video.discovered_at.desc())
                    .limit(PREVIEW)
                ).all()
            )

        found = {v.id: v for v in db.scalars(select(Video).where(Video.id.in_(ids))).all()}
        kws = _rows(db, ids)
        stages.append(
            {
                "key": key,
                "label": label,
                "count": total,
                "totalSec": secs,
                # 받아쓰기만 시간을 어림합니다. 검토는 자막 길이가 아니라
                # 토큰 수에 좌우돼서 영상 길이로 재면 틀립니다.
                "etaSec": int(secs / settings.asr_realtime_factor)
                if key == "transcript"
                else None,
                "items": [
                    _item(found[i], kws, order=n)
                    for n, i in enumerate(ids, 1)
                    if i in found
                ],
            }
        )

    skipped = db.scalars(
        select(Video).where(Video.state == SKIPPED).order_by(Video.updated_at.desc()).limit(100)
    ).all()
    return {
        "stages": stages,
        "skipped": [_item(v, _rows(db, [v.id for v in skipped])) for v in skipped],
        # 화면이 "5배속 기준"이라고 말할 수 있게 근거를 같이 보냅니다.
        "asrRealtimeFactor": settings.asr_realtime_factor,
    }


@router.post("/queue/{video_id}/skip", status_code=204)
def skip(video_id: str, db: Session = Depends(get_db)):
    """처리 전에 뺍니다. 되돌릴 수 있습니다."""
    v = db.get(Video, video_id)
    if v is None:
        raise ApiError(404, "VIDEO_NOT_FOUND", "해당 영상을 찾을 수 없습니다.")
    if v.state == SKIPPED:
        return
    if v.state not in {s for _, _, s in STAGES}:
        raise ApiError(
            409, "NOT_IN_QUEUE", "이미 처리됐거나 처리 중인 영상입니다."
        )

    # **어디로 되돌릴지는 이력에 남깁니다.** 컬럼을 새로 만들 필요가 없고,
    # 어차피 남기고 있는 기록이라 되돌리기가 공짜가 됩니다.
    db.add(
        PipelineEvent(
            video_id=v.id,
            from_state=v.state,
            to_state=SKIPPED,
            stage="skip",
            ok=True,
            detail={"reason": "대기 목록에서 미리 뺐습니다."},
        )
    )
    v.state = SKIPPED
    v.state_reason = "대기 목록에서 미리 뺐습니다 — 되돌릴 수 있습니다."
    db.commit()


@router.post("/queue/{video_id}/restore", status_code=204)
def restore(video_id: str, db: Session = Depends(get_db)):
    """뺀 것을 원래 줄로 돌려놓습니다."""
    v = db.get(Video, video_id)
    if v is None or v.state != SKIPPED:
        raise ApiError(404, "NOT_SKIPPED", "빼 둔 영상이 아닙니다.")

    last = db.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.video_id == video_id, PipelineEvent.to_state == SKIPPED)
        .order_by(PipelineEvent.created_at.desc())
    ).first()
    # 이력이 없으면 자막 대기로 보냅니다 — 자막이 있으면 다음 사이클이
    # 곧바로 검토로 올리므로, 잘못 돌려놔도 한 칸 늦어질 뿐입니다.
    back = (last.from_state if last else None) or "TRANSCRIPT_PENDING"
    v.state = back
    v.state_reason = None
    db.add(
        PipelineEvent(
            video_id=v.id, from_state=SKIPPED, to_state=back, stage="skip", ok=True,
            detail={"reason": "대기 목록으로 되돌렸습니다."},
        )
    )
    db.commit()
