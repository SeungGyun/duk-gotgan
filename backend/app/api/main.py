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

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

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
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
else:
    logging.warning("[api] frontend/dist 가 없습니다 — 화면은 vite dev(5173)로만 열립니다.")
