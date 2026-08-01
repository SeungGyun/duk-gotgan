"""워커 중복 실행 방지.

MySQL 의 `GET_LOCK` 을 씁니다. 만료 시각을 직접 관리하는 방식(락 테이블 +
TTL)은 **워커가 `kill -9` 됐을 때 락이 영원히 잠기는 문제**를 TTL 로 덮는
것뿐이고, TTL 을 짧게 잡으면 정상 작업 중에 남이 뺏어 갑니다.

`GET_LOCK` 은 **커넥션이 끊기면 자동으로 풀립니다.** 프로세스가 죽으면
커넥션도 죽으므로, 만료를 세는 코드 자체가 필요 없습니다.
"""

import logging
from contextlib import contextmanager

from sqlalchemy import text

from app.db.session import engine

logger = logging.getLogger(__name__)

CYCLE_LOCK = "dukgotgan:cycle"


@contextmanager
def try_lock(name: str = CYCLE_LOCK):
    """잡으면 True, 이미 남이 쥐고 있으면 False 를 넘겨줍니다.

    기다리지 않습니다(timeout=0) — 틱은 1분마다 다시 오므로 줄을 설 이유가
    없고, 줄을 세우면 밀린 틱이 한꺼번에 몰려 같은 일을 여러 번 합니다.
    """
    conn = engine.connect()
    try:
        got = conn.execute(text("SELECT GET_LOCK(:n, 0)"), {"n": name}).scalar()
        acquired = got == 1
        try:
            yield acquired
        finally:
            if acquired:
                # **놓기는 최선을 다할 뿐입니다.** 커넥션이 이미 죽었으면
                # RELEASE_LOCK 도 실패하는데, 그 예외를 그대로 올리면 사이클의
                # 진짜 결과가 스택트레이스에 묻힙니다. 어차피 커넥션이 끊기면
                # 락은 서버가 알아서 풉니다 — 그게 GET_LOCK 을 고른 이유입니다.
                try:
                    conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": name})
                except Exception as e:  # noqa: BLE001
                    logger.warning("[lock] 놓기 실패 — 끊기면 자동으로 풀립니다 (%s)", type(e).__name__)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
