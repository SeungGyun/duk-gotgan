"""시간 취급 규칙.

DB(MySQL)는 `--default-time-zone=+09:00` 으로 띄우고, 컬럼은 tz 없는 DATETIME 입니다.
그래서 **저장은 KST naive, 응답은 UTC ISO 8601** 로 통일합니다.
API 계약(docs/API.md)이 `2026-07-31T04:02:00Z` 형태를 요구하기 때문입니다.

naive datetime 을 그냥 isoformat() 하면 "Z" 도 오프셋도 없이 나가서, 브라우저가
로컬 시간으로 읽습니다. KST 값이 9시간 밀려 보이는 흔한 사고라 변환을 한 곳에 모읍니다.
"""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """지금(KST) — tz 정보를 뗀 naive. DB 기본값용."""
    return datetime.now(KST).replace(tzinfo=None)


def to_utc_iso(dt: datetime | None) -> str | None:
    """KST naive → `2026-07-31T04:02:00Z`."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_date_str(dt: datetime | None) -> str | None:
    """날짜만 필요한 곳(`publishedAt`)용 — KST 기준 `YYYY-MM-DD`."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d")
