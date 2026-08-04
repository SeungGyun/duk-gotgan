#!/bin/zsh
# 자동 기동 설치/해제.
#
#   ops/install.sh          설치하고 바로 띄웁니다
#   ops/install.sh remove   해제합니다 (파일은 남습니다)
#   ops/install.sh restart  세 서비스를 다시 띄웁니다
#
# **plist 를 심볼릭 링크로 겁니다.** 복사해 두면 저장소의 정의를 고쳐도
# 반영되지 않아, 두 벌이 어긋난 채 "왜 안 바뀌지"를 하게 됩니다.

set -u
HERE="${0:A:h}"
AGENTS="$HOME/Library/LaunchAgents"
# worker-agy 는 두 번째 요약 워커입니다(안티그래비티). `agy` 가 없으면
# 기동 스크립트가 스스로 빠지므로, 안 쓰는 사람이 있어도 그냥 둡니다.
SERVICES=(mysql api worker worker-agy)
DOMAIN="gui/$(id -u)"

mkdir -p "$AGENTS" "$HOME/Library/Logs/dukgotgan"

# **정말 사라질 때까지 기다립니다.** worker 는 SIGTERM 을 받아도 진행 중인
# 사이클을 마치고 멈추는데(받아쓰기 한 편이 3분, 사이클 상한 20분), 그동안
# 라벨이 잡혀 있어 bootstrap 이 "Input/output error 5" 로 거부됩니다.
# 기다리지 않으면 서비스 하나가 조용히 빠진 채로 설치가 끝납니다.
unload() {
  for s in $SERVICES; do
    launchctl bootout "$DOMAIN/com.dukgotgan.$s" 2>/dev/null
  done
  local deadline=$(( $(date +%s) + 1500 ))
  for s in $SERVICES; do
    while launchctl print "$DOMAIN/com.dukgotgan.$s" >/dev/null 2>&1; do
      if (( $(date +%s) > deadline )); then
        print "  ! com.dukgotgan.$s 가 25분 안에 안 내려갑니다 — 강제로 진행합니다"
        break
      fi
      print -n "."
      sleep 3
    done
  done
}

case "${1:-install}" in
  remove)
    unload
    for s in $SERVICES; do rm -f "$AGENTS/com.dukgotgan.$s.plist"; done
    print "해제했습니다."
    ;;
  restart)
    unload
    sleep 1
    for s in $SERVICES; do launchctl bootstrap "$DOMAIN" "$AGENTS/com.dukgotgan.$s.plist"; done
    print "다시 띄웠습니다."
    ;;
  *)
    unload
    for s in $SERVICES; do
      ln -sf "$HERE/com.dukgotgan.$s.plist" "$AGENTS/com.dukgotgan.$s.plist"
      launchctl bootstrap "$DOMAIN" "$AGENTS/com.dukgotgan.$s.plist" \
        && print "  ✓ com.dukgotgan.$s" \
        || print "  ✗ com.dukgotgan.$s — 이미 등록돼 있거나 정의가 잘못됐습니다"
    done
    print "\n로그인할 때마다 알아서 올라옵니다."
    print "로그: ~/Library/Logs/dukgotgan/{mysql,api,worker}.log"
    ;;
esac
