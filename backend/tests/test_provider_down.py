"""회사 쪽 사정으로 실패한 것은 영상 탓이 아닙니다.

**두 번째로 같은 실수를 했습니다.** 처음엔 `Claude Code returned an error
result` 가 일시적 목록에 없어 60건이 영구 탈락했습니다. 이번엔 안티그래비티
쿼터가 떨어진 8분 사이에 51건이 `Individual quota reached` 를 사유로 탈락
했습니다 — 계정을 바꾸니 그대로 되는 영상들이었습니다.

연속 실패를 세는 장치가 있었는데도 막지 못했습니다. 사유에 매번 다른
재시도 시각과 stderr 꼬리가 붙어, 같은 오류인데도 매번 "다른 오류"로
세어졌기 때문입니다.
"""

from app.llm.runner import _is_transient, _provider_down, _streak_key

QUOTA = (
    "Individual quota reached. Please upgrade your subscription to increase "
    "your limits. Resets at 2026-08-05T07:15:00Z"
)


def test_쿠터_초과는_탈락이_아니다():
    assert _is_transient(QUOTA)
    assert _is_transient("Error: 429 Too Many Requests")
    assert _is_transient("RESOURCE_EXHAUSTED")


def test_인증_실패도_탈락이_아니다():
    """계정을 바꾸는 동안 agy 가 대화형 인증 프롬프트를 띄우고 60초 뒤
    죽었습니다. 그 영상들에는 아무 문제가 없습니다."""
    assert _is_transient("authentication failed or timed out")
    assert _is_transient("You are not logged into Antigravity.")
    assert _is_transient("401 Unauthorized")


def test_진짜_실패는_그대로_탈락한다():
    """전부 일시적으로 만들면 고장난 영상이 영원히 큐를 맴돕니다."""
    assert not _is_transient("형식 오류 3회 — sections 가 문자열입니다")
    assert not _is_transient("자막이 없습니다.")
    assert not _is_transient(None)


def test_회사가_안_받으면_사유를_집어낸다():
    assert _provider_down(QUOTA) == "quota reached"
    assert _provider_down("authentication timed out") == "authentication"
    # 영상마다 다른 실패는 회사 사정이 아닙니다
    assert _provider_down("자막이 없습니다.") is None


def test_같은_쿠터_오류는_시각이_달라도_한_묶음():
    """이게 핵심입니다. 원문을 그대로 비교해서 51건이 지나갔습니다."""
    a = QUOTA
    b = QUOTA.replace("07:15:00", "08:20:31") + " — stderr: conn 4aa2ce9f"
    assert _streak_key(a) == _streak_key(b), "같은 오류로 세어져야 사이클이 접힙니다"


def test_영상마다_다른_실패는_따로_센다():
    """자막 없음 같은 것까지 한 묶음이 되면, 멀쩡한 사이클이 세 건 만에
    접힙니다."""
    assert _streak_key("자막이 없습니다.") != _streak_key("형식 오류 3회 — topic 누락")


def test_회사가_안_받으면_그_자리에서_접는다():
    """세 번을 기다리지 않습니다. 쿼터가 떨어진 상태로 계속 돌면 대기 줄을
    그대로 훑으며 전부 실패로 만듭니다 — 실제로 분당 아홉 편씩 지나갔습니다."""
    import inspect

    from app.llm import runner

    src = inspect.getsource(runner.review_pending)
    assert "_provider_down(run.error)" in src
    assert src.index("_provider_down(run.error)") < src.index("if streak[1] >= STOP_AFTER")
