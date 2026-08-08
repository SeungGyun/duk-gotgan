"""덕!곳간 백엔드 진입점.

  uvicorn app.api.main:app --reload --port 8000

프론트(vite dev)는 `/api` 를 이 서버로 프록시합니다. 계약은 docs/API.md.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import errors
from app.api.routes import channels, keywords, lectures, queue, stats, users
from app.db.session import init_db
from config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="덕!곳간 API", version="0.1.0", lifespan=lifespan)

# vite 프록시를 쓰면 동일 출처라 CORS 가 필요 없지만, 브라우저가 8000 을 직접
# 부르는 구성(다른 기기에서 접속 등)도 되게 열어 둡니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

errors.install(app)

app.include_router(users.router, prefix="/api/v1")
app.include_router(keywords.router, prefix="/api/v1")
app.include_router(lectures.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(channels.router, prefix="/api/v1")
app.include_router(queue.router, prefix="/api/v1")


@app.get("/api/v1/health")
def health():
    return {"ok": True}


# ── 화면 ─────────────────────────────────────────────────────
# 빌드된 프론트를 **같은 포트에서** 냅니다. 상시 서비스로 돌릴 때 프로세스가
# 둘이면 재시작마다 한쪽이 빠질 여지가 생기고, 주소도 둘이 됩니다.
#
# 개발은 그대로 `npm run dev`(5173, /api 를 여기로 프록시)를 쓰면 됩니다 —
# 이 마운트는 dist 가 있을 때만 붙어서 개발 흐름을 건드리지 않습니다.

DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

# **캐시 규칙을 적어 보냅니다.**
#
# 안 적으면 브라우저가 알아서 정합니다(RFC 9111 §4.2.2 어림 신선도) — 대개
# `(지금 − Last-Modified) × 10%` 이고, 폰의 사파리가 특히 오래 잡습니다.
# 그래서 `rebuild-ui.sh` 로 화면을 고쳐도 폰에서는 **옛 화면이 그대로**
# 남았습니다. 고친 것이 되돌아간 것처럼 보이는데, 실제로는 폰이 옛
# index.html 을 들고 그것이 가리키는 옛 asset 을 계속 쓰는 것입니다.
#
#   index.html   매번 물어봅니다. ETag 가 있어 안 바뀌었으면 304 라 쌉니다.
#   /assets/*    이름에 내용 해시가 박혀 있습니다(index-BbuZIiXP.js).
#                내용이 바뀌면 이름이 바뀌므로 영원히 캐시해도 안전합니다.
NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"


class HashedAssets(StaticFiles):
    """내용 해시가 이름에 박힌 파일들. 영원히 캐시해도 됩니다."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["cache-control"] = IMMUTABLE
        return resp


if DIST.is_dir():
    app.mount("/assets", HashedAssets(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """SPA 폴백. `/lectures/abc` 같은 깊은 주소도 index.html 로 받습니다.

        **`/api` 는 넘기지 않습니다.** 없는 API 를 부르면 HTML 이 돌아와,
        프론트가 JSON 파싱에서 엉뚱하게 죽습니다 — 404 로 끝내야 원인이 보입니다.
        """
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target = DIST / path
        if path and target.is_file():
            # 해시가 안 붙은 것들(favicon, apple-touch-icon)입니다. 이름이
            # 고정이라 immutable 로 두면 아이콘을 바꿔도 영영 안 바뀝니다.
            return FileResponse(target, headers={"cache-control": NO_CACHE})
        return FileResponse(DIST / "index.html", headers={"cache-control": NO_CACHE})
else:
    logging.warning("[api] frontend/dist 가 없습니다 — 화면은 vite dev(5173)로만 열립니다.")
