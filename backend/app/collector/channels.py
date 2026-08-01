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

from app.db.models import ChannelBlock, Evaluation, Lecture, Video
from config.time import now_kst

logger = logging.getLogger(__name__)

# 차단 기준이 두 겹입니다.
#
# 무관·홍보는 **다른 분야**라는 뜻이라 두 번이면 충분합니다. 반면
# introductory(주제는 맞지만 얕음)는 그 채널이 언젠가 깊은 걸 낼 수도 있어서
# 더 참습니다 — 다만 계속 떨어지면 AI 호출만 태우므로 세 번에서 끊습니다.
#
# 실측 사례: "슬기로운 스테이블코인 생활" 은 코인 뉴스 채널인데 세 번 걸렸고
# 그중 둘이 introductory 였습니다. 좁은 기준으로는 영영 안 막혔을 겁니다.
STRIKE_LIMIT = 2
ANY_REJECT_LIMIT = 3

# 이 아래면 "검색어와 무관"으로 봅니다 (AI 가 매긴 0~100)
IRRELEVANT_BELOW = 40

# 이 판정들은 관련도와 무관하게 스트라이크입니다
BAD_VERDICTS = ("promotional", "irrelevant")


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


def consider_block(db: Session, video: Video, ev: Evaluation) -> ChannelBlock | None:
    """판정 하나가 끝날 때마다 이 채널을 막을지 다시 봅니다.

    **한 번이라도 공개된 강의를 낸 채널은 막지 않습니다.** 좋은 강의를 내는
    채널도 가끔 주제에서 벗어난 영상을 올리는데, 그걸로 막아버리면 이후의
    좋은 강의까지 통째로 놓칩니다.
    """
    # 해제된 채널도 건너뜁니다 — 사용자가 "괜찮다"고 한 것을 되막지 않습니다.
    if not video.channel_id or has_record(db, video.channel_id):
        return None

    published = db.scalar(
        select(func.count())
        .select_from(Lecture)
        .join(Video, Video.id == Lecture.video_id)
        .where(Video.channel_id == video.channel_id, Lecture.is_hidden.is_(False))
    )
    if published:
        return None  # 이 채널은 좋은 것도 냅니다 — 막지 않습니다

    def _count(condition=None):
        stmt = (
            select(func.count())
            .select_from(Evaluation)
            .join(Video, Video.id == Evaluation.video_id)
            .where(Video.channel_id == video.channel_id)
        )
        return db.scalar(stmt.where(condition) if condition is not None else stmt) or 0

    off_topic = _count(
        (Evaluation.verdict.in_(BAD_VERDICTS))
        | (Evaluation.keyword_relevance < IRRELEVANT_BELOW)
    )
    rejected = _count()

    if off_topic >= STRIKE_LIMIT:
        why = f"검색과 무관하거나 홍보인 영상이 {off_topic}번 걸렸습니다"
    elif rejected >= ANY_REJECT_LIMIT:
        why = f"{rejected}번 검토했지만 한 번도 기준을 넘지 못했습니다"
    else:
        return None

    block = ChannelBlock(
        channel_id=video.channel_id,
        channel_title=video.channel_title,
        reason=f"{why} (최근: {ev.topic or ev.verdict})",
        auto=True,
        created_at=now_kst(),
    )
    db.add(block)
    logger.info("[channels] 자동 차단 — %s · %s", video.channel_title, why)
    return block
