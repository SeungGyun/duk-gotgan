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


def test_보충은_키워드끼리_번갈아_올린다():
    """앞에서부터 채우면 발견 55건인 키워드 하나가 줄을 독차지하고
    나머지는 굶습니다. 실제로 그런 상태였습니다 — 자막·요약 트랙이
    노는 동안 283건이 묶여 있었고, 키워드당 하루 10편씩이라 다 풀리는
    데 엿새가 걸렸습니다."""
    import inspect

    from app.collector import jobs

    src = inspect.getsource(jobs.backfill)
    assert "queue.next_ids(db" in src, "번갈아 뽑는 큐를 써야 합니다"


def test_보충은_유튜브_유닛을_쓰지_않는다():
    """이미 발견해 둔 것을 올릴 뿐이라 검색 호출이 없어야 합니다.
    유닛을 쓴다면 하루 한 번이라는 검색 주기를 우회하는 셈이 됩니다."""
    import inspect

    from app.collector import jobs

    src = inspect.getsource(jobs.backfill)
    for banned in ("search_ids", "fetch_details", "playlist_video_ids", "_spend", "quota"):
        assert banned not in src, f"{banned} 를 부르면 안 됩니다"


def test_자막_줄이_차_있으면_보충하지_않는다():
    """줄이 이미 길면 더 올릴 이유가 없습니다 — 마음이 바뀌어 대기
    목록에서 빼고 싶을 때 줄만 길어집니다."""
    import inspect

    from app.collector import jobs

    assert "room <= 0" in inspect.getsource(jobs.backfill)


# ── 보관 정리 ────────────────────────────────────────────────


def test_처리가_안_끝난_영상의_원문은_지우지_않는다():
    """만료일만 보고 지우면, 오래 밀려 있던 영상의 원문이 요약 직전에
    사라져 그 영상만 영영 처리되지 않습니다. 조용히 일어나서 알아채기도
    어렵습니다."""
    from app.collector.cleanup import DONE_STATES

    for still_working in (
        "TRANSCRIPT_PENDING",
        "TRANSCRIBING",
        "TRANSCRIBED",
        "REVIEWING",
        "DISCOVERED",
    ):
        assert still_working not in DONE_STATES, f"{still_working} 는 아직 원문이 필요합니다"
    assert "PUBLISHED" in DONE_STATES


def test_룰_탈락_영상은_지우지_않는다():
    """그 행이 "이미 본 영상"이라는 표시입니다. 지우면 다음 검색에 처음
    본 것처럼 다시 들어와 또 탈락하는 순환이 생겨 영영 줄지 않습니다."""
    import inspect

    from app.collector import cleanup

    src = inspect.getsource(cleanup)
    assert "REJECTED_RULE" not in src.split('"""')[2], "룰 탈락은 지우는 대상이 아닙니다"


def test_이력을_실행_기록보다_먼저_지운다():
    """실행을 먼저 지우면 이력이 사라진 실행을 가리킨 채 남습니다."""
    import inspect

    src = inspect.getsource(__import__("app.collector.cleanup", fromlist=["sweep"]).sweep)
    assert src.index("PipelineEvent") < src.index("CrawlRun")


def test_스왑이_없는_것과_꽉_찬_것을_가른다(monkeypatch):
    """**여유 0이라고 다 같은 0이 아닙니다.**

    맥은 스왑 파일을 필요할 때 만듭니다. 메모리가 넉넉하면
    `total = 0.00M  free = 0.00M` 이 나오는데, 여유만 보면 "꽉 찼다"로
    읽힙니다 — 실제로는 스왑을 쓸 일이 없었다는 뜻입니다.

    이걸 못 갈라서 요약이 통째로 멎었습니다. 자막 26건이 쌓이는 동안
    1분마다 "메모리가 빡빡해 건너뜁니다" 만 찍혔고, 그때 램 16GB 에
    스왑 사용량은 0이었습니다.
    """
    from app.collector import resources

    def 스왑(total, free):
        monkeypatch.setattr(resources, "_swap_mb", lambda: (total, free))

    스왑(0.0, 0.0)
    assert resources.memory_tight() is False, "스왑을 안 만든 것은 여유롭다는 뜻입니다"

    # 가드가 원래 지켜야 했던 상황 — 5,120M 중 4,800M 사용.
    스왑(5120.0, 320.0)
    assert resources.memory_tight() is True

    스왑(5120.0, 2000.0)
    assert resources.memory_tight() is False

    # 못 재면 막지 않습니다 — 측정이 깨진 날 파이프라인이 서면 안 됩니다.
    monkeypatch.setattr(resources, "_swap_mb", lambda: None)
    assert resources.memory_tight() is False


def test_기다리는_사이_기간이_지나면_줄에서_뺀다():
    """수집할 때는 창 안이었습니다. 그런데 자막·요약 줄이 밀리는 동안
    날짜가 지나갑니다 — 실측에서 대기 108편 중 **48편**이 그랬습니다
    (창 1일짜리 키워드가 사흘 된 영상을 붙들고 있었습니다).

    그대로 요약하면 "하루만 지나도 헌 이야기" 라고 사용자가 정해 둔
    기준을 어기면서 편당 8만 토큰을 씁니다.
    """
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.collector.jobs import drop_stale
    from app.db.models import Base, Keyword, Video, VideoKeyword
    from config.settings import settings
    from config.time import now_kst

    engine = create_engine(settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        now = now_kst()
        시황 = Keyword(term="주식", status="active", search_window_days=1, last_run_at=now)
        과학 = Keyword(term="과학", status="active", search_window_days=90, last_run_at=now)
        db.add_all([시황, 과학])
        db.flush()

        def 영상(vid, days_old, kws):
            db.add(Video(id=vid, title=vid, state="TRANSCRIPT_PENDING", duration_sec=600,
                         published_at=now - timedelta(days=days_old), channel_title="채널"))
            db.flush()
            for k in kws:
                db.add(VideoKeyword(video_id=vid, keyword_id=k.id))

        영상("fresh0001", 0, [시황])      # 창 안
        영상("stale0001", 3, [시황])      # 창 1일인데 사흘 전 — 빠져야 합니다
        영상("shared001", 3, [시황, 과학])  # 과학(90일)은 아직 원합니다 — 남아야 합니다
        db.commit()

        assert drop_stale(db) == 1, "창 지난 것만 빠져야 합니다"

        db.expire_all()
        assert db.get(Video, "stale0001").state == "SKIPPED"
        assert "3일 전" in (db.get(Video, "stale0001").state_reason or "")
        assert db.get(Video, "fresh0001").state == "TRANSCRIPT_PENDING"
        assert db.get(Video, "shared001").state == "TRANSCRIPT_PENDING", (
            "한 키워드라도 아직 원하면 남겨야 합니다 — 넓은 창 쪽 사람의 곳간에서 "
            "지운 적 없는 것이 사라지면 안 됩니다"
        )

        # 두 번 돌려도 같은 것을 또 빼지 않습니다.
        assert drop_stale(db) == 0
    finally:
        db.close()
        engine.dispose()
