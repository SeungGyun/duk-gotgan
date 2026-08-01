#!/bin/zsh
# 기동 스크립트 공통부.
#
# **부팅 직후에는 아무것도 준비돼 있지 않습니다.** 로그인 시점에 도커는
# 아직 안 떠 있고, 떠도 데몬이 응답하기까지 수십 초가 걸립니다. MySQL 은
# 그 뒤에야 올라옵니다. 그래서 각 서비스는 "될 때까지 기다린다"를 직접
# 해야 합니다 — 순서를 launchd 에 맡길 수 없습니다(의존 순서 개념이 없습니다).
#
# 기다리다 실패하면 **0 이 아닌 값으로 끝냅니다.** launchd 의 KeepAlive 가
# 다시 띄워 주므로, 여기서 무한 루프를 돌 이유가 없습니다.

set -u

ROOT="/Users/layers/git/duk-gotgan"
BACKEND="$ROOT/backend"
PY="$BACKEND/.venv/bin/python"
LOGDIR="$HOME/Library/Logs/dukgotgan"

mkdir -p "$LOGDIR"

log() { print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*" }

# 도커 데몬이 응답할 때까지. 없으면 데스크톱을 직접 띄웁니다 — 사용자가
# 로그인 항목을 켜 두었는지에 기대지 않습니다.
wait_for_docker() {
  local deadline=$(( $(date +%s) + 300 ))
  if ! docker info >/dev/null 2>&1; then
    log "도커가 응답하지 않습니다 — Docker Desktop 을 띄웁니다"
    open -ga Docker || true
  fi
  while ! docker info >/dev/null 2>&1; do
    if (( $(date +%s) > deadline )); then
      log "도커를 5분 안에 못 띄웠습니다"
      return 1
    fi
    sleep 5
  done
  log "도커 준비됨"
}

# MySQL 이 실제로 접속을 받을 때까지. 컨테이너가 'Up' 이어도 초기화 중에는
# 연결이 거부됩니다 — 포트가 열렸는지가 아니라 **핑이 도는지**를 봅니다.
wait_for_mysql() {
  local deadline=$(( $(date +%s) + 240 ))
  while ! docker exec dukgotgan_mysql \
      mysqladmin ping -h 127.0.0.1 -u root -pdukgotgan_root >/dev/null 2>&1; do
    if (( $(date +%s) > deadline )); then
      log "MySQL 이 4분 안에 안 떴습니다"
      return 1
    fi
    sleep 3
  done
  log "MySQL 준비됨"
}
