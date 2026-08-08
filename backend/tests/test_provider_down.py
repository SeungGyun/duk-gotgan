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


def test_우리말로_쓴_사유는_글자로_맞힐_수_없다():
    """**세 번째로 같은 실수를 했습니다.** 이번엔 우리가 쓴 사유였습니다.

    `_TRANSIENT` 시그니처는 전부 영어인데 `agy.py` 가 쓰는 시한 초과 사유는
    우리말입니다. 그래서 `agy 가 900초 안에 끝나지 않았습니다.` 가 판정을
    통과하지 못하고 15편이 영구 탈락으로 쌓였습니다.

    자막 크기가 1,295~20,086 토큰으로 고르게 퍼져 있어 분량 문제도
    아니었습니다 — 가장 작은 것도 걸렸습니다.
    """
    말로만 = "agy 가 900초 안에 끝나지 않았습니다."
    assert not any(sig in 말로만.lower() for sig in ("timeout", "timed out")), (
        "이 문장에는 영어 시그니처가 없습니다 — 그래서 적어 두어야 합니다"
    )
    assert _is_transient(말로만, True)


def test_적어_둔_판정이_글자_맞히기를_이긴다():
    """남이 준 메시지에만 글자를 맞힙니다. 우리 사유는 우리가 정합니다."""
    # 적어 두면 영어 시그니처가 없어도 일시적입니다
    assert _is_transient("무슨 일인지 우리말로만 적힌 사유", True)
    # 반대로, 영어 시그니처가 들어 있어도 아니라고 적었으면 아닙니다
    assert not _is_transient("timeout 이라는 낱말이 들어간 진짜 탈락 사유", False)
    # 안 적었으면 예전대로 글자를 봅니다
    assert _is_transient("Control request timeout: initialize")
    assert not _is_transient("형식 오류 3회 — sections 가 문자열입니다")


def test_되돌리기에도_상한이_있다():
    """되살리려다 큐를 맴도는 영상을 만들면 뒤의 멀쩡한 것들이 밀립니다.
    자막 쪽(`MAX_TRANSCRIPT_RETRY`)과 같은 이유로 같은 값입니다."""
    import inspect

    from app.llm import runner

    assert runner.MAX_REVIEW_RETRY == 5
    src = inspect.getsource(runner.review_pending)
    assert "_retries(db, video.id)" in src
    assert "MAX_REVIEW_RETRY" in src


def test_되돌린_횟수는_이력에서_센다():
    """컬럼을 더할 만한 값이 아닙니다. `REVIEWING → TRANSCRIBED` 이벤트만
    세면 됩니다 — 되돌릴 때만 남는 이벤트라서요."""
    import inspect

    from app.llm import runner

    src = inspect.getsource(runner._retries)
    assert '"review"' in src
    assert '"REVIEWING"' in src and '"TRANSCRIBED"' in src


def test_agy_의_catch_all_은_탈락이_아니다():
    """`Agent execution terminated due to error.` 안에는 서버가 준
    `INVALID_ARGUMENT (code 400)` 이 들어 있습니다. **자막 크기와 상관이
    없습니다** — 성공한 것과 실패한 것의 크기 분포가 겹칩니다(중앙값
    5,342 vs 5,631). 같은 영상을 다시 돌리면 그대로 됩니다.

    이걸 탈락으로 적었더니 12분에 28편이 날아갔습니다."""
    assert _is_transient("Agent execution terminated due to error.")
    assert _is_transient(
        "Agent execution terminated due to error. — INVALID_ARGUMENT (code 400)"
    )
