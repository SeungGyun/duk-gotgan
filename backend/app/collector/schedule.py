"""언제 무엇을 돌릴지 — **크론이 아니라 키워드가 판단합니다.**

크론 하나로 "매일 04:00"을 걸면 키워드별 주기를 담을 수 없습니다. `weekly`
키워드는 무슨 요일인지 답이 없고, 나중에 "이 키워드만 정오에"가 오면 받을
방법이 없습니다. 키워드마다 크론을 만들어 붙이는 방식은 등록·수정·삭제 때마다
크론을 붙였다 떼야 해서, 그 동기화가 어긋나는 순간 조용히 안 돌게 됩니다.

그래서 워커가 주기적으로 깨어나 **"지금 돌아야 할 키워드가 있나"** 만 묻습니다.
판단 근거는 전부 `keywords` 행에 있습니다.

  다음 차례 = (마지막 실행일 + 주기) 그날의 run_hour
  due       = 지금 >= 다음 차례

시각을 절대값으로 잡으므로 매일 조금씩 밀리지 않습니다. `last_run_at + 24시간`
으로 하면 실행이 끝나는 데 걸린 시간만큼 매일 뒤로 밀립니다.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Keyword
from config.time import now_kst

# 주기 → 며칠마다
INTERVAL_DAYS = {"daily": 1, "twice_weekly": 3, "weekly": 7}
DEFAULT_INTERVAL = 1


def next_due_at(kw: Keyword) -> datetime | None:
    """이 키워드가 다음에 돌아야 할 시각. 즉시 대상이면 None."""
    if kw.status == "pending" or kw.last_run_at is None:
        return None  # 등록 직후 — 기다리지 않습니다
    days = INTERVAL_DAYS.get(kw.schedule, DEFAULT_INTERVAL)
    base = (kw.last_run_at + timedelta(days=days)).date()
    hour = min(23, max(0, kw.run_hour))
    return datetime(base.year, base.month, base.day, hour)


def is_due(kw: Keyword, now: datetime | None = None) -> bool:
    if kw.status not in ("pending", "active"):
        return False  # paused · quota_wait · archived 는 대상이 아닙니다
    due_at = next_due_at(kw)
    return due_at is None or (now or now_kst()) >= due_at


def due_keywords(db: Session, now: datetime | None = None) -> list[Keyword]:
    """지금 수집해야 할 키워드. pending 을 먼저 돌려줍니다."""
    now = now or now_kst()
    rows = db.scalars(
        select(Keyword).where(Keyword.status.in_(("pending", "active")))
    ).all()
    due = [k for k in rows if is_due(k, now)]
    due.sort(key=lambda k: (k.status != "pending", k.last_run_at or datetime.min))
    return due
