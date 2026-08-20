"""사람 하나를 지울 때 무엇까지 딸려 나가는가.

라우트에서 떼어 둡니다. 지우는 순서가 한 줄이라도 어긋나면 **남의 곳간이
같이 비는** 종류의 실수라, 규칙을 한곳에 모아 두고 그 자리에서 설명합니다.

세 겹으로 나뉩니다.

  - **그 사람만의 것** — 세션·구독·읽음/즐겨찾기/제외·채널 숨김. 남이 볼
    수 없는 값들이라 사람과 함께 사라집니다.
  - **아무도 안 보게 된 키워드** — 그 사람이 빠진 뒤 **지금 구독하는 사람이
    0명**인 키워드입니다. 남겨 두면 보는 사람이 없는데 매일 수집합니다.
    한 명이라도 남아 있으면 손대지 않습니다 — 지운 적 없는 사람의 곳간이
    말라붙는 것이 가장 나쁩니다.
  - **그 키워드‘만’ 데려온 영상** — 다른 키워드도 데려온 영상은 남깁니다.
    같은 영상을 다른 키워드로 보고 있는 사람이 있고, 그 사람에게는 지운
    적 없는 것이 사라지는 셈이 되기 때문입니다.

**FK 의 ON DELETE CASCADE 에 기대지 않고 직접 지웁니다.** 무엇이 지워지는지가
이 기능의 전부인데, 그것이 테이블 정의에 흩어져 있으면 읽어서 확인할 수가
없습니다. 운영 DB 의 제약이 언제 만들어졌느냐에 따라 결과가 달라지는 것도
곤란합니다.

`pipeline_events` 는 FK 가 아예 없어서(감사 로그라 영상보다 오래 남깁니다)
어차피 여기서 지워 줘야 합니다.

**블로그에 이미 올라간 글은 건드리지 않습니다.** 지우는 것은 곳간 안의
이력(`blog_posts`)뿐이고, 티스토리 쪽 글은 그대로 남습니다 — 곳간 바깥의
것을 조용히 지우는 것은 삭제 버튼 하나가 할 일이 아닙니다.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    BlogPost,
    CrawlRun,
    Evaluation,
    Keyword,
    Lecture,
    PipelineEvent,
    Transcript,
    User,
    UserChannelBlock,
    UserKeyword,
    UserLecture,
    UserSession,
    Video,
    VideoKeyword,
)

logger = logging.getLogger(__name__)


@dataclass
class Removed:
    """지운 뒤에 사람에게 보여 줄 숫자.

    "지웠습니다" 한 줄로 끝내면, 키워드와 강의까지 없어졌다는 것을 나중에
    빈 목록으로 알게 됩니다. 누른 자리에서 바로 말해 주는 편이 낫습니다.
    """

    keywords: int = 0
    lectures: int = 0


def delete_user(db: Session, user: User) -> Removed:
    """사람을 지우고, 아무도 안 보게 된 것까지 정리합니다. **되돌릴 수 없습니다.**

    구독 목록은 지우기 **전에** 챙겨 둡니다 — 사람이 사라지고 나면 무엇을
    보고 있었는지 물어볼 데가 없어집니다. 끊어 둔 것(`archived_at`)까지
    함께 봅니다. 끊었다고 해서 그 키워드를 남이 보고 있다는 뜻은 아니라,
    빼놓으면 아무도 안 보는 키워드가 그대로 남아 매일 돕니다.
    """
    watched = set(
        db.scalars(select(UserKeyword.keyword_id).where(UserKeyword.user_id == user.id)).all()
    )

    for model in (UserSession, UserKeyword, UserLecture, UserChannelBlock):
        db.execute(delete(model).where(model.user_id == user.id))

    # 만든 사람 자리는 비웁니다. 남는 키워드는 임자가 없어져 아무도 못
    # 고치게 되는데, 임자 없는 설정이 모두에게 열려 있는 것보다 낫습니다
    # (models.Keyword.created_by 에 같은 이야기가 있습니다).
    db.execute(update(Keyword).where(Keyword.created_by == user.id).values(created_by=None))

    name = user.name
    db.delete(user)
    db.flush()

    removed = purge_unwatched(db, watched)
    db.commit()
    logger.info(
        "[purge] %s 삭제 — 키워드 %d개, 강의 %d편 함께 지움", name, removed.keywords, removed.lectures
    )
    return removed


def purge_unwatched(db: Session, keyword_ids: set[str]) -> Removed:
    """준 키워드 중 **지금 보는 사람이 0명**인 것과 그 내용을 지웁니다.

    커밋은 하지 않습니다 — 부르는 쪽(사람 삭제)의 트랜잭션 안에 있어야,
    중간에 실패했을 때 "사람은 지워졌는데 키워드는 남은" 상태가 안 생깁니다.
    """
    if not keyword_ids:
        return Removed()

    # 끊어 둔 사람은 세지 않습니다. 세면 아무도 안 읽는 키워드가 남습니다.
    doomed = set(
        db.scalars(
            select(Keyword.id).where(
                Keyword.id.in_(keyword_ids),
                ~select(1)
                .select_from(UserKeyword)
                .where(
                    UserKeyword.keyword_id == Keyword.id,
                    UserKeyword.archived_at.is_(None),
                )
                .exists(),
            )
        ).all()
    )
    if not doomed:
        return Removed()

    lectures = _purge_videos(db, doomed)

    db.execute(delete(VideoKeyword).where(VideoKeyword.keyword_id.in_(doomed)))
    # 남이 끊어 둔(삭제 영역에 있는) 구독도 함께 사라집니다. 되살릴 자리를
    # 남겨 봐야 되살릴 대상이 이미 없습니다.
    db.execute(delete(UserKeyword).where(UserKeyword.keyword_id.in_(doomed)))
    # **실행 이력은 남깁니다.** 어제 유튜브 유닛을 무엇에 썼는지는 키워드가
    # 사라져도 답이 필요한 질문입니다. 이름(label)은 행에 적혀 있습니다.
    db.execute(update(CrawlRun).where(CrawlRun.keyword_id.in_(doomed)).values(keyword_id=None))
    db.execute(delete(Keyword).where(Keyword.id.in_(doomed)))
    db.flush()

    return Removed(keywords=len(doomed), lectures=lectures)


def _purge_videos(db: Session, doomed: set[str]) -> int:
    """지울 키워드‘만’ 데려온 영상을 통째로 지웁니다. 지운 강의 편수를 돌려줍니다.

    **영상 행까지 지웁니다.** 완전삭제(`DELETE /lectures/{id}`)는 영상을
    `EXCLUDED` 로 세워 두는데, 그건 그 키워드가 계속 돌면서 같은 영상을 또
    데려오기 때문입니다. 여기서는 데려온 키워드 자체가 사라지므로 세워 둘
    이유가 없고, 남겨 두면 아무도 안 보는 행만 쌓입니다.
    """
    mine = set(
        db.scalars(select(VideoKeyword.video_id).where(VideoKeyword.keyword_id.in_(doomed))).all()
    )
    if not mine:
        return 0

    shared = set(
        db.scalars(
            select(VideoKeyword.video_id).where(
                VideoKeyword.video_id.in_(mine),
                VideoKeyword.keyword_id.notin_(doomed),
            )
        ).all()
    )
    orphans = mine - shared
    if not orphans:
        return 0

    # 편수는 버전이 아니라 영상으로 셉니다 — 재요약하면 행이 늘지만
    # 사람에게는 한 편입니다.
    lectures = int(
        db.scalar(
            select(func.count(func.distinct(Lecture.video_id))).where(
                Lecture.video_id.in_(orphans)
            )
        )
        or 0
    )

    for model in (Lecture, Transcript, Evaluation, UserLecture, BlogPost, VideoKeyword):
        db.execute(delete(model).where(model.video_id.in_(orphans)))
    # FK 가 없는 유일한 곳입니다 — 여기서 안 지우면 영상 없는 로그가 남습니다.
    db.execute(delete(PipelineEvent).where(PipelineEvent.video_id.in_(orphans)))
    db.execute(delete(Video).where(Video.id.in_(orphans)))
    db.flush()

    return lectures


__all__ = ["Removed", "delete_user", "purge_unwatched"]
