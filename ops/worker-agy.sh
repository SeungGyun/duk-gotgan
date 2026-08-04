#!/bin/zsh
# 두 번째 요약 워커 — 안티그래비티(`agy` CLI).
#
# **요약만 돕니다.** 검색·자막·정리는 소비자가 하나면 충분하고, 받아쓰기는
# GPU 를 붙들어서 둘이 돌면 서로를 밀어냅니다. 요약은 원격 대기가 대부분이라
# (로컬 CPU 5.5%) 같이 돌려도 부딪히지 않습니다.
#
# 락 이름이 `dukgotgan:review:antigravity` 라 클로드 워커와 겹치지 않습니다.
# 같은 줄에서 한 편씩 가져가되, 조건부 UPDATE 로 집기 때문에 같은 영상을
# 둘이 요약하지 않습니다 (app/collector/queue.py).
source "$(dirname "$0")/lib.sh"

wait_for_docker || exit 1
wait_for_mysql || exit 1
cd "$BACKEND" || exit 1

# agy 가 여기 있습니다. launchd 의 PATH 에는 홈 밑 경로가 없습니다.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v agy >/dev/null 2>&1; then
  log "agy 를 찾을 수 없습니다 — 안티그래비티 워커를 띄우지 않습니다"
  exit 1
fi

export REVIEW_PROVIDER=antigravity
exec "$PY" -m scripts.worker --only review
