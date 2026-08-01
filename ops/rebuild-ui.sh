#!/bin/zsh
# 화면을 고친 뒤 상시 서비스에 반영합니다.
#
# 상시 서비스는 **빌드된 파일**(frontend/dist)을 냅니다. 개발 중에는
# `npm run dev`(5173)가 소스를 직접 보므로 이 스크립트가 필요 없지만,
# 8000 번에서 보이는 화면은 빌드해야 바뀝니다.
set -eu
cd "${0:A:h}/../frontend"
npm run build
print "\n반영됐습니다 — http://localhost:8000 (새로고침)"
print "API 재시작은 필요 없습니다. 정적 파일만 바뀝니다."
