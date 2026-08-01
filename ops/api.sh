#!/bin/zsh
# 웹 서비스. 화면과 API 를 **한 포트에서** 냅니다 (http://localhost:8000).
#
# --reload 를 쓰지 않습니다. 개발용 감시 프로세스가 상시 도는 것은 낭비이고,
# 파일이 바뀌는 순간 재시작해서 요청이 끊깁니다.
#
# **0.0.0.0 에 묶습니다.** 127.0.0.1 이면 이 맥에서만 열려서, 폰이나 다른
# 기기에서 못 붙습니다. 집 안 네트워크에서 보려면 이래야 합니다.
#
# ⚠️ 로그인이 없습니다. 같은 네트워크에 있는 사람은 누구나 열람하고 키워드를
# 고칠 수 있습니다. 집 공유기 안에서만 쓰세요 — 공용 와이파이에서는
# HOST=127.0.0.1 로 두고 쓰는 편이 낫습니다.
HOST="${DUKGOTGAN_HOST:-0.0.0.0}"
source "$(dirname "$0")/lib.sh"

wait_for_docker || exit 1
wait_for_mysql || exit 1
cd "$BACKEND" || exit 1
exec "$BACKEND/.venv/bin/uvicorn" app.api.main:app --host "$HOST" --port 8000
