"""무거운 일을 시작해도 되는지 — **메모리** 기준.

CPU 만 보고 "받아쓰기는 GPU, 요약은 네트워크 대기라 같이 돌려도 된다"고
판단했는데, 둘이 정말로 다투는 자원은 **메모리**였습니다.

  스왑 5,120M 중 4,800M 사용 (94%)
  누적 스왑아웃 1,858,225 페이지 (약 7GB)

위스퍼는 오디오를 통째로 16kHz float32 로 올립니다 — 두 시간짜리면
그것만 460MB 이고, 모델과 중간 텐서가 더 붙습니다. 그 순간 클로드
프로세스가 뜨지 못하고 `Claude Code returned an error result` 로 죽었고,
그게 60건 쌓였습니다.

**미리 비켜 주는 편이 낫습니다.** 죽은 뒤 재시도하면 그때까지 쓴 시간이
버려지고, 실패 기록도 남습니다.

macOS 전용입니다 — launchd·MLX 로 이미 이 기계에 묶여 있습니다.
"""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# 남은 스왑이 이보다 적으면 새 작업을 미룹니다. 500MB 는 클로드 CLI
# 하나가 뜨는 데 필요한 정도(실측 RSS 368MB)에 여유를 더한 값입니다.
MIN_FREE_SWAP_MB = 500


def _swap_free_mb() -> float | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"free\s*=\s*([\d.]+)M", out)
    return float(m.group(1)) if m else None


def memory_tight() -> bool:
    """지금 무거운 프로세스를 새로 띄우면 위험한가.

    재지 못하면 **막지 않습니다.** 알 수 없다는 이유로 일을 멈추면,
    측정이 깨진 날 파이프라인이 통째로 서 버립니다.
    """
    free = _swap_free_mb()
    if free is None:
        return False
    tight = free < MIN_FREE_SWAP_MB
    if tight:
        logger.warning("[resources] 스왑 여유 %.0fMB — 무거운 작업을 미룹니다", free)
    return tight
