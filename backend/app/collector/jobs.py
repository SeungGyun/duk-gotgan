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

from app.collector import cadence
from app.collector import cleanup
from app.collector import discover as D
from app.collector import resources
from app.collector import quota
from app.collector import queue
from app.blog import publish
from app.collector.rules import window_start
from app.collector.schedule import due_keywords
from app.collector.transcript import blocked_until, transcribe_pending
from app.collector.youtube import YouTubeError
from app.db.models import CrawlRun, Evaluation, Keyword, PipelineEvent, Video, VideoKeyword
from app.llm import pace, usage
from app.llm.runner import recover_zombies, review_pending
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

DISCOVER_LOCK = "dukgotgan:discover"
TRANSCRIPT_LOCK = "dukgotgan:transcript"
# **요약 락은 회사마다 다릅니다.** 이름이 같으면 클로드 워커와 안티그래비티
# 워커가 번갈아 하나씩만 돌아, 나눈 의미가 없습니다. 갈라 두면 둘이 같이
# 돌고, 같은 회사 안에서는 여전히 하나만 돕니다 — 좀비 회수가 자기 회사의
# 진행 중인 작업을 건드리지 않는 근거가 이 직렬화입니다 (runner.recover_zombies).
REVIEW_LOCK = f"dukgotgan:review:{settings.review_provider}"
CLEANUP_LOCK = "dukgotgan:cleanup"
# **회사로 가르지 않습니다.** 요약은 두 회사가 나눠 하지만 블로그는 하나뿐이라,
# 워커가 둘 떠 있어도 발행은 한 번에 하나만 돌아야 합니다.
PUBLISH_LOCK = "dukgotgan:publish"



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


# 자막 줄이 이보다 짧아지면 발견분에서 채웁니다.
#
# 20편이면 평균 30분짜리 기준으로 열 시간치 소리이고, 받아쓰기 5배속에서
# 두 시간 분량입니다. 넉넉히 물려 있으면서도, 마음이 바뀌어 대기 목록에서
# 빼고 싶을 때 줄이 지나치게 길지 않은 정도입니다.
BACKFILL_LOW_WATER = 20


def backfill(db: Session) -> int:
    """발견해 둔 것을 자막 줄로 올립니다. **유튜브 유닛이 들지 않습니다.**

    `max_per_run` 은 *새 검색 결과*를 한 번에 몇 편 올릴지 정하는 값입니다.
    한 사이클이 발견부터 요약까지 다 하던 시절의 비용 가드였는데, 셋을
    따로 돌리는 지금은 토큰 상한이 그 일을 합니다.

    그 사이 이런 일이 벌어졌습니다 — 발견 283건이 묶여 있는데 자막·요약
    트랙은 완전히 놀고, 키워드당 하루 10편씩이라 다 풀리는 데 엿새가
    걸립니다. **검색은 이미 끝나서 유닛을 지불한 재고인데** 쓰지 못하고
    있었습니다.

    **키워드끼리 번갈아 올립니다.** 앞에서부터 채우면 발견 55건인 키워드
    하나가 줄을 독차지하고 나머지는 그대로 굶습니다 (queue.py 참고).
    """
    waiting = int(
        db.scalar(select(func.count()).select_from(Video).where(Video.state == "TRANSCRIPT_PENDING"))
        or 0
    )
    room = BACKFILL_LOW_WATER - waiting
    if room <= 0:
        return 0

    ids = queue.next_ids(db, "DISCOVERED", room)
    if not ids:
        return 0

    for vid in ids:
        v = db.get(Video, vid)
        if v is None or v.state != "DISCOVERED":
            continue
        v.state = "TRANSCRIPT_PENDING"
        v.state_reason = None
        db.add(
            PipelineEvent(
                video_id=v.id,
                from_state="DISCOVERED",
                to_state="TRANSCRIPT_PENDING",
                stage="discover",
                ok=True,
                detail={"reason": "자막 줄이 비어 발견분에서 채웠습니다."},
            )
        )
    db.commit()
    logger.info("[discover] 발견분 %d편을 자막 줄로 올렸습니다 (대기 %d편)", len(ids), waiting)
    return len(ids)


def drop_stale(db: Session) -> int:
    """줄에서 기다리다 **창을 벗어난 것**을 뺍니다.

    수집할 때는 창 안이었습니다. 그런데 자막·요약 줄이 밀리는 동안 날짜가
    지나갑니다 — `경제`·`주식` 처럼 창이 하루인 키워드가 사흘 된 영상을
    붙들고 있는 식입니다. 실측에서 대기 108편 중 **48편이 그랬습니다.**

    그걸 그대로 요약하면 "하루만 지나도 헌 이야기" 라고 **사용자가 정해 둔
    기준을 어기면서** 편당 8만 토큰을 씁니다. 창은 화면에서 키워드마다
    정하는 값이라, 그 뜻을 여기서도 지켜야 합니다.

    **기준은 발견 단계와 같은 함수(`window_start`)입니다.** 따로 계산하면
    한쪽이 데려오고 다른 쪽이 버리는 일이 생깁니다 — 그 함수는 "못 돈
    날은 그만큼 더 거슬러 본다" 까지 알고 있어서, 수집이 멎었던 기간의
    영상을 애먼 이유로 버리지 않습니다.

    **한 키워드라도 아직 원하면 남깁니다.** 영상 하나에 키워드가 여럿
    붙는데(`과학,사이언스`), 창이 좁은 쪽 기준으로 버리면 넓은 쪽 사람의
    곳간에서 지운 적 없는 것이 사라집니다.

    지우지 않고 `SKIPPED` 로 세웁니다. 발견 단계가 이 상태를 보고 다시
    데려오지 않고(discover.py), 대기 목록 화면에서 되돌릴 수도 있습니다.
    """
    rows = db.execute(
        select(Video, Keyword)
        .join(VideoKeyword, VideoKeyword.video_id == Video.id)
        .join(Keyword, Keyword.id == VideoKeyword.keyword_id)
        .where(Video.state.in_(("DISCOVERED", "TRANSCRIPT_PENDING")))
    ).all()

    wanted: dict[str, bool] = {}
    videos: dict[str, Video] = {}
    now = now_kst()
    for video, kw in rows:
        videos[video.id] = video
        if video.published_at is None:
            # 올린 날짜를 모르면 판단할 근거가 없습니다 — 남깁니다.
            wanted[video.id] = True
            continue
        wanted[video.id] = wanted.get(video.id, False) or (
            video.published_at >= window_start(kw, now)
        )

    dropped = 0
    for vid, keep in wanted.items():
        if keep:
            continue
        v = videos[vid]
        # **반올림합니다.** `.days` 로 자르면 사흘에서 몇 마이크로초 모자란
        # 값이 "2일 전" 이 됩니다 — DB 시각이 초 단위라 흔히 이렇게 됩니다.
        age = max(1, round((now - v.published_at).total_seconds() / 86400))
        v.state = "SKIPPED"
        v.state_reason = f"기다리는 사이 기간이 지났습니다 · {age}일 전 영상"
        db.add(
            PipelineEvent(
                video_id=v.id,
                from_state="TRANSCRIPT_PENDING",
                to_state="SKIPPED",
                stage="discover",
                ok=True,
                detail={"reason": v.state_reason},
            )
        )
        dropped += 1
    if dropped:
        db.commit()
        logger.info("[transcript] 기간이 지난 %d편을 줄에서 뺐습니다", dropped)
    return dropped


def discover_job(db: Session, keyword_ids: list[str] | None = None, trigger: str = "scheduled"):
    """차례가 된 키워드를 검색합니다. 초 단위로 끝나는 가벼운 일입니다."""
    r = JobResult(job="discover")

    # **차례가 아니어도 재고는 씁니다.** 검색은 하루 한 번이지만, 이미
    # 발견해 둔 것을 올리는 데는 유닛이 들지 않습니다.
    filled = backfill(db)
    if filled:
        r.stats["rulePassed"] = filled

    targets = (
        list(db.scalars(select(Keyword).where(Keyword.id.in_(keyword_ids))))
        if keyword_ids
        else due_keywords(db)
    )
    if not targets:
        if filled:
            run = _start(db, "discover", trigger, f"발견분 {filled}편을 자막 줄로")
            r.did_work = True
            r.label = f"발견분 {filled}편을 자막 줄로"
            _finish(db, run, r)
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

    # **세우기 전에 기간부터 봅니다.** 줄에서 기다리는 동안 창을 벗어난
    # 것을 그대로 요약하면, 키워드마다 정해 둔 "며칠까지 볼 것인가" 를
    # 어기면서 편당 8만 토큰을 씁니다.
    stale = drop_stale(db)
    if stale:
        r.stats["skipped"] = stale

    cooling = blocked_until(db)
    waiting = db.scalar(
        select(func.count()).select_from(Video).where(Video.state == "TRANSCRIPT_PENDING")
    )
    if not waiting:
        return r

    run = _start(db, "transcript", "scheduled", f"자막 — 대기 {waiting}건")
    t = transcribe_pending(db, limit=cadence.TRANSCRIBE_BATCH, run_id=run.id)
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

    # **모델을 내려놓는 손질은 이제 없습니다.**
    #
    # 예전에는 이 잡이 위스퍼 1.6GB 를 쥔 채로 살아서, 놀 때나 메모리가
    # 빡빡할 때 손으로 내려놔야 했습니다 — 안 그러면 요약 잡이 뜰 자리를
    # 영영 못 찾았습니다. 받아쓰기를 자식 프로세스로 옮기면서 모델이 한 편
    # 끝날 때마다 프로세스와 함께 사라집니다 (collector/asr.py).

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

    # **회사가 안 받아 주는 중이면 여기서 끝냅니다.** 풀렸는지는 불러
    # 봐야만 알 수 있어서 타이머로 셉니다 (llm/pace.py).
    resting = pace.resume_at(db, settings.review_provider)
    if resting is not None:
        return False, waiting, f"{resting:%H:%M} 까지 쉬는 중"

    # **우리 상한은 매번 다시 봅니다.**
    #
    # 처음엔 "창이 바뀔 때까지 쉰다"고 시각을 적어 두었습니다. 그런데 상한은
    # 화면에서 언제든 올릴 수 있어서, 올린 뒤에도 적어 둔 시각이 남아 두
    # 시간을 그냥 놀았습니다. 적어 둔 것은 결정의 캐시인데 그 입력이 바뀌는
    # 값이었습니다 — 장부를 읽는 것은 인덱스 한 번이라 매 틱 봐도 쌉니다.
    try:
        usage.check(db)
    except usage.UsageExceeded as e:
        pace.mark_capped(db, settings.review_provider, str(e))
        return False, waiting, "상한을 넘어 멈춤"
    pace.clear_capped(db, settings.review_provider)

    if waiting >= cadence.REVIEW_BATCH:
        return True, waiting, f"{waiting}건 모임"

    # 몇 건 안 되면 기다립니다 — 다만 캐시가 만료될 때까지만. 한 편이
    # 하루 종일 남아 있으면 곤란합니다.
    last = db.scalar(select(func.max(Evaluation.created_at)))
    if last is None:
        return True, waiting, "처음"
    idle_min = (now_kst() - last).total_seconds() / 60
    if idle_min >= cadence.REVIEW_MAX_WAIT_MIN:
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
    runs = await review_pending(db, limit=min(waiting, cadence.REVIEW_LIMIT), run_id=run.id)
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


# ── 4) 블로그 발행 ───────────────────────────────────────────


def publish_job(db: Session) -> JobResult:
    """차례가 되면 한 편을 블로그로 내보냅니다 (.spec/tistory.md).

    **기본은 꺼져 있습니다.** 공개 발행은 되돌리기 번거로워서, 워커를
    재시작했다는 이유만으로 글이 나가서는 안 됩니다 (`BLOG_ENABLED`).
    """
    r = JobResult(job="publish")
    if not settings.blog_enabled:
        return r
    if not publish.due(db):
        return r

    out = publish.publish_once(db)
    if not out.did_work:
        # **기록을 남기지 않습니다.** 올릴 것이 없거나 세션이 만료된 경우인데,
        # 여기서 실행 기록을 만들면 1분마다 한 줄씩 쌓여 화면이 덮입니다 —
        # 자막 잡이 같은 이유로 같은 일을 합니다.
        if out.error:
            logger.warning("[publish] %s", out.error)
        return r

    run = _start(db, "publish", "scheduled", "블로그")
    r.did_work = True
    r.label = f"블로그 — {out.label}"
    r.stats = {"published": 1 if out.ok else 0}
    if out.error:
        r.notes.append(out.error)
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
    "PUBLISH_LOCK",
    "REVIEW_LOCK",
    "TRANSCRIPT_LOCK",
    "discover_job",
    "publish_job",
    "recover_stale_runs",
    "review_due",
    "review_job",
    "settings",
    "take_queued_run",
    "transcript_job",
]


# ── 4) 정리 ──────────────────────────────────────────────────


def cleanup_job(db: Session) -> JobResult:
    """다 쓴 자막 원문과 오래된 이력을 버립니다.

    **기록을 남기지 않습니다.** 하루 한 번 도는 청소인데 실행 로그에 줄을
    더하면, 정작 무슨 일이 있었는지 보려는 화면이 청소 기록으로 덮입니다.
    지운 것이 있으면 워커 로그에만 적습니다.
    """
    r = JobResult(job="cleanup")
    out = cleanup.sweep(db)
    r.did_work = any(out.values())
    r.stats = out
    if r.did_work:
        r.label = f"자막 {out['transcripts']} · 이력 {out['events']} · 기록 {out['runs']} 정리"
    return r
