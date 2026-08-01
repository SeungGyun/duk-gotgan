#!/bin/zsh
# 세 서비스가 살아 있는지 한눈에.
set -u
print "서비스"
for s in mysql api worker; do
  line=$(launchctl list 2>/dev/null | grep "com.dukgotgan.$s")
  if [[ -z "$line" ]]; then
    print "  ✗ $s — 등록되지 않았습니다 (ops/install.sh)"
  else
    pid=${${(z)line}[1]}
    if [[ "$pid" != "-" ]]; then
      print "  ✓ $s — PID $pid"
    elif [[ "$s" == "mysql" ]]; then
      # 컨테이너를 띄우고 끝나는 일회성입니다. 상주하지 않는 게 정상이라,
      # 여기서는 프로세스가 아니라 **컨테이너**를 봐야 합니다.
      docker ps --filter name=dukgotgan_mysql --format '{{.Status}}' 2>/dev/null | grep -q Up \
        && print "  ✓ $s — 컨테이너 실행 중 (기동 스크립트는 일회성)" \
        || print "  ✗ $s — 컨테이너가 없습니다"
    else
      print "  ✗ $s — 등록됐지만 죽어 있습니다"
    fi
  fi
done
print "\n포트"
nc -z 127.0.0.1 3307 && print "  ✓ MySQL 3307" || print "  ✗ MySQL 3307"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/v1/health 2>/dev/null)
[[ "$code" == "200" ]] && print "  ✓ 웹 8000" || print "  ✗ 웹 8000 (HTTP ${code:-무응답})"
