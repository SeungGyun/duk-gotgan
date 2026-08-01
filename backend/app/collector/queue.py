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

from sqlalchemy import text
from sqlalchemy.orm import Session

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
