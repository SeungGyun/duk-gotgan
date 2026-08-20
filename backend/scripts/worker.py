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
import time

from app.collector import beat, cadence, jobs
from app.db.lock import try_lock
from app.db.session import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

TICKS = cadence.TICKS


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


def _cleanup_blocking() -> str | None:
    db = SessionLocal()
    try:
        r = jobs.cleanup_job(db)
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


def _publish_blocking() -> str | None:
    db = SessionLocal()
    try:
        jobs.recover_stale_runs(db, "publish")
        r = jobs.publish_job(db)
        return r.label if r.did_work else None
    finally:
        db.close()


async def _run_publish() -> None:
    label = await asyncio.to_thread(_publish_blocking)
    if label:
        logger.info("[publish] %s", label)


async def _run_discover() -> None:
    label = await asyncio.to_thread(_discover_blocking)
    if label:
        logger.info("[discover] %s", label)


async def _run_cleanup() -> None:
    label = await asyncio.to_thread(_cleanup_blocking)
    if label:
        logger.info("[cleanup] %s", label)


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
    "publish": (jobs.PUBLISH_LOCK, _run_publish),
    "cleanup": (jobs.CLEANUP_LOCK, _run_cleanup),
}


async def run_once(name: str) -> None:
    lock, fn = JOBS[name]
    with try_lock(lock) as acquired:
        if not acquired:
            logger.debug("[%s] 다른 워커가 도는 중 — 건너뜁니다", name)
            return
        await fn()


# ── 멈춤 감시 ────────────────────────────────────────────────
#
# **멈추면 보여야 합니다.** 2026-08-14, 자막 잡이 위스퍼 안에서 36시간을
# 헛돌았는데 로그에는 한 줄도 안 남았습니다 — 대기가 76건까지 쌓이고 나서야
# 알았습니다. 다른 잡들은 멀쩡히 돌고 있어서 "워커가 죽었나" 로도 안 보였고요.
#
# 잡마다 마지막으로 **한 걸음 나아간** 때를 적어 두고, 그만큼이 지나도록
# 소식이 없으면 경고를 냅니다. **끊지는 않습니다** — 무엇이 붙들려 있는지
# 모르는 채로 죽이면 하던 일이 어디까지 갔는지도 같이 잃습니다. 끊는 것은
# 그 일을 아는 쪽이 합니다 (받아쓰기는 collector/asr.py 가 합니다).
#
# **한 바퀴 돌았는지로 재던 것을 고쳤습니다.** 요약 한 호출은 대기를 스무
# 편까지 붙잡고 도는데, 돌아오는 때만 보면 네 편을 멀쩡히 담는 동안에도
# "10분째 한 바퀴도 못 돌았습니다" 가 찍혔습니다 (collector/beat.py).
STALL_TICKS = 10

# 이만큼 조용하면 붙들린 것으로 봅니다. **잡마다 다릅니다** — 한 걸음의
# 크기가 다르기 때문입니다. 검색은 초 단위로 끝나지만, 받아쓰기 한 편은
# 두 시간짜리 영상이면 12분이고 요약 한 편도 자막이 길면 10분을 넘깁니다.
# 한 값으로 묶으면 둘 중 하나가 틀립니다 — 짧게 잡으면 긴 한 편마다
# 오경보가 나고, 길게 잡으면 검색이 반나절 멎어도 조용합니다.
STALL_FLOOR_SEC = {"discover": 10 * 60, "transcript": 30 * 60, "review": 30 * 60}
STALL_FLOOR_DEFAULT = 10 * 60
# 한 번 붙들리면 풀릴 때까지 조용할 테니, 같은 경고로 로그를 덮지 않습니다.
STALL_REPEAT_SEC = 30 * 60

_ticked: dict[str, float] = {}
_warned: dict[str, float] = {}


def stalled(names: list[str]) -> list[tuple[str, int, str]]:
    """붙들린 잡과 (조용한 시간(분), 그때 하던 일). 알린 것은 한동안 다시 안 냅니다."""
    now = time.monotonic()
    out = []
    for name in names:
        # **한 바퀴 돈 때와 마지막 걸음 중 나중 것을 봅니다.** 잡이 한
        # 호출 안에서 여러 편을 처리하므로, 돌아오는 때만 보면 일하는
        # 중에 경보가 납니다 — 그런 경보는 다음부터 안 읽힙니다.
        # **없을 때 0.0 으로 메우면 안 됩니다.** `monotonic()` 은 부팅
        # 기준이라 작을 수 있고(이 기계에서 재부팅 두 시간 뒤 7,000초),
        # 0 과 견주면 그게 바닥이 되어 조용한 시간이 부팅 시각에서 잘립니다.
        step = beat.last(name)
        seen = _ticked.get(name, now)
        if step is not None:
            seen = max(seen, step[0])
        quiet = now - seen
        if quiet < max(STALL_FLOOR_SEC.get(name, STALL_FLOOR_DEFAULT), TICKS[name] * STALL_TICKS):
            continue
        # **기본값이 0.0 이면 안 됩니다.** `monotonic()` 은 작은 수에서
        # 시작하므로(이 기계에서 재실행 직후 1,156초였습니다), 0 과의 차이가
        # 30분보다 작아서 **한 번도 알린 적 없는 잡이 "방금 알렸다"로**
        # 취급됩니다 — 켜고 30분 안에 붙들리면 워치독이 통째로 침묵합니다.
        # 그때가 바로 알려야 할 때입니다.
        if now - _warned.get(name, float("-inf")) < STALL_REPEAT_SEC:
            continue
        _warned[name] = now
        out.append((name, int(quiet // 60), step[1] if step else ""))
    return out


async def _watch(names: list[str], stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            await asyncio.wait_for(stopping.wait(), timeout=60)
            return
        except asyncio.TimeoutError:
            pass
        for name, minutes, what in stalled(names):
            # **무엇을 하다 멈췄는지까지 적습니다.** 잡 이름만으로는 로그를
            # 거슬러 올라가야 하는데, 붙들린 잡은 정의상 아무 줄도 안 남깁니다.
            logger.warning(
                "[watchdog] %s 잡이 %d분째 아무 진척이 없습니다 — %s",
                name, minutes,
                f"「{what}」 에서 붙들린 것으로 보입니다" if what else "한 바퀴도 못 돌았습니다",
            )


async def _loop(name: str, stopping: asyncio.Event) -> None:
    tick = TICKS[name]
    _ticked[name] = time.monotonic()
    while not stopping.is_set():
        try:
            await run_once(name)
        except Exception:  # noqa: BLE001 — 한 잡이 죽어도 나머지는 살아야 합니다
            logger.exception("[%s] 예기치 못한 오류 — 다음 차례에 다시 시도합니다", name)
        # 오류로 끝났어도 한 바퀴는 돈 것입니다. 감시하려는 것은 "실패"가
        # 아니라 "돌아오지 않음" 입니다.
        _ticked[name] = time.monotonic()
        _warned.pop(name, None)
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
    await asyncio.gather(*(_loop(n, stopping) for n in names), _watch(names, stopping))
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
