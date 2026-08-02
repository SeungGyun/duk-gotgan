"""채널 차단 — 검색이 계속 물어오는 엉뚱한 채널을 걸러냅니다.

검색어를 다듬는 방법은 실패했습니다. 실측(2026-08-01)에서 `"결제 시스템"`
따옴표는 거의 차이가 없었고, `-노코드 -쇼핑몰` 같은 제외 연산자는 **관련도
랭킹 자체를 무너뜨려** 상위가 "BRICS 디지털 통화", "60대 이후 하면 안 되는
3가지"로 뒤덮였습니다.

그래서 **AI 가 이미 내린 판정을 재사용합니다.** 판정은 어차피 하고 있고,
그 결과를 버리지 않고 채널 단위로 쌓으면 다음 수집부터 룰 단계에서 걸립니다.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ChannelBlock, Lecture, Video
from config.time import now_kst

logger = logging.getLogger(__name__)

# 같은 채널을 이만큼 빼면 자동으로 막습니다.
#
# 세 번인 이유: 한두 번은 그 채널의 특정 영상이 안 맞았을 뿐일 수 있지만,
# 세 번이면 채널 자체가 안 맞는 것입니다. 키워드 화면에서 언제든 풀 수
# 있으므로 지나치게 신중할 이유는 없습니다.
EXCLUDE_LIMIT = 3



def is_blocked(db: Session, channel_id: str | None) -> ChannelBlock | None:
    """차단 중인 것만. 해제된 행은 None 을 돌려줍니다."""
    if not channel_id:
        return None
    row = db.get(ChannelBlock, channel_id)
    return row if row is not None and row.active else None


def has_record(db: Session, channel_id: str | None) -> bool:
    """해제된 것까지 포함해 이력이 있는가. 자동 차단을 막는 근거입니다."""
    return bool(channel_id) and db.get(ChannelBlock, channel_id) is not None


def blocked_ids(db: Session) -> set[str]:
    """룰 필터가 한 번에 읽어 갑니다 — 후보마다 조회하면 N+1 입니다."""
    return set(
        db.scalars(select(ChannelBlock.channel_id).where(ChannelBlock.active.is_(True))).all()
    )


def consider_block(db: Session, video: Video) -> ChannelBlock | None:
    """제외 하나가 끝날 때마다 이 채널을 막을지 다시 봅니다.

    **한 편이라도 남겨 둔 채널은 막지 않습니다.** 좋은 영상을 내는 채널도
    가끔 주제에서 벗어난 것을 올리는데, 그걸로 막으면 이후의 좋은 것까지
    통째로 놓칩니다.
    """
    # 해제된 채널도 건너뜁니다 — 사용자가 "괜찮다"고 한 것을 되막지 않습니다.
    if not video.channel_id or has_record(db, video.channel_id):
        return None

    def _count(*where):
        return (
            db.scalar(
                select(func.count())
                .select_from(Lecture)
                .join(Video, Video.id == Lecture.video_id)
                .where(
                    Video.channel_id == video.channel_id,
                    Lecture.is_hidden.is_(False),
                    *where,
                )
            )
            or 0
        )

    if _count(Lecture.excluded_at.is_(None)):
        return None  # 남겨 둔 것이 있습니다 — 막지 않습니다

    excluded = _count(Lecture.excluded_at.isnot(None))
    if excluded < EXCLUDE_LIMIT:
        return None

    block = ChannelBlock(
        channel_id=video.channel_id,
        channel_title=video.channel_title or "",
        reason=f"{excluded}편을 빼고 한 편도 남기지 않았습니다.",
        auto=True,
        active=True,
        created_at=now_kst(),
    )
    db.add(block)
    logger.info("[channels] 자동 차단 — %s (%d편 제외)", video.channel_title, excluded)
    return block
