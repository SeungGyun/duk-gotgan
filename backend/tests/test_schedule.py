"""due 판정 — 크론 대신 키워드가 자기 차례를 안다.

여기가 틀리면 조용히 안 돌거나 하루에 여러 번 돕니다. 둘 다 한참 뒤에야
알아차리게 되는 종류라 테스트로 못박아 둡니다.
"""

from datetime import datetime

import pytest

from app.collector.schedule import INTERVAL_DAYS, is_due, next_due_at
from app.db.models import Keyword

NOW = datetime(2026, 8, 1, 10, 0)  # 화요일 오전 10시


def kw(**over) -> Keyword:
    base = dict(
        id="kw_t", term="테스트", status="active", language="ko", schedule="daily",
        min_duration_sec=1200, max_duration_sec=14400, min_expert_score=75,
        max_per_run=10, run_hour=4,
    )
    base.update(over)
    return Keyword(**base)


def test_등록_직후는_기다리지_않는다():
    assert is_due(kw(status="pending", last_run_at=None), NOW)
    assert next_due_at(kw(status="pending", last_run_at=None)) is None


def test_한_번도_안_돌았으면_즉시():
    assert is_due(kw(status="active", last_run_at=None), NOW)


def test_오늘_이미_돌았으면_내일_run_hour():
    k = kw(last_run_at=datetime(2026, 8, 1, 4, 2))
    assert next_due_at(k) == datetime(2026, 8, 2, 4, 0)
    assert not is_due(k, NOW)


def test_어제_돌았고_오늘_시각을_지났으면_due():
    k = kw(last_run_at=datetime(2026, 7, 31, 4, 5))
    assert is_due(k, NOW)


def test_어제_돌았지만_아직_시각_전이면_아직():
    k = kw(last_run_at=datetime(2026, 7, 31, 4, 5))
    assert not is_due(k, datetime(2026, 8, 1, 3, 30))


@pytest.mark.parametrize("schedule,days", list(INTERVAL_DAYS.items()))
def test_주기별_간격(schedule, days):
    k = kw(schedule=schedule, last_run_at=datetime(2026, 8, 1, 4, 0))
    assert next_due_at(k) == datetime(2026, 8, 1 + days, 4, 0)


def test_키워드마다_다른_시각():
    k = kw(run_hour=12, last_run_at=datetime(2026, 7, 31, 12, 0))
    assert not is_due(k, datetime(2026, 8, 1, 11, 59))
    assert is_due(k, datetime(2026, 8, 1, 12, 0))


def test_시각이_밀리지_않는다():
    """last_run_at + 24시간으로 하면 실행에 걸린 시간만큼 매일 뒤로 밀립니다.
    절대 시각으로 잡으면 몇 시에 끝났든 다음 차례는 같습니다."""
    early = kw(last_run_at=datetime(2026, 8, 1, 4, 0))
    late = kw(last_run_at=datetime(2026, 8, 1, 4, 47))  # 47분 걸린 실행
    assert next_due_at(early) == next_due_at(late) == datetime(2026, 8, 2, 4, 0)


@pytest.mark.parametrize("status", ["paused", "quota_wait", "archived"])
def test_멈춘_키워드는_대상이_아니다(status):
    assert not is_due(kw(status=status, last_run_at=None), NOW)


def test_run_hour_범위를_벗어나도_깨지지_않는다():
    k = kw(run_hour=99, last_run_at=datetime(2026, 7, 31, 4, 0))
    assert next_due_at(k) == datetime(2026, 8, 1, 23, 0)


def test_락_놓기가_실패해도_예외로_번지지_않는다(monkeypatch):
    """MySQL 이 내려가면 RELEASE_LOCK 도 실패합니다. 그 예외를 그대로 올리면
    사이클의 진짜 결과가 스택트레이스에 묻힙니다. 커넥션이 끊기면 락은
    서버가 알아서 푸는데(그게 GET_LOCK 을 고른 이유), 굳이 시끄러울 이유가
    없습니다."""
    from app.db import lock

    class DeadConn:
        def __init__(self):
            self.calls = 0

        def execute(self, *a, **k):
            self.calls += 1
            if self.calls == 1:  # GET_LOCK 은 성공
                return type("R", (), {"scalar": staticmethod(lambda: 1)})()
            raise RuntimeError("Lost connection to MySQL server")

        def close(self):
            raise RuntimeError("이미 죽었습니다")

    monkeypatch.setattr(lock, "engine", type("E", (), {"connect": staticmethod(DeadConn)})())
    with lock.try_lock() as acquired:
        assert acquired  # 여기까지 왔으면 잡은 것


def test_일시적_실패는_탈락이_아니다():
    """밤새 `Control request timeout: initialize` 가 18번 났습니다. 받아쓰기가
    GPU 를 붙들고 있는 동안 SDK 가 시작 시간을 못 맞춘 것으로, 영상에는 아무
    문제가 없습니다. 그런데 FAILED_REVIEW 로 적어 두 번 다시 검토되지
    않았습니다."""
    from app.llm.runner import _is_transient

    assert _is_transient("Control request timeout: initialize")
    assert _is_transient("Lost connection to MySQL server")
    # 진짜 실패는 그대로 탈락이어야 합니다
    assert not _is_transient("형식 오류 3회 — sections 가 문자열입니다")
    assert not _is_transient(None)


def test_같은_오류가_이어지면_사이클을_접는다():
    """`name 'threshold' is not defined` 가 여섯 시간 동안 매 사이클 열
    건씩 실패하며 토큰만 태웠습니다. 코드 버그는 다음 영상이라고 나아지지
    않습니다 — 영상마다 다른 실패(자막 없음 등)는 여기 걸리지 않습니다."""
    import inspect

    from app.llm import runner

    src = inspect.getsource(runner.review_pending)
    assert "STOP_AFTER" in src and "streak" in src


def test_막는_잡은_스레드로_보낸다():
    """셋을 asyncio.gather 로 띄워 놓고 동기 함수를 그대로 부르면 이벤트
    루프가 멈춥니다. 받아쓰기 한 편이 5~13분인데 그동안 검토도 검색도
    한 발짝을 못 나갑니다 — 나눈 의미가 사라집니다."""
    import inspect

    from scripts import worker

    src = inspect.getsource(worker)
    assert "asyncio.to_thread(_transcript_blocking)" in src
    assert "asyncio.to_thread(_discover_blocking)" in src


def test_메모리가_빡빡하면_요약을_미룬다():
    """CPU 만 보고 "받아쓰기는 GPU, 요약은 네트워크 대기"라 판단했는데,
    둘이 정말로 다투는 자원은 메모리였습니다. 스왑이 94% 찬 상태에서
    클로드 프로세스가 뜨지 못해 60건이 실패로 쌓였습니다."""
    import inspect

    from app.collector import jobs

    assert "resources.memory_tight()" in inspect.getsource(jobs.review_job)


def test_프로세스가_못_뜬_것은_영상_탓이_아니다():
    """`Claude Code returned an error result` 가 일시적 목록에 없어서
    영구 탈락으로 쌓였습니다. 같은 영상을 손으로 돌리면 그대로 됩니다."""
    from app.llm.runner import _is_transient

    assert _is_transient("실행 실패: Claude Code returned an error result: success")
    assert _is_transient("Cannot allocate memory")
    assert not _is_transient("형식 오류 3회 — sections 가 문자열입니다")
