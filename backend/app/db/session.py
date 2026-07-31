"""엔진·세션과 스키마 초기화."""

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base
from config.settings import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    echo=False,
    # 개발 중 서버를 오래 띄워두면 MySQL 이 유휴 커넥션을 끊습니다(wait_timeout 8시간).
    # 끊긴 커넥션을 그대로 쓰면 첫 요청이 "MySQL server has gone away" 로 죽습니다.
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """없는 테이블을 만들고, create_all 이 못 하는 것들을 채웁니다."""
    Base.metadata.create_all(bind=engine)
    from app.db.migrations import ensure_schema

    ensure_schema(engine)
    logger.info("[db] schema ready")


def get_db():
    """FastAPI 의존성."""
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
