"""유튜브 쿼터 가드.

무료 할당량은 하루 10,000유닛인데 `search.list` 한 번이 100유닛입니다.
**하루에 가능한 검색은 100회**가 전부라, 이 파일이 사실상 수집량의 상한을
정합니다.

가드를 호출 *전에* 겁니다. 쓰고 나서 재면 이미 늦습니다 — 할당량을 넘긴
요청은 403 으로 돌아오고, 그날 남은 수집이 통째로 멈춥니다.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UsageLedger
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

# YouTube Data API v3 호출별 유닛 (공식 문서 기준)
UNITS_SEARCH = 100
UNITS_VIDEOS = 1

# 상한에 딱 붙여 쓰지 않습니다. 다른 곳에서 몇 번 더 부르거나 재시도가
# 겹치면 그날 마지막 몇 건이 403 으로 깨지는데, 그 손해가 남겨둔 여유보다 큽니다.
SAFETY_MARGIN = 0.8


class QuotaExceeded(Exception):
    """오늘 쓸 수 있는 유닛이 모자랍니다. 내일 다시 시도합니다."""

    def __init__(self, used: int, limit: int, need: int):
        self.used, self.limit, self.need = used, limit, need
        super().__init__(
            f"유튜브 검색 할당량이 부족합니다 — 오늘 {used}/{limit} 유닛 사용, "
            f"{need} 유닛이 더 필요합니다. 내일 자동으로 재개됩니다."
        )


def _today_row(db: Session, day: date | None = None) -> UsageLedger:
    """오늘 행을 잠급니다. 없으면 만듭니다.

    `SELECT ... FOR UPDATE` 로 잠그는 이유: 워커가 둘 이상이면 둘 다
    "아직 여유 있음"을 읽고 둘 다 호출해 상한을 넘길 수 있습니다.
    """
    day = day or now_kst().date()
    row = db.scalar(select(UsageLedger).where(UsageLedger.day == day).with_for_update())
    if row is None:
        row = UsageLedger(day=day)
        db.add(row)
        db.flush()
    return row


def check(db: Session, units: int) -> None:
    """유닛을 쓸 수 있는지 확인만 합니다. 모자라면 QuotaExceeded."""
    row = _today_row(db)
    budget = int(settings.youtube_unit_limit * SAFETY_MARGIN)
    if row.youtube_units + units > budget:
        raise QuotaExceeded(row.youtube_units, budget, units)


def spend(db: Session, units: int) -> int:
    """확인하고 차감한 뒤 **바로 커밋합니다.** 호출 직전에 씁니다.

    커밋을 미루지 않는 이유: 유닛은 우리 트랜잭션이 아니라 **구글 쪽에서**
    깎입니다. 뒤에서 적재가 실패해 롤백하면 장부만 되돌아가고 실제 소비는
    그대로 남아, 다음 호출이 상한을 넘겨 403 을 맞습니다. 조금 넘겨 세는
    쪽이 모자라게 세는 쪽보다 훨씬 쌉니다.
    """
    row = _today_row(db)
    budget = int(settings.youtube_unit_limit * SAFETY_MARGIN)
    if row.youtube_units + units > budget:
        raise QuotaExceeded(row.youtube_units, budget, units)
    row.youtube_units += units
    db.commit()
    left = budget - row.youtube_units
    logger.info("[quota] -%d units (오늘 %d/%d, 남음 %d)", units, row.youtube_units, budget, left)
    return left


def remaining(db: Session) -> int:
    row = _today_row(db)
    return max(0, int(settings.youtube_unit_limit * SAFETY_MARGIN) - row.youtube_units)


def searches_left(db: Session) -> int:
    """오늘 남은 검색 횟수 — 운영자가 실제로 궁금해하는 단위."""
    return remaining(db) // UNITS_SEARCH
