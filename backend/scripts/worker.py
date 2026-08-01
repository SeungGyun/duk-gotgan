"""수집 워커 — 1분마다 깨어나 할 일이 있으면 합니다.

  python -m scripts.worker
  python -m scripts.worker --once      # 한 사이클만 (수동 실행)
  python -m scripts.worker --tick 300  # 틱 간격 바꾸기

API 서버와 **분리된 프로세스**입니다. 검토 한 건이 몇 분씩 도는 동안 화면이
영향을 받지 않아야 하고, 워커가 죽어도 열람은 계속돼야 합니다.
"""

import argparse
import asyncio
import logging
import signal

from app.collector.cycle import run_cycle
from app.db.lock import try_lock
from app.db.session import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

DEFAULT_TICK = 60


async def one_cycle() -> None:
    with try_lock() as acquired:
        if not acquired:
            logger.info("[tick] 다른 워커가 도는 중 — 건너뜁니다")
            return
        db = SessionLocal()
        try:
            result = await run_cycle(db)
            if result.did_anything or result.notes:
                logger.info("[cycle] %s", result)
                for n in result.notes:
                    logger.warning("[cycle] %s", n)
            else:
                logger.debug("[cycle] 할 일 없음")
        finally:
            db.close()


async def loop(tick: int) -> None:
    stopping = asyncio.Event()

    def stop(*_):
        logger.info("종료 신호 — 이번 사이클을 마치고 멈춥니다")
        stopping.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)

    logger.info("워커 시작 — %d초마다 확인합니다", tick)
    while not stopping.is_set():
        try:
            await one_cycle()
        except Exception:  # noqa: BLE001 — 한 사이클이 죽어도 워커는 살아야 합니다
            logger.exception("[cycle] 예기치 못한 오류 — 다음 틱에 다시 시도합니다")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=tick)
        except asyncio.TimeoutError:
            continue
    logger.info("워커 종료")


def main() -> None:
    parser = argparse.ArgumentParser(description="수집 워커")
    parser.add_argument("--once", action="store_true", help="한 사이클만 돌고 종료")
    parser.add_argument("--tick", type=int, default=DEFAULT_TICK, help="틱 간격(초)")
    args = parser.parse_args()

    init_db()
    asyncio.run(one_cycle() if args.once else loop(args.tick))


if __name__ == "__main__":
    main()
