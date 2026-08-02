"""수집 워커 — 세 가지 일을 **따로** 돌립니다.

  python -m scripts.worker
  python -m scripts.worker --once      # 각 잡을 한 번씩만
  python -m scripts.worker --only review

예전에는 한 사이클이 발견 → 자막 → 검토를 순서대로 했습니다. 그래서
자막이 나와도 검토까지 최대 50분을 기다렸고, 긴 영상 하나가 받아쓰기
예산을 다 쓰면 그 사이클의 검토는 통째로 밀렸습니다.

셋은 서로 다른 자원을 씁니다 — 받아쓰기는 GPU, 검토는 원격 API 대기
(로컬 CPU 5.5%), 발견은 초 단위 네트워크 호출. 10코어 기계에서 같이
돌려도 서로를 밀어내지 않습니다.

**각자 다른 락을 씁니다.** 워커가 두 개 떠도 같은 잡이 겹치지 않고,
서로 다른 잡은 자유롭게 같이 돕니다.

API 서버와도 분리된 프로세스입니다. 검토 한 건이 몇 분씩 도는 동안
화면이 영향을 받지 않아야 하고, 워커가 죽어도 열람은 계속돼야 합니다.
"""

import argparse
import asyncio
import logging
import signal

from app.collector import jobs
from app.db.lock import try_lock
from app.db.session import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

# 잡마다 확인 주기가 다릅니다.
#
#   발견   1분 — 키워드의 차례를 놓치지 않을 만큼만 자주.
#   자막   30초 — 대기가 있으면 계속 도는 것이 목적이라 짧게.
#   검토   1분 — 모였는지만 보므로 자주 봐도 쌉니다. 실제로 부를지는
#          review_due() 가 정합니다(5건 모임 또는 1시간 조용함).
TICKS = {"discover": 60, "transcript": 30, "review": 60}


# **막는 일은 스레드로 보냅니다.**
#
# 셋을 asyncio.gather 로 띄워 놓고 동기 함수를 그대로 부르면 이벤트 루프가
# 통째로 멈춥니다. 받아쓰기 한 편이 5~13분인데 그동안 검토도 검색도 한
# 발짝을 못 나갑니다 — 나눈 의미가 사라집니다.
#
# DB 세션은 **스레드 안에서** 만듭니다. 스레드 간에 세션을 넘기면
# 안 됩니다.


def _discover_blocking() -> str | None:
    db = SessionLocal()
    try:
        jobs.recover_stale_runs(db, "discover")
        # "지금 실행"은 검색 잡이 집어갑니다 — 누르는 의도는 "지금 새로
        # 찾아봐"이지 "요약해"가 아닙니다.
        queued = jobs.take_queued_run(db)
        if queued is not None:
            queued.status = "succeeded"
            db.commit()
            r = jobs.discover_job(db, trigger="manual")
        else:
            r = jobs.discover_job(db)
        return r.label if r.did_work else None
    finally:
        db.close()


def _transcript_blocking() -> str | None:
    db = SessionLocal()
    try:
        jobs.recover_stale_runs(db, "transcript")
        r = jobs.transcript_job(db)
        return r.label if r.did_work else None
    finally:
        db.close()


async def _run_discover() -> None:
    label = await asyncio.to_thread(_discover_blocking)
    if label:
        logger.info("[discover] %s", label)


async def _run_transcript() -> None:
    label = await asyncio.to_thread(_transcript_blocking)
    if label:
        logger.info("[transcript] %s", label)


async def _run_review() -> None:
    db = SessionLocal()
    try:
        jobs.recover_stale_runs(db, "review")
        r = await jobs.review_job(db)
        if r.did_work:
            logger.info("[review] %s", r.label)
            for n in r.notes:
                logger.warning("[review] %s", n)
    finally:
        db.close()


JOBS = {
    "discover": (jobs.DISCOVER_LOCK, _run_discover),
    "transcript": (jobs.TRANSCRIPT_LOCK, _run_transcript),
    "review": (jobs.REVIEW_LOCK, _run_review),
}


async def run_once(name: str) -> None:
    lock, fn = JOBS[name]
    with try_lock(lock) as acquired:
        if not acquired:
            logger.debug("[%s] 다른 워커가 도는 중 — 건너뜁니다", name)
            return
        await fn()


async def _loop(name: str, stopping: asyncio.Event) -> None:
    tick = TICKS[name]
    while not stopping.is_set():
        try:
            await run_once(name)
        except Exception:  # noqa: BLE001 — 한 잡이 죽어도 나머지는 살아야 합니다
            logger.exception("[%s] 예기치 못한 오류 — 다음 차례에 다시 시도합니다", name)
        try:
            await asyncio.wait_for(stopping.wait(), timeout=tick)
        except asyncio.TimeoutError:
            continue


async def loop(names: list[str]) -> None:
    stopping = asyncio.Event()

    def stop(*_):
        logger.info("종료 신호 — 하던 일을 마치고 멈춥니다")
        stopping.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)

    logger.info("워커 시작 — %s", " · ".join(f"{n} {TICKS[n]}초" for n in names))
    # **같이 돕니다.** 순서대로 묶어 두었던 것이 손해였습니다.
    await asyncio.gather(*(_loop(n, stopping) for n in names))
    logger.info("워커 종료")


def main() -> None:
    parser = argparse.ArgumentParser(description="수집 워커")
    parser.add_argument("--once", action="store_true", help="각 잡을 한 번씩만 돌고 종료")
    parser.add_argument(
        "--only", choices=sorted(JOBS), action="append", help="이 잡만 돌립니다 (여러 번 가능)"
    )
    args = parser.parse_args()

    names = args.only or list(JOBS)
    init_db()
    if args.once:
        asyncio.run(_once_all(names))
    else:
        asyncio.run(loop(names))


async def _once_all(names: list[str]) -> None:
    for n in names:
        await run_once(n)


if __name__ == "__main__":
    main()
