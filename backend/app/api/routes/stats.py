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
from app.collector import transcript
from app.db.models import (
    CrawlRun,
    Keyword,
    Lecture,
    PipelineEvent,
    UsageLedger,
    Video,
    VideoKeyword,
)
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


# 파이프라인 각 칸에 지금 몇 개가 서 있는지. **"기다리면 되는가 손대야
# 하는가"를 가르는 화면의 근거**입니다. 실행 기록만 봐서는 알 수 없습니다 —
# 기록은 지나간 일이고, 사용자가 궁금한 것은 지금 상태니까요.
_STAGES = [
    ("discovered", "발견", ("DISCOVERED",)),
    ("transcript", "자막 대기", ("TRANSCRIPT_PENDING",)),
    ("review", "검토 대기", ("TRANSCRIBED",)),
    ("working", "처리 중", ("TRANSCRIBING", "REVIEWING")),
    ("published", "공개", ("PUBLISHED",)),
]
# 손을 봐야 하는 것들. 위 흐름과 섞으면 "막힌 것"이 흐름 속에 묻힙니다.
_STUCK = [
    ("failedTranscript", "자막 실패", ("FAILED_TRANSCRIPT", "FAILED")),
    ("failedReview", "검토 실패", ("FAILED_REVIEW",)),
]


@router.get("/stats/pipeline")
def pipeline(db: Session = Depends(get_db)):
    """지금 파이프라인 상태 — 각 칸의 대기 수와 지금 하는 일."""
    counts = dict(
        db.execute(select(Video.state, func.count()).group_by(Video.state)).all()
    )

    def take(states):
        return sum(int(counts.get(s, 0)) for s in states)

    running = db.scalars(
        select(CrawlRun).where(CrawlRun.status.in_(("running", "queued")))
        .order_by(CrawlRun.started_at.desc())
    ).first()

    # 지금 무엇을 하고 있는지는 **마지막 전이 기록**이 가장 정확합니다.
    # 상태 숫자만으로는 "자막 대기 107" 이 도는 중인지 멈춘 건지 모릅니다.
    last = db.scalars(
        select(PipelineEvent).order_by(PipelineEvent.created_at.desc()).limit(1)
    ).first()
    last_video = db.get(Video, last.video_id) if last else None

    cooling = transcript.blocked_until(db)
    return {
        "stages": [
            {"key": k, "label": label, "count": take(states)} for k, label, states in _STAGES
        ],
        "stuck": [
            {"key": k, "label": label, "count": take(states)} for k, label, states in _STUCK
        ],
        "current": None
        if running is None
        else {
            "runId": running.id,
            "status": running.status,
            "startedAt": to_utc_iso(running.started_at),
            "label": running.label,
        },
        "lastEvent": None
        if last is None
        else {
            "at": to_utc_iso(last.created_at),
            "stage": last.stage,
            "toState": last.to_state,
            "ok": bool(last.ok),
            "title": last_video.title if last_video else "",
        },
        # 자막이 쉬는 중이면 "기다리면 됩니다"의 근거가 됩니다.
        "transcriptCoolingUntil": to_utc_iso(cooling) if cooling else None,
    }


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, db: Session = Depends(get_db)):
    """실행 하나가 실제로 무엇을 옮겼는지.

    **이미 쌓고 있던 것을 안 보여 주고 있었습니다.** 단계별 합계만으로는
    "검토 3건"이 무엇이었는지, 왜 실패했는지 알 수 없습니다.
    """
    rows = db.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.run_id == run_id)
        .order_by(PipelineEvent.created_at)
        .limit(300)
    ).all()
    vids = {v.id: v for v in db.scalars(
        select(Video).where(Video.id.in_([e.video_id for e in rows]))
    ).all()} if rows else {}
    return [
        {
            "at": to_utc_iso(e.created_at),
            "stage": e.stage,
            "fromState": e.from_state,
            "toState": e.to_state,
            "ok": bool(e.ok),
            "videoId": e.video_id,
            "title": (vids.get(e.video_id).title if vids.get(e.video_id) else ""),
            "detail": (e.detail or {}).get("reason") or (e.detail or {}).get("error") or "",
        }
        for e in rows
    ]


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
