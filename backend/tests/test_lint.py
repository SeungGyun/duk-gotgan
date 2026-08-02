"""정의되지 않은 이름을 잡습니다.

`name 'threshold' is not defined` 가 **여섯 시간 동안** 안 잡혔습니다.
판정 게이트를 걷어내며 변수는 지웠는데 쓰는 곳 세 군데를 남겼고, 도구가
모든 예외를 삼켜 문자열로 돌려주는 구조라 조용히 돌았습니다. 그동안
검토는 전부 롤백돼 한 편도 담기지 않았고 토큰만 나갔습니다.

임포트만으로는 안 잡힙니다 — 그 줄을 실제로 밟아야 나는 오류입니다.
단위 테스트로 모든 분기를 밟는 것도 현실적이지 않습니다. 정적 검사가
맞는 도구이고, ruff 는 이미 dev 의존성에 있었습니다.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUFF = ROOT / ".venv" / "bin" / "ruff"


@pytest.mark.skipif(not RUFF.exists() and not shutil.which("ruff"), reason="ruff 없음")
def test_정의되지_않은_이름이_없다():
    ruff = str(RUFF) if RUFF.exists() else "ruff"
    r = subprocess.run(
        [ruff, "check", "app", "scripts", "config", "--select", "F821", "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"정의되지 않은 이름:\n{r.stdout}{r.stderr}"
