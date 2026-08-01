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


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
            """
        ),
        {"t": table},
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

        # 개요·핵심포인트·챕터를 하나로 합친 섹션 구조
        if not _column_exists(conn, "lectures", "sections"):
            conn.execute(text("ALTER TABLE lectures ADD COLUMN sections JSON NULL"))
            conn.execute(text("UPDATE lectures SET sections = JSON_ARRAY()"))
            logger.info("[db] lectures.sections added")
        if not _column_exists(conn, "lectures", "closing"):
            conn.execute(text("ALTER TABLE lectures ADD COLUMN closing TEXT NULL"))
            logger.info("[db] lectures.closing added")

        # 관련도·주제를 값으로 남깁니다 (채널 차단의 근거)
        if not _column_exists(conn, "evaluations", "keyword_relevance"):
            conn.execute(
                text("ALTER TABLE evaluations ADD COLUMN topic VARCHAR(300) NOT NULL DEFAULT ''")
            )
            conn.execute(
                text(
                    "ALTER TABLE evaluations "
                    "ADD COLUMN keyword_relevance INT NOT NULL DEFAULT 100"
                )
            )
            logger.info("[db] evaluations.topic · keyword_relevance added")

        # 차단 해제를 값으로 남깁니다 (행을 지우면 다시 자동 차단됨)
        if _table_exists(conn, "channel_blocks") and not _column_exists(
            conn, "channel_blocks", "active"
        ):
            conn.execute(
                text("ALTER TABLE channel_blocks ADD COLUMN active TINYINT(1) NOT NULL DEFAULT 1")
            )
            logger.info("[db] channel_blocks.active added")

        # 채널 구독 — 검색(101유닛) 대신 업로드 목록(2유닛)을 봅니다
        if not _column_exists(conn, "keywords", "source_type"):
            for ddl in (
                "ALTER TABLE keywords ADD COLUMN source_type VARCHAR(10) NOT NULL DEFAULT 'search'",
                "ALTER TABLE keywords ADD COLUMN channel_id VARCHAR(64) NULL",
                "ALTER TABLE keywords ADD COLUMN channel_title VARCHAR(200) NULL",
                "ALTER TABLE keywords ADD COLUMN uploads_playlist_id VARCHAR(64) NULL",
            ):
                conn.execute(text(ddl))
            logger.info("[db] keywords 채널 구독 컬럼 추가")

        # 실행 시각을 키워드가 갖습니다 (크론 → 틱 방식 전환)
        if not _column_exists(conn, "keywords", "run_hour"):
            conn.execute(
                text("ALTER TABLE keywords ADD COLUMN run_hour INT NOT NULL DEFAULT 4")
            )
            logger.info("[db] keywords.run_hour added")

        # 삭제 영역에서 "언제 지웠는지"를 보여주기 위한 컬럼
        if not _column_exists(conn, "keywords", "archived_at"):
            conn.execute(text("ALTER TABLE keywords ADD COLUMN archived_at DATETIME NULL"))
            logger.info("[db] keywords.archived_at added")

        # 읽음 표시. NULL 이면 안 읽은 것 — 기본 정렬이 이걸로 앞뒤를 가릅니다.
        if not _column_exists(conn, "lectures", "read_at"):
            conn.execute(text("ALTER TABLE lectures ADD COLUMN read_at DATETIME NULL"))
            logger.info("[db] lectures.read_at added")
        # 기본 정렬(안 읽은 것 먼저 · 유튜브 최신순)이 매번 전체를 훑지 않게.
        if not _index_exists(conn, "lectures", "ix_lectures_read"):
            conn.execute(
                text("CREATE INDEX ix_lectures_read ON lectures (is_hidden, read_at)")
            )
            logger.info("[db] ix_lectures_read created")
