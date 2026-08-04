"""요약 워커를 둘 이상 띄우기 위한 소유권 규칙.

토큰이 모자라 회사를 나눠 맡기기로 하면서 생긴 요구입니다. 지금까지
요약 트랙은 소비자가 하나뿐이라는 전제로 돌았고, 중복을 막는 것은
`REVIEW_LOCK` 하나였습니다 — 이름을 갈라 둘을 같이 돌리는 순간 그
전제가 무너집니다.

여기서 못박는 것은 **경합이 있어도 한 명만 이긴다**와 **남이 쥔 것은
건드리지 않는다** 둘입니다. 둘 다 실제로 겹쳐 돌려 봐야 나는 종류라
DB 없이도 잡히는 선까지 정적으로 확인합니다.
"""

import inspect


def test_요약_락은_회사마다_다르다():
    """이름이 같으면 클로드 워커와 안티그래비티 워커가 번갈아 하나씩만
    돌아, 나눈 의미가 없습니다."""
    from app.collector import jobs
    from config.settings import settings

    assert jobs.REVIEW_LOCK != jobs.TRANSCRIPT_LOCK
    assert settings.review_provider in jobs.REVIEW_LOCK
    # 나머지 잡은 소비자가 하나뿐이라 그대로입니다
    assert jobs.TRANSCRIPT_LOCK == "dukgotgan:transcript"
    assert jobs.DISCOVER_LOCK == "dukgotgan:discover"


def test_집기는_조건부_UPDATE_다():
    """`next_ids` 는 잠금 없는 SELECT 라, 워커가 둘이면 같은 목록을
    받습니다. 조건 없이 상태를 덮어쓰면 먼저 잡힌 것을 알아챌 방법이
    없어 같은 영상을 둘이 요약합니다."""
    from app.collector import queue

    src = inspect.getsource(queue.claim)
    assert "UPDATE videos" in src
    assert "state = :frm" in src, "현재 상태를 조건으로 걸어야 진 쪽이 0을 받습니다"
    assert "rowcount == 1" in src, "이겼는지를 반드시 확인해야 합니다"


def test_놓기도_내_것일_때만():
    """오래 걸려 회수당한 뒤 다른 워커가 이어받았을 수 있습니다. 그때
    조건 없이 쓰면 지금 잘 돌고 있는 남의 작업을 실패로 덮어씁니다."""
    from app.collector import queue

    src = inspect.getsource(queue.release)
    assert "claimed_by = :owner" in src


def test_요약은_한_건씩_집는다():
    """목록을 통째로 받아 순회하면 그 목록 자체가 이미 겹쳐 있습니다."""
    from app.llm import runner

    src = inspect.getsource(runner.review_pending)
    assert "queue.claim(" in src, "집기를 거치지 않고 상태를 세우면 안 됩니다"
    assert 'video.state = "REVIEWING"' not in src, "직접 덮어쓰던 자리는 사라져야 합니다"
    assert "queue.release(" in src, "실패 처리도 조건부여야 합니다"


def test_좀비_회수는_남이_쥔_것을_건드리지_않는다():
    """예전에는 30분 넘은 REVIEWING 을 누가 쥐고 있든 되돌렸습니다.
    다른 회사가 40분째 돌고 있는 영상을 회수하면 같은 영상을 두 번
    요약하고, 나중에 끝난 쪽이 앞의 결과를 덮습니다."""
    from app.llm import runner

    src = inspect.getsource(runner.recover_zombies)
    assert "claimed_by" in src, "임자를 봐야 합니다"
    assert "review_provider" in src, "내 회사가 붙든 것만 바로 회수합니다"
    assert "orphan" in src, "회사를 내렸을 때 영영 갇히지 않도록 긴 유예가 필요합니다"


def test_저장할_때_아직_내_것인지_본다():
    """마지막 안전망입니다. 회수된 뒤 뒤늦게 끝난 쪽이 그대로 저장하면
    `_publish` 가 version 2 를 쓰고 version 1 을 감춥니다 — 두 번 요약한
    결과가 조용히 서로를 덮습니다."""
    from app.llm import store

    # 두 회사가 같은 함수를 지납니다 — 여기 한 곳만 지키면 됩니다.
    src = inspect.getsource(store.save)
    assert "with_for_update()" in src, "확인과 저장 사이에 틈이 없어야 합니다"
    assert "video.claimed_by != owner" in src
    assert 'video.state != "REVIEWING"' in src


def test_사용량은_집기_전에_본다():
    """집고 나서 확인하면 상한에 닿은 순간의 한 건이 REVIEWING 으로
    갇혀, 회수될 때까지 아무도 못 만집니다."""
    from app.llm import runner

    src = inspect.getsource(runner.review_pending)
    assert src.index("usage.check(db)") < src.index("queue.claim(")


def test_토큰_창은_회사별로_센다():
    """합쳐서 세면 한쪽이 많이 쓴 것 때문에 아직 여유가 있는 쪽까지
    멈춥니다 — 토큰이 모자라서 회사를 늘렸는데 정반대가 됩니다."""
    from app.db.models import UsageWindow
    from app.llm import usage

    pk = {c.name for c in UsageWindow.__table__.primary_key}
    assert pk == {"start", "provider"}
    assert "provider" in inspect.signature(usage.check).parameters
    assert "provider" in inspect.signature(usage.record).parameters


def test_임자_이름은_회사로_시작한다():
    """좀비 회수가 이 접두사로 "내 회사가 붙든 것"을 가립니다."""
    from app.llm.runner import worker_id
    from config.settings import settings

    owner = worker_id()
    assert owner.startswith(f"{settings.review_provider}:")
    # 컬럼이 VARCHAR(64) 입니다 — 넘치면 조용히 잘려 임자가 뒤섞입니다.
    assert len(owner) <= 64
