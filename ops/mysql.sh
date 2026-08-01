#!/bin/zsh
# MySQL 컨테이너 지킴이.
#
# **한 번 띄우고 끝내면 안 됩니다.** 그러면 로그인 시점에는 잘 올라오지만,
# 도커를 도중에 종료했다 켜거나 컨테이너가 죽으면 아무도 다시 올려 주지
# 않습니다. api·worker 는 MySQL 을 기다리다 실패하고 재시작만 반복합니다.
#
# compose 의 `restart: unless-stopped` 는 **도커가 떠 있을 때만** 유효합니다.
# 도커 자체가 없는 상황은 여기서 감당합니다.
source "$(dirname "$0")/lib.sh"

CHECK_SEC=60

while true; do
  if ! wait_for_docker; then
    exit 1  # launchd 가 30초 뒤 다시 부릅니다
  fi
  if ! docker ps --filter name=dukgotgan_mysql --format '{{.Names}}' | grep -q dukgotgan_mysql; then
    log "컨테이너가 없습니다 — 띄웁니다"
    cd "$BACKEND" && docker compose up -d && wait_for_mysql && log "mysql 기동 완료"
  fi
  sleep $CHECK_SEC
done
