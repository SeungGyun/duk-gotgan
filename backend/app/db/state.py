"""프로세스 밖에 남는 값. 워커와 API 가 같은 것을 봅니다.

**전역 변수로는 안 됩니다.** 워커는 재시작할 때마다 잊고, API 프로세스는
워커가 무엇을 겪었는지 아예 모릅니다. 자막 냉각이 정확히 그래서 깨져
있었습니다 — 백오프는 쌓이지 않고, 화면의 냉각 안내는 뜨지 않았습니다.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import AppState
from config.time import now_kst


def get_time(db: Session, key: str) -> datetime | None:
    row = db.get(AppState, key)
    if row is None or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def set_time(db: Session, key: str, when: datetime | None) -> None:
    row = db.get(AppState, key)
    if row is None:
        row = AppState(key=key)
        db.add(row)
    row.value = when.isoformat() if when else ""
    row.updated_at = now_kst()
    db.commit()
