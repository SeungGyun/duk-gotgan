#!/bin/zsh
# 웹 서비스. 화면과 API 를 **한 포트에서** 냅니다 (http://localhost:8000).
#
# --reload 를 쓰지 않습니다. 개발용 감시 프로세스가 상시 도는 것은 낭비이고,
# 파일이 바뀌는 순간 재시작해서 요청이 끊깁니다.
source "$(dirname "$0")/lib.sh"

wait_for_docker || exit 1
wait_for_mysql || exit 1
cd "$BACKEND" || exit 1
exec "$BACKEND/.venv/bin/uvicorn" app.api.main:app --host 127.0.0.1 --port 8000
