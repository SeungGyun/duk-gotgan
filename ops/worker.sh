#!/bin/zsh
# 수집 워커(스케줄러). 1분마다 깨어나 할 일이 있으면 합니다.
source "$(dirname "$0")/lib.sh"

wait_for_docker || exit 1
wait_for_mysql || exit 1
cd "$BACKEND" || exit 1
exec "$PY" -m scripts.worker
