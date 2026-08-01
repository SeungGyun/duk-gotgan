"""운영 지표 — docs/API.md §3.

수집 파이프라인이 아직 없으므로 대부분 0 입니다. 0 을 감추려고 값을 지어내지
않습니다 — UI 는 0 을 받으면 해당 칩·미터를 숨기도록 만들어져 있습니다.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.serializers import run_out
from app.db.models import CrawlRun, Keyword, Lecture, UsageLedger, Video, VideoKeyword
from app.db.session import get_db
from config.settings import settings
from config.time import KST, now_kst, to_utc_iso

router = APIRouter(tags=["stats"])

# 파이프라인 상태 → 퍼널 단계
_TRANSCRIBED_STATES = ("TRANSCRIBED", "REVIEWING", "PUBLISHED")
_REVIEWED_STATES = ("PUBLISHED", "REJECTED")


@router.get("/stats/overview")
def overview(db: Session = Depends(get_db)):
    today = now_kst().date()
    day_start = _midnight(today)
    week_start = _midnight(today - timedelta(days=7))

    published = db.scalar(
        select(func.count()).select_from(Lecture).where(Lecture.is_hidden.is_(False))
    )
    new_today = db.scalar(
        select(func.count())
        .select_from(Lecture)
        .where(Lecture.is_hidden.is_(False), Lecture.published_at >= day_start)
    )
    week_added = db.scalar(
        select(func.count())
        .select_from(Lecture)
        .where(Lecture.is_hidden.is_(False), Lecture.published_at >= week_start)
    )
    avg_score = db.scalar(
        select(func.avg(Lecture.expert_score)).where(Lecture.is_hidden.is_(False))
    )

    # 오늘 발견된 영상 기준 퍼널
    discovered = _count_videos(db, day_start)
    rule_passed = _count_videos(db, day_start, exclude_state="DISCOVERED")
    transcribed = _count_videos(db, day_start, states=_TRANSCRIBED_STATES)
    reviewed = _count_videos(db, day_start, states=_REVIEWED_STATES)
    published_today = _count_videos(db, day_start, states=("PUBLISHED",))

    ledger = db.get(UsageLedger, today)

    contributions = db.execute(
        select(Keyword.id, Keyword.term, func.count(func.distinct(Lecture.video_id)))
        .join(VideoKeyword, VideoKeyword.keyword_id == Keyword.id)
        .join(Lecture, Lecture.video_id == VideoKeyword.video_id)
        .where(Lecture.is_hidden.is_(False), Lecture.published_at >= day_start)
        .group_by(Keyword.id, Keyword.term)
        .order_by(func.count(func.distinct(Lecture.video_id)).desc())
        .limit(8)
    ).all()

    last_run = db.scalar(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(1))

    return {
        "newToday": new_today or 0,
        "totalLectures": published or 0,
        "weekAdded": week_added or 0,
        "avgScore": round(float(avg_score)) if avg_score is not None else 0,
        "queued": {
            "transcript": _count_videos(db, states=("RULE_PASSED",)),
            "review": _count_videos(db, states=("TRANSCRIBED", "REVIEWING")),
        },
        "funnel": {
            "discovered": discovered,
            "rulePassed": rule_passed,
            "transcribed": transcribed,
            "reviewed": reviewed,
            "published": published_today,
        },
        "earlyExitCount": ledger.early_exit_count if ledger else 0,
        "earlyExitSavedInputTokens": ledger.early_exit_saved_input_tokens if ledger else 0,
        "contributions": [
            {"keywordId": kid, "term": term, "published": cnt} for kid, term, cnt in contributions
        ],
        "failures": _failures(db),
        "lastRunAt": to_utc_iso(last_run.started_at) if last_run else None,
    }


@router.get("/stats/usage")
def usage(db: Session = Depends(get_db)):
    today = now_kst().date()
    ledger = db.get(UsageLedger, today)
    return {
        "inputTokens": ledger.input_tokens if ledger else 0,
        "outputTokens": ledger.output_tokens if ledger else 0,
        "dailyLimitTokens": settings.daily_token_limit or None,
        "youtubeUnits": ledger.youtube_units if ledger else 0,
        "youtubeUnitLimit": settings.youtube_unit_limit,
        # 유튜브 쿼터는 태평양 표준시 자정에 리셋됩니다. 우리 집계는 KST 날짜
        # 기준이므로, 여기서는 "다음 KST 자정"을 알려 줍니다.
        "resetsAt": to_utc_iso(_midnight(today + timedelta(days=1))),
    }


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    rows = db.scalars(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(50)).all()
    return [run_out(r) for r in rows]


@router.post("/runs", status_code=202)
def request_run(db: Session = Depends(get_db)):
    """"지금 실행" — **요청만 남깁니다.** 워커가 다음 틱에 집어갑니다.

    여기서 직접 돌리지 않는 이유: 한 사이클이 몇 분씩 걸려서 HTTP 요청이
    그동안 매달려 있게 되고, 브라우저가 먼저 끊으면 진행 상황을 알 수
    없습니다. 요청을 기록으로 남기면 실행 로그에 바로 보이고, 워커가
    집어가면서 상태가 이어집니다.
    """
    waiting = db.scalar(select(CrawlRun).where(CrawlRun.status == "queued"))
    if waiting is not None:
        raise ApiError(409, "RUN_ALREADY_QUEUED", "이미 실행을 기다리는 요청이 있습니다.")

    run = CrawlRun(
        trigger="manual",
        status="queued",
        started_at=now_kst(),
        label="실행 대기 중",
        stats={},
    )
    db.add(run)
    db.commit()
    return run_out(run)


# ── 내부 ──────────────────────────────────────────────────


def _midnight(d: date):
    from datetime import datetime

    return datetime(d.year, d.month, d.day)


def _count_videos(db: Session, since=None, states=None, exclude_state=None) -> int:
    stmt = select(func.count()).select_from(Video)
    if since is not None:
        stmt = stmt.where(Video.discovered_at >= since)
    if states is not None:
        stmt = stmt.where(Video.state.in_(states))
    if exclude_state is not None:
        stmt = stmt.where(Video.state != exclude_state)
    return db.scalar(stmt) or 0


def _failures(db: Session) -> list[dict]:
    """최근 실패. 사용자에게 보여줄 문장은 state_reason 에 이미 사람 말로 들어 있습니다."""
    rows = db.scalars(
        select(Video)
        .where(Video.state == "FAILED")
        .order_by(Video.updated_at.desc())
        .limit(5)
    ).all()
    out = []
    for v in rows:
        reason = v.state_reason or ""
        kind = "transcript" if "자막" in reason else "review"
        out.append(
            {
                "kind": kind,
                "label": "자막 없음" if kind == "transcript" else "검토 실패",
                "title": v.title,
                "detail": reason,
            }
        )
    return out


__all__ = ["router", "KST"]
