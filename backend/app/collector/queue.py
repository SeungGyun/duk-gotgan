"""처리 순서 — 키워드끼리 **번갈아** 가져갑니다.

**먼저 온 순서대로 처리하면 안 됩니다.** 실제로 이렇게 됐습니다:

  LLM 서빙 최적화   발견 86  공개 23   ← 가장 먼저 만든 키워드
  과학              발견 64  공개  0
  사이언스           발견 74  공개  0
  피지컬AI          발견 113 공개  0
  ...

전부 발견은 잘 됐는데 공개는 첫 키워드만 됐습니다. 자막과 검토가
`discovered_at` 오름차순이라, 200건짜리 줄의 앞을 첫 키워드가 통째로
차지했기 때문입니다. 받아쓰기가 편당 2~3분이라 줄이 줄어드는 속도보다
새 키워드가 쌓이는 속도가 빨라, 뒤에 선 키워드는 **영원히 차례가 안 옵니다.**

그래서 키워드별로 한 편씩 번갈아 집습니다. 키워드가 열 개면 한 사이클에
열 개 전부 한 편씩은 나아갑니다 — 느리더라도 모두가 움직입니다.

영상 하나에 키워드가 여럿 붙기도 합니다(`과학,사이언스`). 그럴 때는 가장
작은 키워드 id 를 대표로 삼습니다 — 어느 쪽을 고르든 공정성은 같고,
결정적이어야 순서가 흔들리지 않습니다.
"""

import logging
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from config.time import now_kst

logger = logging.getLogger(__name__)

# 키워드별로 순번을 매기고, 순번이 같은 것끼리 먼저 나갑니다.
# 1번째들 → 2번째들 → 3번째들 … 순서라 결과가 자연스럽게 번갈아 나옵니다.
_ROUND_ROBIN = text(
    """
    SELECT v.id
      FROM videos v
      LEFT JOIN (
            SELECT video_id, MIN(keyword_id) AS kw
              FROM video_keywords
             GROUP BY video_id
           ) k ON k.video_id = v.id
     WHERE v.state = :state
     ORDER BY ROW_NUMBER() OVER (
                  PARTITION BY COALESCE(k.kw, '') ORDER BY v.discovered_at
              ),
              v.discovered_at
     LIMIT :lim
    """
)


def next_ids(db: Session, state: str, limit: int) -> list[str]:
    """다음에 처리할 영상 id — 키워드끼리 고르게 섞어서."""
    return list(db.scalars(_ROUND_ROBIN, {"state": state, "lim": limit}).all())


# ── 집기와 놓기 ──────────────────────────────────────────────
#
# **목록을 미리 받아 순회하면 안 됩니다.** `next_ids` 는 잠금 없는 평범한
# SELECT 라, 워커가 둘이면 같은 순간에 **같은 목록**을 받습니다. 그 뒤에
# 상태를 조건 없이 덮어쓰면 먼저 잡힌 것을 알아챌 방법이 없어, 같은 영상을
# 둘이 요약하고 결과가 서로를 덮습니다.
#
# 그래서 한 건씩 조건부 UPDATE 로 집습니다. `WHERE state = :from` 이 붙어
# 있어서 진 쪽은 rowcount 0 을 받습니다 — 경합이 있어도 정확히 한 명만
# 이깁니다. 트랜잭션을 붙들지 않는 것도 중요합니다: 요약 한 건이 몇 분씩
# 걸리는데 `SELECT ... FOR UPDATE` 로 잡으면 그동안 행이 잠깁니다.


def claim(db: Session, video_id: str, *, from_state: str, to_state: str, owner: str) -> bool:
    """영상 하나를 집습니다. 이미 남이 집었으면 False."""
    now = now_kst()
    res = cast(
        CursorResult,
        db.execute(
            text(
                """
                UPDATE videos
                   SET state = :to, state_reason = NULL,
                       claimed_by = :owner, claimed_at = :now, updated_at = :now
                 WHERE id = :id AND state = :frm
                """
            ),
            {"id": video_id, "frm": from_state, "to": to_state, "owner": owner, "now": now},
        ),
    )
    db.commit()
    return res.rowcount == 1


def release(
    db: Session, video_id: str, *, owner: str, to_state: str, reason: str | None = None
) -> bool:
    """붙들고 있던 것을 놓습니다. **내가 아직 쥐고 있을 때만** 씁니다.

    조건 없이 쓰면 안 됩니다. 회수된 뒤 다른 워커가 다시 집어 간 영상을
    뒤늦게 끝난 쪽이 실패로 적어, 지금 잘 돌고 있는 작업을 지웁니다.
    """
    now = now_kst()
    res = cast(
        CursorResult,
        db.execute(
            text(
                """
                UPDATE videos
                   SET state = :to, state_reason = :reason,
                       claimed_by = NULL, claimed_at = NULL, updated_at = :now
                 WHERE id = :id AND claimed_by = :owner
                """
            ),
            {"id": video_id, "to": to_state, "reason": reason, "owner": owner, "now": now},
        ),
    )
    db.commit()
    if res.rowcount != 1:
        logger.warning("[queue] %s 는 이미 내 것이 아닙니다 — 상태를 건드리지 않습니다", video_id)
    return res.rowcount == 1
