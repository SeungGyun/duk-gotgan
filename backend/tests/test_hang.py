"""**끝났는데 안 끝난 것처럼 붙들리는 것** — 요약이 멈추는 진짜 이유.

2026-08-20, 안티그래비티 워커가 한 편에 13분 30초를 멈춰 있었습니다. agy 는
이미 끝났는데 그놈이 띄운 `/usr/bin/security -i`(키체인 창을 띄운 채 서
있던 것)가 우리 stdout·stderr 의 쓰기 끝을 쥐고 있어서, `communicate()` 에
EOF 가 오지 않았습니다. 그전에는 같은 이유로 72분짜리도 있었습니다.

그동안 그 편들은 재시도 횟수를 태우고 `FAILED_REVIEW` 로 적혔습니다 —
사유는 `Claude Code returned an error result` 나 스키마 위반이라 원인과
아무 상관 없는 문장이었습니다. 실제로 키체인을 고친 뒤 여섯 번·열 번씩
죽었던 다섯 편이 **한 번에 다 됐습니다.**

여기서 보는 것은 그 구조입니다. 파이프가 커플링이었고, 없애면 커플링도
없어집니다.
"""

import asyncio
import inspect
import time
from pathlib import Path

from app.llm import agy


def test_손자가_파이프를_쥐고_있으면_파이프로는_안_끝난다(tmp_path: Path):
    """**이게 그 버그입니다.** 자식이 손자에게 fd 를 물려주고 먼저 죽으면,
    파이프를 읽는 쪽은 손자가 죽을 때까지 기다립니다.

    `sh -c 'sleep 3 & exit 0'` 이 그 상황을 그대로 만듭니다 — `sh` 는
    곧바로 끝나지만 `sleep` 이 stdout 을 물려받은 채 3초를 삽니다.
    """

    async def go():
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", "sleep 3 & exit 0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        started = time.monotonic()
        await proc.communicate()
        return time.monotonic() - started

    took = asyncio.run(go())
    assert took > 2.5, (
        "파이프로 받으면 손자가 죽을 때까지 기다립니다 — 실제로는 그 손자가 "
        "사람이 창을 누를 때까지 살아 있었고, 그게 13분이었습니다"
    )


def test_파일로_받으면_자식이_죽는_즉시_끝난다(tmp_path: Path):
    """같은 상황에서 파일로 받으면 **agy 자신의 종료**만 기다립니다.
    손자가 무엇을 쥐고 있든 상관하지 않습니다."""
    out = tmp_path / "out"

    async def go():
        with out.open("wb") as f:
            proc = await asyncio.create_subprocess_exec(
                "sh", "-c", "echo 다녀왔습니다; sleep 3 & exit 0",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=f,
                stderr=asyncio.subprocess.DEVNULL,
            )
        started = time.monotonic()
        await proc.wait()
        return time.monotonic() - started

    took = asyncio.run(go())
    assert took < 1.5, "자식이 끝났으면 끝난 것입니다"
    # 그리고 받아 적은 것은 그대로 읽힙니다 — 안 기다린다고 잃지 않습니다.
    assert "다녀왔습니다" in agy._read_and_drop(out)
    assert not out.exists(), "읽고 나면 버립니다"


def test_agy_는_파이프를_쓰지_않는다():
    """위 둘이 보여 준 차이를 실제 코드가 지키고 있는지.

    함수를 통째로 돌리려면 `agy` CLI 와 인증이 필요해서, 여기서는 어느
    쪽으로 받는지만 봅니다 — 이 한 줄이 되돌아가면 13분짜리 멈춤도
    같이 돌아옵니다.
    """
    src = inspect.getsource(agy.review)
    assert "stdout=fout" in src and "stderr=ferr" in src
    # 주석에는 `communicate()` 가 왜 안 되는지 적혀 있습니다 — 그건 봐야 할
    # 대상이 아니라 이유이므로, 실제로 도는 줄만 봅니다.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "communicate()" not in code, "파이프를 다시 물면 안 됩니다"
    assert "stdin=asyncio.subprocess.DEVNULL" in src, "물어볼 데가 없어야 합니다"
    assert "start_new_session=True" in src, "그룹 단위로 정리하려면 필요합니다"


def test_정상으로_끝나도_뒷정리를_한다():
    """예전에는 타임아웃일 때만 그룹을 죽였습니다. 그래서 잘 끝난 뒤에도
    도우미가 남아 다음 창을 띄우고 쌓였습니다."""
    src = inspect.getsource(agy.review)
    finally_block = src[src.rindex("finally:"):]
    assert "_sweep_group" in finally_block, "성공 경로에서도 정리해야 합니다"


def test_남은_것이_있으면_이름을_적는다(monkeypatch, caplog):
    """붙든 것이 `/usr/bin/security -i` 라는 사실을 알아내는 데 `lsof` 로
    파이프 반대쪽을 추적해야 했습니다. 이름 한 줄이면 될 일이었습니다."""
    import subprocess

    class 가짜:
        stdout = "47414 /usr/bin/security -i\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: 가짜())
    monkeypatch.setattr(agy.os, "killpg", lambda *a: None)

    with caplog.at_level("WARNING"):
        agy._sweep_group(4242, "abc123")
    assert "security -i" in caplog.text
    assert "abc123" in caplog.text


def test_아무것도_안_남았으면_조용하다(monkeypatch, caplog):
    """정상이 시끄러우면 진짜일 때 안 읽힙니다."""
    import subprocess

    class 빈것:
        stdout = "\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: 빈것())
    monkeypatch.setattr(agy.os, "killpg", lambda *a: None)

    with caplog.at_level("WARNING"):
        agy._sweep_group(4242, "abc123")
    assert caplog.text == ""


def test_시한을_넘긴_것은_탈락이_아니다():
    """멈춤 한 번에 그 편들이 영구 실패로 적히면, 고친 뒤에도 다시
    시도되지 않습니다 — 오늘 되살린 다섯 편이 정확히 그렇게 죽었습니다."""
    from app.llm import runner

    src = inspect.getsource(runner.review_video)
    block = src[src.index("except asyncio.TimeoutError:"):]
    assert "run.transient = True" in block.split("except Exception")[0]
    assert "wait_for" in src and "review_timeout_sec" in src


def test_두_경로_모두_시한이_있다():
    """agy 쪽은 진작 상한을 두고 있었는데 클로드 쪽만 비어 있었습니다.
    `max_turns` 는 턴 수이지 시한이 아닙니다."""
    from config.settings import settings

    assert settings.agy_timeout_sec > 0
    assert settings.review_timeout_sec > 0
    # 실측 최대가 2.2분(agy)·21.5분(클로드)이라 그 위에 있어야 합니다.
    assert settings.agy_timeout_sec >= 180
    assert settings.review_timeout_sec >= 22 * 60
