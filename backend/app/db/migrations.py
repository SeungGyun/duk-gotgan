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
        if not _column_exists(conn, "crawl_runs", "job"):
            conn.execute(
                text("ALTER TABLE crawl_runs ADD COLUMN job VARCHAR(16) NOT NULL DEFAULT 'cycle'")
            )
            logger.info("[db] crawl_runs.job added")

        if not _table_exists(conn, "usage_window"):
            conn.execute(
                text(
                    "CREATE TABLE usage_window ("
                    " start DATETIME NOT NULL PRIMARY KEY,"
                    " input_tokens BIGINT NOT NULL DEFAULT 0,"
                    " output_tokens BIGINT NOT NULL DEFAULT 0,"
                    " llm_calls INT NOT NULL DEFAULT 0)"
                )
            )
            logger.info("[db] usage_window created")

        # 프로세스 밖에 남아야 하는 값(자막 냉각 등).
        if not _table_exists(conn, "app_state"):
            conn.execute(
                text(
                    "CREATE TABLE app_state ("
                    " `key` VARCHAR(64) NOT NULL PRIMARY KEY,"
                    " value TEXT NOT NULL,"
                    " updated_at DATETIME NOT NULL)"
                )
            )
            logger.info("[db] app_state created")

        # 사용자가 직접 뺀 것. is_hidden(재요약으로 밀려난 옛 버전)과 다릅니다.
        if not _column_exists(conn, "lectures", "excluded_at"):
            conn.execute(text("ALTER TABLE lectures ADD COLUMN excluded_at DATETIME NULL"))
            logger.info("[db] lectures.excluded_at added")
        if not _index_exists(conn, "lectures", "ix_lectures_excluded"):
            conn.execute(
                text("CREATE INDEX ix_lectures_excluded ON lectures (excluded_at)")
            )
            logger.info("[db] ix_lectures_excluded created")

        # 기본 정렬(안 읽은 것 먼저 · 유튜브 최신순)이 매번 전체를 훑지 않게.
        if not _index_exists(conn, "lectures", "ix_lectures_read"):
            conn.execute(
                text("CREATE INDEX ix_lectures_read ON lectures (is_hidden, read_at)")
            )
            logger.info("[db] ix_lectures_read created")

        _seed_owner(conn)


def _seed_owner(conn) -> None:
    """주인 한 명을 만들고, 지금까지 쌓인 것을 그 사람 이름으로 옮깁니다.

    **여러 번 돌려도 같은 결과여야 합니다.** 매 기동마다 실행되므로,
    이미 사용자가 있으면 통째로 건너뜁니다 — 안 그러면 서버를 재시작할
    때마다 주인이 늘거나, 사용자가 지운 구독이 되살아납니다.

    옛 컬럼(`lectures.read_at` 등)은 **지우지 않습니다.** 옮긴 값이 틀렸을
    때 되돌릴 곳이 있어야 합니다. 코드는 이미 새 표만 읽으므로 남아 있어도
    화면에 영향이 없고, 몇 주 지켜본 뒤 따로 걷습니다.
    """
    from app.db.models import new_id
    from app.security import hash_pin
    from config.time import now_kst

    if conn.execute(text("SELECT COUNT(*) FROM users")).scalar():
        return

    owner_id = new_id()
    conn.execute(
        text(
            "INSERT INTO users (id, name, password_hash, is_owner, created_at)"
            " VALUES (:id, :name, :pw, 1, :now)"
        ),
        # 첫 비밀번호는 0000 입니다. 화면이 이걸 알아보고 바꾸라고 띄웁니다
        # (routes/users.py 의 `pinIsDefault`).
        # 시각은 파이썬에서 넣습니다 — MySQL 의 NOW() 는 컨테이너 표준시라
        # 다른 시각들과 몇 시간씩 어긋납니다.
        {"id": owner_id, "name": "주인", "pw": hash_pin("0000"), "now": now_kst()},
    )

    # 보관된 키워드까지 전부 옮깁니다 — 되살렸을 때 남의 것이 되어 있으면
    # 복구가 복구가 아닙니다. **보관 시각도 같이 옮겨야** 지웠던 6개가
    # 활성 목록이 아니라 삭제 영역에 그대로 남습니다.
    kw = conn.execute(
        text(
            "INSERT INTO user_keywords (user_id, keyword_id, created_at, archived_at)"
            " SELECT :u, id, created_at,"
            "        CASE WHEN status = 'archived'"
            "             THEN COALESCE(archived_at, created_at) END"
            " FROM keywords"
        ),
        {"u": owner_id},
    ).rowcount

    # 읽음·즐겨찾기·제외. 재요약본이 여러 개인 영상은 하나로 접습니다 —
    # 새 표의 키가 video_id 라 버전이 몇이든 한 줄입니다.
    lec = conn.execute(
        text(
            "INSERT INTO user_lectures (user_id, video_id, read_at, is_favorite, excluded_at)"
            " SELECT :u, video_id, MAX(read_at), MAX(is_favorite), MAX(excluded_at)"
            " FROM lectures"
            " WHERE read_at IS NOT NULL OR is_favorite = 1 OR excluded_at IS NOT NULL"
            " GROUP BY video_id"
        ),
        {"u": owner_id},
    ).rowcount

    # 지금까지 쌓인 자동 차단은 주인이 뺀 결과입니다. 개인 차단으로 옮기되
    # **전역 차단도 그대로 둡니다** — 이미 수집을 멈춰 둔 채널이라, 여기서
    # 풀면 다음 수집에 도로 들어와 비용만 다시 듭니다.
    ch = conn.execute(
        text(
            "INSERT INTO user_channel_blocks"
            " (user_id, channel_id, channel_title, reason, auto, created_at)"
            " SELECT :u, channel_id, channel_title, reason, auto, created_at"
            " FROM channel_blocks WHERE active = 1"
        ),
        {"u": owner_id},
    ).rowcount

    logger.info(
        "[db] 주인 계정 생성 — 키워드 %d · 읽음/제외 %d · 차단 채널 %d 이관 (첫 비밀번호 0000)",
        kw, lec, ch,
    )
