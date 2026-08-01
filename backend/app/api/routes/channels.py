"""차단한 채널 — 목록·해제·직접 차단.

자동 차단은 판정 이력으로 굴러가지만 **오판할 수 있습니다.** 되돌릴 방법이
DB 뿐이면 곤란하므로 화면에서 풀 수 있게 합니다. 반대로 AI 를 세 번 태우기
전에 미리 막고 싶은 채널도 있어서, 직접 추가하는 길도 둡니다.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.collector.youtube import YouTubeError, resolve_channel
from app.db.models import ChannelBlock, Evaluation, Video
from app.db.session import get_db
from config.time import to_utc_iso

router = APIRouter(prefix="/channels", tags=["channels"])


class BlockDraft(BaseModel):
    """`@핸들` 로 직접 막습니다."""

    handle: str
    reason: str = ""


def _out(b: ChannelBlock, rejected: int = 0) -> dict:
    return {
        "channelId": b.channel_id,
        "channelTitle": b.channel_title,
        "reason": b.reason,
        "auto": bool(b.auto),
        "rejectedCount": rejected,
        "createdAt": to_utc_iso(b.created_at),
    }


@router.get("/blocks")
def list_blocks(db: Session = Depends(get_db)):
    blocks = db.scalars(
        select(ChannelBlock)
        .where(ChannelBlock.active.is_(True))
        .order_by(ChannelBlock.created_at.desc())
    ).all()
    if not blocks:
        return []
    # 채널별 탈락 횟수 — "왜 막혔는지"의 근거를 화면에서 같이 보여줍니다
    counts = dict(
        db.execute(
            select(Video.channel_id, func.count())
            .join(Evaluation, Evaluation.video_id == Video.id)
            .where(Video.channel_id.in_([b.channel_id for b in blocks]))
            .group_by(Video.channel_id)
        ).all()
    )
    return [_out(b, counts.get(b.channel_id, 0)) for b in blocks]


@router.post("/blocks", status_code=201)
def add_block(draft: BlockDraft, db: Session = Depends(get_db)):
    handle = draft.handle.strip()
    if not handle:
        raise ApiError(400, "HANDLE_REQUIRED", "채널 핸들(@이름)을 입력해 주세요.")

    try:
        info = resolve_channel(handle)
    except YouTubeError as e:
        raise ApiError(404, "CHANNEL_NOT_FOUND", str(e)) from e

    block = db.get(ChannelBlock, info.channel_id)
    if block is not None and block.active:
        raise ApiError(409, "ALREADY_BLOCKED", f"{info.title} 은(는) 이미 차단되어 있습니다.")
    if block is None:
        block = ChannelBlock(channel_id=info.channel_id)
        db.add(block)
    block.channel_title = info.title
    block.reason = draft.reason.strip() or "직접 차단했습니다."
    block.auto = False
    block.active = True
    db.commit()
    return _out(block)


@router.delete("/blocks/{channel_id}", status_code=204)
def remove_block(channel_id: str, db: Session = Depends(get_db)):
    """차단을 풉니다.

    판정 이력은 지우지 않습니다 — 지우면 다음 탈락 때 처음부터 세기 시작해
    같은 채널이 또 자동 차단됩니다. 대신 해제한 채널은 자동 차단 대상에서
    빠지도록 `auto=False` 로 남겨 둡니다.
    """
    block = db.get(ChannelBlock, channel_id)
    if block is None or not block.active:
        raise ApiError(404, "BLOCK_NOT_FOUND", "차단 목록에 없는 채널입니다.")
    block.active = False
    block.reason = "사용자가 차단을 풀었습니다 — 다시 자동 차단하지 않습니다."
    db.commit()
