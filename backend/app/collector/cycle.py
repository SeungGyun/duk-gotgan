"""한 사이클 — 파이프라인 전 단계를 한 번씩 훑습니다.

**놓친 작업을 따로 추적하지 않습니다.** 할 일이 전부 상태로 표현되어 있어서,
틱이 스무 번 건너뛰어도 조건은 그대로 참입니다. 큐도 미처리 목록도 없습니다.

  자막 대기 → `state = TRANSCRIPT_PENDING`
  검토 대기 → `state = TRANSCRIBED`
  수집 대상 → `last_run_at + 주기 <= 지금`

**사이클마다 상한을 둡니다.** 대기가 50건이면 한 사이클이 몇 시간 돌고, 그동안
새 요청에 반응하지 못합니다. 끊어서 처리하고 다음 틱이 이어받습니다 — 검토
10건이면 이미 30분이라 1분 간격은 무시할 수준이고, 프롬프트 캐시(1시간)도
유지됩니다.

**할 일이 있을 때만 실행 기록을 남깁니다.** 1분마다 빈 기록을 만들면 실행
로그가 아무 일도 없었다는 줄로 뒤덮여 정작 무슨 일이 있었는지 안 보입니다.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector import discover as D
from app.collector import quota
from app.collector.schedule import due_keywords
from app.collector.transcript import blocked_until, transcribe_pending
from app.collector.youtube import YouTubeError
from app.db.models import CrawlRun, Keyword, Video
from app.llm.runner import recover_zombies, review_pending
from config.time import now_kst

logger = logging.getLogger(__name__)

# 사이클당 상한
TRANSCRIBE_PER_CYCLE = 20
REVIEW_PER_CYCLE = 10

# 사용자가 "지금 실행"을 누르면 이 상태로 기록이 하나 생기고, 워커가 집어갑니다.
# UI 가 직접 실행하지 않는 이유: 한 사이클이 몇 분씩 걸려서 HTTP 요청이
# 그동안 매달려 있게 됩니다.
QUEUED = "queued"


@dataclass
class CycleResult:
    zombies: int = 0
    keywords_run: int = 0
    discovered: int = 0
    rule_passed: int = 0
    transcribed: int = 0
    reviewed: int = 0
    published: int = 0
    notes: list[str] = field(default_factory=list)  # 실패 — 사람이 봐야 합니다
    paused: list[str] = field(default_factory=list)  # 보류 — 기다리면 풀립니다

    @property
    def did_anything(self) -> bool:
        return bool(self.zombies or self.keywords_run or self.transcribed or self.reviewed)

    def __str__(self) -> str:
        parts = []
        if self.zombies:
            parts.append(f"좀비회수 {self.zombies}")
        if self.keywords_run:
            parts.append(
                f"키워드 {self.keywords_run} → 발견 {self.discovered}·통과 {self.rule_passed}"
            )
        if self.transcribed:
            parts.append(f"자막 {self.transcribed}")
        if self.reviewed:
            parts.append(f"검토 {self.reviewed}·공개 {self.published}")
        return " · ".join(parts) if parts else "할 일 없음"


def recover_stale_runs(db: Session) -> int:
    """끊긴 실행 기록을 닫습니다.

    영상에는 좀비 회수가 있었는데 **실행 기록에는 없었습니다.** 워커가
    사이클 도중에 죽으면(재시작·강제 종료) 기록이 `running` 인 채로 영원히
    남고, 화면에는 "진행 중"이 여러 줄 쌓여 지금 정말 도는 것이 무엇인지
    알 수 없게 됩니다. 실제로 여덟 줄이 그렇게 남았습니다.

    **시간으로 재지 않습니다.** 락(GET_LOCK) 때문에 사이클은 동시에 하나
    뿐이라, 새 사이클이 시작하는 이 시점에 `running` 인 것은 예외 없이
    죽은 기록입니다. 몇 분이 지났는지 추측할 필요가 없습니다.

    **실패와 구분합니다.** 끊긴 것은 사람이 손댈 일이 아니라 다음 사이클이
    이어받는 상태입니다. 실패로 적으면 조치할 것이 있는 것처럼 보입니다.
    """
    stuck = db.scalars(select(CrawlRun).where(CrawlRun.status == "running")).all()
    for r in stuck:
        r.status = "interrupted"
        r.finished_at = now_kst()
        r.error = r.error or "워커가 사이클 도중에 멈췄습니다 — 남은 일은 다음 사이클이 이어받습니다."
    if stuck:
        db.commit()
        logger.info("[cycle] 끊긴 실행 기록 %d건 정리", len(stuck))
    return len(stuck)


def take_queued_run(db: Session) -> CrawlRun | None:
    """"지금 실행" 요청이 있으면 집어옵니다."""
    run = db.scalar(
        select(CrawlRun).where(CrawlRun.status == QUEUED).order_by(CrawlRun.started_at).limit(1)
    )
    if run is not None:
        run.status = "running"
        run.started_at = now_kst()
        db.commit()
    return run


def _has_pipeline_work(db: Session) -> bool:
    """지금 **실제로 할 수 있는** 일이 있는지.

    자막 냉각 중이면 대기 중인 `TRANSCRIPT_PENDING` 은 할 일이 아닙니다.
    이걸 일로 세면 냉각 60분 동안 매분 실행 기록이 하나씩 생기고, 전부
    "차단으로 쉬는 중"이라는 같은 사유로 실패 표시가 됩니다 — 실행 로그가
    정상적인 대기로 뒤덮여 정작 진짜 실패가 안 보입니다.
    """
    n = db.scalar(select(func.count()).select_from(Video).where(Video.state.in_(workable_states(db))))
    return bool(n)


def workable_states(db: Session) -> list[str]:
    """지금 손댈 수 있는 영상 상태."""
    return ["TRANSCRIBED"] if _cooling(db) else ["TRANSCRIBED", "TRANSCRIPT_PENDING"]


def _cooling(db: Session) -> bool:
    until = blocked_until(db)
    return bool(until and now_kst() < until)


async def run_cycle(db: Session) -> CycleResult:
    """한 바퀴. 락은 호출부(워커)가 잡습니다."""
    r = CycleResult()

    # 1) 죽은 워커가 잡아둔 것부터 풀어줍니다. 이게 먼저여야 이번 사이클에서
    #    다시 처리됩니다.
    r.zombies = recover_zombies(db)
    recover_stale_runs(db)

    requested = take_queued_run(db)
    # "지금 실행"은 주기를 무시하고 활성 키워드를 전부 돌립니다.
    if requested is not None:
        targets = list(db.scalars(select(Keyword).where(Keyword.status.in_(("pending", "active")))))
    else:
        targets = due_keywords(db)

    if not (targets or requested or _has_pipeline_work(db)):
        return r  # 할 일 없음 — 기록도 남기지 않습니다

    run = requested or CrawlRun(
        trigger="scheduled",
        status="running",
        started_at=now_kst(),
        label="정기 실행",
        stats={},
    )
    if requested is None:
        db.add(run)
        db.commit()

    try:
        await _pipeline(db, run, r, targets)
    except Exception as e:  # noqa: BLE001
        # **무슨 일이 있어도 실행 기록은 마감합니다.** 안 그러면 running 인
        # 채로 남아 실행 로그가 미완성 줄로 쌓입니다. 실제로 그랬습니다 —
        # 영상 하나의 403 이 사이클을 죽여 기록 여섯 개가 열린 채였습니다.
        db.rollback()
        r.notes.append(f"사이클이 중단됐습니다 — {type(e).__name__}: {e}")
        logger.exception("[cycle] 예기치 못한 오류")
        _finish(db, run, r, tokens=(0, 0))
        raise
    return r


async def _pipeline(
    db: Session, run: CrawlRun, r: CycleResult, targets: list[Keyword]
) -> None:
    # 2) 수집 — 쿼터가 모자라면 알아서 멈춥니다
    if targets:
        try:
            _, results = D.run_discovery(
                db,
                [k.id for k in targets],
                trigger=run.trigger,
                run=run,
            )
            r.keywords_run = len(results)
            r.discovered = run.stats.get("discovered", 0)
            r.rule_passed = run.stats.get("rulePassed", 0)
        except (quota.QuotaExceeded, YouTubeError) as e:
            r.notes.append(str(e))

    # 3) 자막 — 순차. 동시에 던지면 IP 가 막히고 그날 전체가 멈춥니다.
    t = transcribe_pending(db, limit=TRANSCRIBE_PER_CYCLE, run_id=run.id)
    r.transcribed = t["ok"]
    if t.get("blocked"):
        # 차단은 실패가 아니라 **뒤로 미룸**입니다. 영상은 대기 상태 그대로라
        # 냉각이 풀리면 다음 틱이 이어받습니다. 실패로 적으면 손댈 것이 있는
        # 것처럼 보이는데, 사실 기다리는 것 말고 할 일이 없습니다.
        r.paused.append(t.get("error", "자막 요청이 차단되었습니다."))
        logger.info("[cycle] 자막 보류 — %s", r.paused[-1])

    # 4) 검토 — 몰아서 순차. 프롬프트 캐시가 살아 있어야 사용량이 1/18 입니다.
    runs = await review_pending(db, limit=REVIEW_PER_CYCLE)
    done = [x for x in runs if x.ok]
    r.reviewed = len(done)
    r.published = len([x for x in done if x.published])
    for x in runs:
        if x.error:
            r.notes.append(f"{x.title[:30]} — {x.error}")

    _finish(db, run, r, tokens=(sum(x.input_weighted for x in done), sum(x.output_tokens for x in done)))


def _finish(db: Session, run: CrawlRun, r: CycleResult, tokens: tuple[int, int]) -> None:
    """실행 기록을 마감합니다. **AI 검토까지 한 기록에 담습니다** — 예전에는
    검색만 남아서, 화면만 보면 요약이 돌았는지 알 수 없었습니다."""
    stats = dict(run.stats or {})
    stats.update({"transcribed": r.transcribed, "reviewed": r.reviewed, "published": r.published})
    run.stats = stats
    run.input_tokens += tokens[0]
    run.output_tokens += tokens[1]
    run.finished_at = now_kst()
    done = _label(r, run.trigger)
    # 발견 단계가 지어 둔 키워드 이름을 살립니다.
    if r.keywords_run and run.label and "실행" in run.label:
        who = run.label.rsplit(" · ", 2)[0]
        run.label = f"{who} → {done}"
    else:
        run.label = done

    if r.notes and not r.did_anything:
        run.status = "failed"
    elif r.notes:
        run.status = "partial"
    else:
        run.status = "succeeded"
    if r.notes:
        run.error = " / ".join(r.notes)[:2000]
    db.commit()


def _label(r: CycleResult, trigger: str) -> str:
    """실행 이름은 **무엇을 했는지**로 짓습니다.

    발견이 있었으면 run_discovery 가 이미 키워드 이름으로 적어 둡니다 —
    그 이름을 덮지 않고 뒤에 결과를 붙입니다. "정기 실행" 만 줄줄이
    쌓이면 실행 로그를 봐도 무엇 때문에 돈 것인지 알 수 없습니다.
    """
    kind = "수동" if trigger == "manual" else "정기"
    bits = []
    if r.transcribed:
        bits.append(f"자막 {r.transcribed}건")
    if r.reviewed:
        bits.append(f"검토 {r.reviewed}건")
    if r.zombies:
        bits.append(f"좀비회수 {r.zombies}건")
    return f"{' · '.join(bits) or '점검'} · {kind} 실행"
