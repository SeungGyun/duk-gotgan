"""덕!곳간 백엔드 진입점.

  uvicorn app.api.main:app --reload --port 8000

프론트(vite dev)는 `/api` 를 이 서버로 프록시합니다. 계약은 docs/API.md.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import errors
from app.api.routes import channels, keywords, lectures, stats
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

app.include_router(keywords.router, prefix="/api/v1")
app.include_router(lectures.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(channels.router, prefix="/api/v1")


@app.get("/api/v1/health")
def health():
    return {"ok": True}
