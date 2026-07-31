"""매 기동마다 안전하게 다시 돌릴 수 있는 스키마 보정.

`Base.metadata.create_all()` 은 **없는 테이블**만 만듭니다. 이미 있는 테이블에
컬럼을 더하거나 인덱스를 거는 일은 안 합니다. Alembic 을 붙일 만큼 스키마가
자주 바뀌는 단계가 아니라, information_schema 를 확인하고 필요한 것만 실행하는
방식으로 둡니다 (GuruguruCoin 과 같은 방식).
"""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.models import LECTURE_FULLTEXT_INDEX

logger = logging.getLogger(__name__)


def _index_exists(conn, table: str, index_name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i
            """
        ),
        {"t": table, "i": index_name},
    ).scalar()
    return (row or 0) > 0


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()
    return (row or 0) > 0


def ensure_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        # 한국어 전문 검색용 ngram FULLTEXT.
        # SQLAlchemy 의 Index() 로는 WITH PARSER 를 표현할 수 없어 여기서 겁니다.
        if not _index_exists(conn, "lectures", "ft_lectures_search"):
            conn.execute(text(LECTURE_FULLTEXT_INDEX))
            logger.info("[db] created ft_lectures_search (ngram)")

        # 개요를 흐름의 마디로 나눠 받기 시작 (요약 구조 개선)
        if not _column_exists(conn, "lectures", "abstract_beats"):
            conn.execute(text("ALTER TABLE lectures ADD COLUMN abstract_beats JSON NULL"))
            conn.execute(text("UPDATE lectures SET abstract_beats = JSON_ARRAY()"))
            logger.info("[db] lectures.abstract_beats added")

        # 삭제 영역에서 "언제 지웠는지"를 보여주기 위한 컬럼
        if not _column_exists(conn, "keywords", "archived_at"):
            conn.execute(text("ALTER TABLE keywords ADD COLUMN archived_at DATETIME NULL"))
            logger.info("[db] keywords.archived_at added")
