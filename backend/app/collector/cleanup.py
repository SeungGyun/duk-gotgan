"""보관 정리 — 다 쓴 것을 버립니다.

**자막 원문이 전체의 절반입니다** (실측 55MB 중 31.5MB). 요약이 끝나면
원문은 용량만 차지하고, 남의 저작물을 무기한 쌓아 둘 이유도 없습니다.

  transcripts   31.5MB   ← 여기
  lectures      13.7MB   ← 제품이니 유지
  videos         4.6MB
  그 외          5.2MB

**30일을 기다리는 이유**는 재요약입니다. 프롬프트를 고치거나 실패한 것을
다시 태울 때 원문이 있어야 자막을 새로 받지 않습니다 — 실제로 메모리
부족으로 죽은 60건을 원문 덕에 그냥 되살렸습니다.

**룰 탈락 영상은 지우지 않습니다.** 원래 계획에는 14일 뒤 삭제가 있었는데,
그 행이 "이미 본 영상"이라는 표시입니다. 지우면 다음 검색에 처음 본 것처럼
다시 들어와 또 탈락하는 순환이 생기고, 그러면 영영 줄지 않습니다. 게다가
videos 테이블 전체가 4.6MB 라 얻는 것도 없습니다.
"""

import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import CrawlRun, PipelineEvent, Transcript, Video
from config.time import now_kst

logger = logging.getLogger(__name__)

# 실행 기록과 전이 이력은 화면이 최근 것만 보여 줍니다(실행 50건). 그보다
# 오래된 것은 되짚을 일이 없는데, 영상 한 편마다 몇 줄씩 쌓입니다.
RUN_KEEP_DAYS = 30

# 원문을 지워도 되는 상태. **처리가 끝난 것만** 입니다 — 자막을 기다리거나
# 요약을 기다리는 영상의 원문을 지우면 그 영상은 영영 처리되지 않습니다.
DONE_STATES = ("PUBLISHED", "EXCLUDED", "SKIPPED", "FAILED_REVIEW")


def sweep(db: Session) -> dict:
    """만료된 것을 지웁니다. 지운 수를 돌려줍니다."""
    cut = now_kst() - timedelta(days=RUN_KEEP_DAYS)

    # 1) 만료된 자막 원문 — 처리가 끝난 영상 것만.
    #
    # 상태를 같이 보는 것이 핵심입니다. 만료일만 보고 지우면, 오래 밀려
    # 있던 영상의 원문이 요약 직전에 사라져 그 영상만 영영 처리되지
    # 않습니다. 그런 건 조용히 일어나서 알아채기도 어렵습니다.
    stale = db.scalars(
        select(Transcript.video_id)
        .join(Video, Video.id == Transcript.video_id)
        .where(Transcript.expires_at < now_kst(), Video.state.in_(DONE_STATES))
    ).all()
    if stale:
        db.execute(delete(Transcript).where(Transcript.video_id.in_(stale)))

    # 2) 오래된 전이 이력 — 실행 기록보다 **먼저** 지웁니다.
    #    실행을 먼저 지우면 이력이 사라진 실행을 가리킨 채 남습니다.
    events = db.execute(
        delete(PipelineEvent).where(PipelineEvent.created_at < cut)
    ).rowcount
    runs = db.execute(
        delete(CrawlRun).where(
            CrawlRun.started_at < cut, CrawlRun.status.notin_(("running", "queued"))
        )
    ).rowcount

    db.commit()
    out = {"transcripts": len(stale), "events": events, "runs": runs}
    if any(out.values()):
        logger.info(
            "[cleanup] 자막 원문 %d · 이력 %d · 실행 기록 %d 건 정리",
            out["transcripts"], out["events"], out["runs"],
        )
    return out


def pending(db: Session) -> dict:
    """지금 지울 수 있는 것이 몇 건인지. 화면에 보여 주는 용도."""
    cut = now_kst() - timedelta(days=RUN_KEEP_DAYS)
    return {
        "transcripts": int(
            db.scalar(
                select(func.count())
                .select_from(Transcript)
                .join(Video, Video.id == Transcript.video_id)
                .where(Transcript.expires_at < now_kst(), Video.state.in_(DONE_STATES))
            )
            or 0
        ),
        "events": int(
            db.scalar(
                select(func.count())
                .select_from(PipelineEvent)
                .where(PipelineEvent.created_at < cut)
            )
            or 0
        ),
    }
