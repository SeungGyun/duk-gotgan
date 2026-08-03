"""운영 지표 — docs/API.md §3.

수집 파이프라인이 아직 없으므로 대부분 0 입니다. 0 을 감추려고 값을 지어내지
않습니다 — UI 는 0 을 받으면 해당 칩·미터를 숨기도록 만들어져 있습니다.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import current_user, require_owner
from app.api.errors import ApiError
from app.api.routes.lectures import Filters, _filtered
from app.api.serializers import run_out
from app.collector import transcript
from app.collector.schedule import next_due_at
from app.llm import usage as usage_guard
from app.db.models import (
    CrawlRun,
    Keyword,
    Lecture,
    Evaluation,
    PipelineEvent,
    Transcript,
    UsageLedger,
    UsageWindow,
    User,
    UserKeyword,
    Video,
    VideoKeyword,
)
from app.db.session import get_db
from config.settings import settings
from config.time import KST, now_kst, to_utc_iso

router = APIRouter(tags=["stats"])

@router.get("/stats/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(current_user)):
    today = now_kst().date()
    day_start = _midnight(today)
    week_start = _midnight(today - timedelta(days=7))

    # **보이는 범위를 목록 화면과 같은 함수로 셉니다.** 여기서 따로 조건을
    # 쓰면 상단바에는 332편인데 목록에는 41편인 상황이 생기고, 그러면
    # 어느 쪽이 고장인지 알 수 없게 됩니다.
    def _count(*where):
        stmt, _ = _filtered(Filters(user.id))
        if where:
            stmt = stmt.where(*where)
        return int(db.scalar(stmt.with_only_columns(func.count()).order_by(None)) or 0)

    published = _count()
    new_today = _count(Lecture.published_at >= day_start)
    week_added = _count(Lecture.published_at >= week_start)
    mine, _ul = _filtered(Filters(user.id))
    avg_score = db.scalar(
        mine.with_only_columns(func.avg(Lecture.expert_score)).order_by(None)
    )

    # **"오늘 한 일" 기준입니다.**
    #
    # 예전에는 "오늘 발견된 영상"이 각 칸까지 갔는지를 셌습니다. 한 사이클이
    # 몇 분 안에 발견→자막→요약을 다 하던 때는 맞는 셈법이었지만, 셋을
    # 따로 돌리고 대기가 몇 시간씩 쌓이는 지금은 오늘 발견한 것이 내일
    # 요약됩니다. 그래서 꼬리 세 칸이 늘 0 이었습니다 — 오늘 16편을
    # 공개했는데도요.
    def _today_count(model, when):
        return int(db.scalar(select(func.count()).select_from(model).where(when >= day_start)) or 0)

    discovered = _count_videos(db, day_start)
    rule_passed = _count_videos(db, day_start, exclude_state="DISCOVERED")
    transcribed = _today_count(Transcript, Transcript.created_at)
    reviewed = _today_count(Evaluation, Evaluation.created_at)
    published_today = new_today or 0

    ledger = db.get(UsageLedger, today)

    # 오늘 어느 키워드가 몇 편을 데려왔는지. **내가 구독한 것만** 셉니다 —
    # 남의 키워드가 올린 실적은 내 화면에서 읽을 수 없는 강의입니다.
    contributions = db.execute(
        select(
            Keyword.id,
            Keyword.term,
            func.count(func.distinct(Lecture.video_id)),
            Keyword.archived_at,
        )
        .join(VideoKeyword, VideoKeyword.keyword_id == Keyword.id)
        .join(Lecture, Lecture.video_id == VideoKeyword.video_id)
        .join(UserKeyword, UserKeyword.keyword_id == Keyword.id)
        .where(
            Lecture.is_hidden.is_(False),
            Lecture.published_at >= day_start,
            UserKeyword.user_id == user.id,
            UserKeyword.archived_at.is_(None),
        )
        .group_by(Keyword.id, Keyword.term, Keyword.archived_at)
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
            # 'RULE_PASSED' 라는 상태를 세고 있었습니다 — 그런 상태는 없어서
            # 자막 대기가 늘 0 으로 보였습니다. 실제로는 86건이었습니다.
            "transcript": _count_videos(db, states=("TRANSCRIPT_PENDING", "TRANSCRIBING")),
            "review": _count_videos(db, states=("TRANSCRIBED", "REVIEWING")),
        },
        "funnel": {
            "discovered": discovered,
            "rulePassed": rule_passed,
            "transcribed": transcribed,
            "reviewed": reviewed,
            "published": published_today,
        },
        "contributions": [
            # 지운 키워드는 괄호로 표시합니다. 이름만 그대로 두면 지금도
            # 도는 키워드처럼 보여서, 왜 저기서 안 나오나 헤매게 됩니다.
            {"keywordId": kid, "term": f"({term})" if archived else term, "published": cnt}
            for kid, term, cnt, archived in contributions
        ],
        "failures": _failures(db),
        "lastRunAt": to_utc_iso(last_run.started_at) if last_run else None,
    }


@router.get("/stats/usage")
def usage(db: Session = Depends(get_db), _: User = Depends(current_user)):
    today = now_kst().date()
    ledger = db.get(UsageLedger, today)
    win = db.get(UsageWindow, usage_guard.window_start())

    # **토큰은 5시간 창, 유튜브는 하루** — 주기가 다릅니다. 한 숫자로
    # 합치면 둘 중 하나는 틀린 기준으로 보이게 됩니다.
    return {
        "inputTokens": win.input_tokens if win else 0,
        "outputTokens": win.output_tokens if win else 0,
        "limitTokens": usage_guard.limit(db) or None,
        "windowHours": settings.token_window_hours,
        "windowResetsAt": to_utc_iso(usage_guard.window_end()),
        # 오늘 하루 합계 — 창과 별개로 "오늘 얼마나 했나"를 보려는 값입니다.
        "todayTokens": (ledger.input_tokens + ledger.output_tokens) if ledger else 0,
        "youtubeUnits": ledger.youtube_units if ledger else 0,
        "youtubeUnitLimit": settings.youtube_unit_limit,
        # 유튜브 쿼터는 태평양 표준시 자정에 리셋됩니다. 우리 집계는 KST 날짜
        # 기준이므로, 여기서는 "다음 KST 자정"을 알려 줍니다.
        "resetsAt": to_utc_iso(_midnight(today + timedelta(days=1))),
    }


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.scalars(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(50)).all()
    return [run_out(r) for r in rows]


# 파이프라인 각 칸에 지금 몇 개가 서 있는지, 그리고 **세 트랙이 각각
# 무엇을 하는 중인지**. 실행 기록만 봐서는 알 수 없습니다 — 기록은 지나간
# 일이고, 사용자가 궁금한 것은 지금 상태니까요.
#
# 셋을 따로 돌리게 된 뒤로 "지금 도는 실행" 하나만 보여 주면 거짓말이
# 됩니다. 자막과 요약이 나란히 도는데 화면에는 나중에 시작한 것만
# 떴습니다.
_FUNNEL = [
    ("discovered", "발견", ("DISCOVERED",)),
    ("transcript", "자막 대기", ("TRANSCRIPT_PENDING",)),
    ("review", "요약 대기", ("TRANSCRIBED",)),
    ("published", "공개", ("PUBLISHED",)),
]
_STUCK = [
    ("failedTranscript", "자막 실패", ("FAILED_TRANSCRIPT", "FAILED")),
    ("failedReview", "요약 실패", ("FAILED_REVIEW",)),
]

# 트랙마다 "지금 붙들고 있는 영상"이 어느 상태로 나타나는지.
_WORKING = {"transcript": "TRANSCRIBING", "review": "REVIEWING"}


def _now_working(db: Session, state: str) -> dict | None:
    v = db.scalars(
        select(Video).where(Video.state == state).order_by(Video.updated_at.desc())
    ).first()
    if v is None:
        return None
    return {"title": v.title, "since": to_utc_iso(v.updated_at)}


class LimitPatch(BaseModel):
    """0 이나 null 이면 상한을 풉니다."""

    limitTokens: int | None = None


@router.put("/stats/usage/limit", status_code=204)
def set_limit(patch: LimitPatch, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    """토큰 상한을 바꿉니다.

    **.env 가 아니라 DB 에 둡니다.** 설정 파일을 고치고 프로세스를
    재시작해야 한다면, 쓰다가 "조금만 올려 보자"를 할 수 없습니다.
    워커와 API 가 같은 값을 봅니다.
    """
    v = patch.limitTokens
    if v is not None and v < 0:
        raise ApiError(400, "INVALID_VALUE", "상한은 0 이상이어야 합니다.")
    # 0 은 "무제한", 값이 없으면 설정 기본값으로 되돌립니다.
    usage_guard.set_limit(db, v)


@router.get("/stats/pipeline")
def pipeline(db: Session = Depends(get_db), _: User = Depends(current_user)):
    counts = dict(db.execute(select(Video.state, func.count()).group_by(Video.state)).all())

    def take(states):
        return sum(int(counts.get(s, 0)) for s in states)

    running = {
        r.job: r
        for r in db.scalars(
            select(CrawlRun).where(CrawlRun.status.in_(("running", "queued")))
        ).all()
    }
    last_by_stage = {
        stage: at
        for stage, at in db.execute(
            select(PipelineEvent.stage, func.max(PipelineEvent.created_at)).group_by(
                PipelineEvent.stage
            )
        ).all()
    }

    # 검색은 "붙들고 있는 영상"이 없습니다. 대신 다음 차례가 언제인지가
    # 알고 싶은 값입니다.
    upcoming = [
        n
        for n in (
            next_due_at(k)
            for k in db.scalars(
                select(Keyword).where(
                    Keyword.status.in_(("pending", "active")), Keyword.archived_at.is_(None)
                )
            )
        )
        if n is not None
    ]

    tracks = []
    for key, label, waiting_states in (
        ("discover", "검색", ("DISCOVERED",)),
        ("transcript", "자막", ("TRANSCRIPT_PENDING",)),
        ("review", "요약", ("TRANSCRIBED",)),
    ):
        run = running.get(key)
        tracks.append(
            {
                "key": key,
                "label": label,
                "status": "running" if run is not None else "idle",
                "waiting": take(waiting_states),
                "runLabel": run.label if run else None,
                "startedAt": to_utc_iso(run.started_at) if run else None,
                # 지금 붙들고 있는 영상 — 이게 있어야 "멈춘 건지 도는
                # 건지"가 구분됩니다.
                "working": _now_working(db, _WORKING[key]) if key in _WORKING else None,
                "lastAt": to_utc_iso(last_by_stage.get(key)),
                "nextAt": to_utc_iso(min(upcoming)) if key == "discover" and upcoming else None,
            }
        )

    cooling = transcript.blocked_until(db)
    return {
        "funnel": [
            {"key": k, "label": label, "count": take(states)} for k, label, states in _FUNNEL
        ],
        "tracks": tracks,
        "stuck": [
            {"key": k, "label": label, "count": take(states)} for k, label, states in _STUCK
        ],
        "transcriptCoolingUntil": to_utc_iso(cooling) if cooling else None,
    }


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
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
def request_run(db: Session = Depends(get_db), _: User = Depends(require_owner)):
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
