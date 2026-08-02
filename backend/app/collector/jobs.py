"""세 가지 일을 **따로** 돌립니다.

예전에는 한 사이클이 발견 → 자막 → 검토를 순서대로 했습니다. 그래서
자막이 나와도 검토까지 최대 50분을 기다렸고, 긴 영상 하나가 받아쓰기
예산을 다 쓰면 그 사이클의 검토는 통째로 밀렸습니다.

**따로 돌려도 되는 이유**는 서로 다른 자원을 쓰기 때문입니다 (실측):

  받아쓰기  GPU 를 씁니다
  검토      원격 API 를 기다립니다 — 로컬 CPU 5.5%
  발견      네트워크 호출 몇 번, 초 단위

10코어에 load average 1.06 이라 셋이 같이 돌아도 서로를 밀어내지
않습니다. 순차로 묶어 둔 것이 손해였습니다.

  묶었을 때  받아쓰기 7.7시간 + 검토 5시간 = 12.7시간
  나눴을 때  max(7.7, 5) = 7.7시간

**각자 다른 락을 씁니다.** 같은 락을 쓰면 나눈 의미가 없고, 락 없이
두면 워커가 두 개 떴을 때 같은 영상을 두 번 처리합니다. 서로 건드리는
영상 상태가 달라서(대기/자막됨) 동시에 돌아도 부딪히지 않습니다.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector import asr
from app.collector import discover as D
from app.collector import resources
from app.collector import quota
from app.collector.schedule import due_keywords
from app.collector.transcript import blocked_until, transcribe_pending
from app.collector.youtube import YouTubeError
from app.db.models import CrawlRun, Evaluation, Keyword, Video
from app.llm.runner import recover_zombies, review_pending
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

DISCOVER_LOCK = "dukgotgan:discover"
TRANSCRIPT_LOCK = "dukgotgan:transcript"
REVIEW_LOCK = "dukgotgan:review"

# 한 번에 받아쓸 편수. 시간 예산 대신 편수로 끊습니다 — 이제 검토가 뒤에서
# 기다리지 않으므로 "20분 안에 끝내라"는 제약이 필요 없고, 편수로 끊어야
# 중간에 냉각·제외 같은 상태 변화를 다시 봅니다.
TRANSCRIBE_BATCH = 5

# 검토를 몰아서 부르는 기준.
#
# 프롬프트에 18,700 토큰짜리 고정 오버헤드가 있고 캐시 수명이 1시간입니다.
# 연달아 부르면 그 부분이 18.6배 싸집니다. 그래서 몇 건 모아서 한 번에
# 처리하고, 안 모이면 캐시가 만료되기 전에 그냥 돌립니다.
REVIEW_BATCH = 5
REVIEW_MAX_WAIT_MIN = 60
# 한 번에 붙잡고 있을 상한. 너무 크면 종료 신호에 늦게 반응합니다.
REVIEW_LIMIT = 20


@dataclass
class JobResult:
    job: str
    label: str = ""
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tokens: tuple[int, int] = (0, 0)
    did_work: bool = False


def _finish(db: Session, run: CrawlRun, r: JobResult) -> None:
    run.stats = {**(run.stats or {}), **r.stats}
    run.input_tokens += r.tokens[0]
    run.output_tokens += r.tokens[1]
    run.finished_at = now_kst()
    run.label = r.label or run.label
    run.status = "partial" if r.notes and r.did_work else ("failed" if r.notes else "succeeded")
    if r.notes:
        run.error = " / ".join(r.notes)[:2000]
    db.commit()


def _start(db: Session, job: str, trigger: str, label: str) -> CrawlRun:
    run = CrawlRun(
        trigger=trigger, job=job, status="running", started_at=now_kst(), label=label, stats={}
    )
    db.add(run)
    db.commit()
    return run


# ── 1) 검색 ──────────────────────────────────────────────────


def discover_job(db: Session, keyword_ids: list[str] | None = None, trigger: str = "scheduled"):
    """차례가 된 키워드를 검색합니다. 초 단위로 끝나는 가벼운 일입니다."""
    r = JobResult(job="discover")
    targets = (
        list(db.scalars(select(Keyword).where(Keyword.id.in_(keyword_ids))))
        if keyword_ids
        else due_keywords(db)
    )
    if not targets:
        return r

    run = _start(db, "discover", trigger, "검색")
    try:
        _, results = D.run_discovery(db, [k.id for k in targets], trigger=trigger, run=run)
        r.did_work = True
        r.stats = {
            "discovered": run.stats.get("discovered", 0),
            "rulePassed": run.stats.get("rulePassed", 0),
        }
        names = [k.channel_title or k.term for k in targets]
        head = ", ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")
        r.label = f"{head} → 발견 {r.stats['discovered']}·통과 {r.stats['rulePassed']}"
    except (quota.QuotaExceeded, YouTubeError) as e:
        r.notes.append(str(e))
    _finish(db, run, r)
    return r


# ── 2) 자막 ──────────────────────────────────────────────────


def transcript_job(db: Session) -> JobResult:
    """대기가 있으면 받아씁니다. 검색·검토를 기다리지 않습니다."""
    r = JobResult(job="transcript")

    cooling = blocked_until(db)
    waiting = db.scalar(
        select(func.count()).select_from(Video).where(Video.state == "TRANSCRIPT_PENDING")
    )
    if not waiting:
        # 할 일이 없으면 모델을 내립니다. 놀면서 1.6GB 를 쥐고 있으면
        # 요약 프로세스가 뜰 자리가 없어집니다 — 실제로 그래서 60건이
        # 죽었습니다. 다시 올리는 데 몇 초면 됩니다.
        asr.release_model()
        return r

    run = _start(db, "transcript", "scheduled", f"자막 — 대기 {waiting}건")
    t = transcribe_pending(db, limit=TRANSCRIBE_BATCH, run_id=run.id)
    r.did_work = bool(t["ok"] or t["failed"])

    # **아무것도 못 했으면 기록을 남기지 않습니다.**
    #
    # 냉각 중이어도 받아쓰기로 처리하므로 보통은 일이 됩니다. 정말 아무것도
    # 못 하는 경우는 받아쓰기까지 막혔을 때뿐인데, 그때 기록을 남기면
    # 30초마다 "자막 보류" 한 줄씩 쌓여 실행 로그가 덮입니다. 예전에
    # 1분 사이클에서 정확히 그렇게 37줄이 쌓였습니다.
    if not r.did_work:
        db.delete(run)
        db.commit()
        if t.get("blocked"):
            logger.info("[transcript] 보류 — %s", t.get("error", ""))
        return r

    r.stats = {"transcribed": t["ok"]}
    r.label = f"자막 {t['ok']}건" + (f" · 실패 {t['failed']}건" if t["failed"] else "")
    if cooling:
        r.label += " (자막 경로 냉각 중 — 받아쓰기)"
    _finish(db, run, r)
    return r


# ── 3) 요약 ──────────────────────────────────────────────────


def review_due(db: Session) -> tuple[bool, int, str]:
    """지금 검토를 돌릴 때인가. (돌릴까, 대기 수, 사유)"""
    waiting = int(
        db.scalar(select(func.count()).select_from(Video).where(Video.state == "TRANSCRIBED")) or 0
    )
    if not waiting:
        return False, 0, ""
    if waiting >= REVIEW_BATCH:
        return True, waiting, f"{waiting}건 모임"

    # 몇 건 안 되면 기다립니다 — 다만 캐시가 만료될 때까지만. 한 편이
    # 하루 종일 남아 있으면 곤란합니다.
    last = db.scalar(select(func.max(Evaluation.created_at)))
    if last is None:
        return True, waiting, "처음"
    idle_min = (now_kst() - last).total_seconds() / 60
    if idle_min >= REVIEW_MAX_WAIT_MIN:
        return True, waiting, f"{int(idle_min)}분째 조용함"
    return False, waiting, ""


async def review_job(db: Session) -> JobResult:
    """모인 자막을 몰아서 요약합니다."""
    r = JobResult(job="review")
    r.stats["zombies"] = recover_zombies(db)

    go, waiting, why = review_due(db)
    if not go:
        return r

    # **메모리가 빡빡하면 비켜 줍니다.** 받아쓰기가 긴 오디오를 올리는
    # 동안 클로드 프로세스가 뜨지 못해 죽는 일이 60건 있었습니다. 죽은
    # 뒤 재시도하면 그때까지 쓴 시간이 버려지고 실패 기록도 남습니다.
    if resources.memory_tight():
        logger.info("[review] 메모리가 빡빡해 이번 차례는 건너뜁니다 (대기 %d건)", waiting)
        return r

    run = _start(db, "review", "scheduled", f"요약 — 대기 {waiting}건 ({why})")
    runs = await review_pending(db, limit=min(waiting, REVIEW_LIMIT), run_id=run.id)
    done = [x for x in runs if x.ok]
    r.did_work = bool(runs)
    r.stats.update({"reviewed": len(done), "published": len([x for x in done if x.published])})
    r.tokens = (sum(x.input_weighted for x in done), sum(x.output_tokens for x in done))
    for x in runs:
        if x.error:
            r.notes.append(f"{x.title[:30]} — {x.error}")
    r.label = f"요약 {r.stats['reviewed']}건 · 공개 {r.stats['published']}건"
    _finish(db, run, r)
    return r


# ── "지금 실행" ──────────────────────────────────────────────


def take_queued_run(db: Session) -> CrawlRun | None:
    """사용자가 누른 요청을 집어옵니다. 검색 잡이 처리합니다 —
    누르는 의도는 "지금 새로 찾아봐"이지 "요약해"가 아닙니다."""
    run = db.scalar(
        select(CrawlRun).where(CrawlRun.status == "queued").order_by(CrawlRun.started_at).limit(1)
    )
    if run is not None:
        run.status = "running"
        run.started_at = now_kst()
        db.commit()
    return run


def recover_stale_runs(db: Session, job: str) -> int:
    """이 잡의 끊긴 실행 기록을 닫습니다.

    락 때문에 같은 잡은 동시에 하나뿐이라, 새로 시작하는 시점에 running
    인 것은 죽은 기록입니다. **잡별로 봅니다** — 나눠 놓고 전체를 닫으면
    지금 돌고 있는 다른 잡의 기록까지 끊어 버립니다.
    """
    # 잡을 나누기 전의 기록(job='cycle')은 아무도 자기 것으로 치지 않아
    # 영영 running 으로 남습니다. 검색 잡이 지나가며 같이 정리합니다.
    mine = [job] + (["cycle"] if job == "discover" else [])
    stuck = db.scalars(
        select(CrawlRun).where(CrawlRun.status == "running", CrawlRun.job.in_(mine))
    ).all()
    for x in stuck:
        x.status = "interrupted"
        x.finished_at = now_kst()
        x.error = x.error or "워커가 도중에 멈췄습니다 — 남은 일은 다음 차례가 이어받습니다."
    if stuck:
        db.commit()
        logger.info("[%s] 끊긴 기록 %d건 정리", job, len(stuck))
    return len(stuck)


__all__ = [
    "DISCOVER_LOCK",
    "REVIEW_LOCK",
    "TRANSCRIPT_LOCK",
    "discover_job",
    "recover_stale_runs",
    "review_due",
    "review_job",
    "settings",
    "take_queued_run",
    "transcript_job",
]
