"""테이블 정의 — docs/SPEC.md §3 데이터 모델의 MySQL 8 구현.

SPEC 은 PostgreSQL 을 전제로 쓰였습니다. 실제 구현은 MySQL 8 을 쓰므로 세 가지를
바꿉니다. 바꾼 이유를 남겨둡니다:

- `jsonb` → `JSON`.  MySQL 의 JSON 은 파싱된 바이너리로 저장되어 jsonb 와 성격이
  같습니다. 다만 JSON 컬럼에는 인덱스를 직접 못 걸어서, 정렬·필터에 쓰는 값
  (`expert_score`, `verdict`)은 JSON 밖에 일반 컬럼으로 둡니다.
- `tsvector` + GIN → `FULLTEXT ... WITH PARSER ngram`.  한국어는 PG 기본 파서로
  형태소가 안 잘려 SPEC 에서도 미결이었는데, MySQL 의 ngram 파서는 2글자 단위로
  쪼개므로 별도 확장 없이 한국어 검색이 됩니다.
- `uuid` → `CHAR(36)`.  MySQL 8 에 uuid 타입이 없습니다. 애플리케이션에서 생성합니다.

두 층 구조는 그대로입니다. 임시 층(videos·transcripts·evaluations·pipeline_events)에
탈락분이 아무리 쌓여도, UI 는 정식 층(lectures)만 조회합니다.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config.time import now_kst


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ── 설정·이력 ────────────────────────────────────────────────


class Keyword(Base):
    """사용자가 등록하는 관심 주제.

    status='pending' 이 수집 스케줄러의 트리거입니다. 신규 등록은 pending 으로
    들어가고, 첫 실행을 마치면 active 로 바뀝니다. 수집기가 아직 없는 동안에도
    pending 으로 남아 있을 뿐 UI 는 정상 동작합니다.
    """

    __tablename__ = "keywords"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    term: Mapped[str] = mapped_column(String(190), nullable=False)
    # pending | active | quota_wait | paused | archived
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # ko | en | any
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="ko")
    # daily | twice_weekly | weekly — cron 문자열은 수집기가 이 값에서 만듭니다
    schedule: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    # 20분. 유튜브 검색이 20분 경계로만 거를 수 있어, 그보다 낮게 잡아도
    # 검색 단계에서 어차피 20분 이상만 들어옵니다 (youtube.duration_bucket).
    min_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=1200)
    max_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=14400)
    min_expert_score: Mapped[int] = mapped_column(Integer, nullable=False, default=75)
    max_per_run: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    published_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 실행 시각(0~23). 주기(schedule)와 시각을 나눠 둡니다 — 크론 하나로는
    # "이 키워드만 정오에" 같은 요구를 받을 수 없고, 키워드마다 크론을
    # 붙였다 떼는 것도 등록·삭제 때마다 사고가 납니다.
    run_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    # 삭제(보관)한 시각. 삭제 영역에서 "언제 지웠는지"를 보여주고, 복구하면 다시 비웁니다.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # 같은 검색어를 두 번 등록하지 못하게. archived 된 것까지 포함해 막습니다 —
        # 되살리는 편이 새로 만드는 것보다 이력이 이어져서 낫습니다.
        UniqueConstraint("term", name="uq_keywords_term"),
        Index("ix_keywords_status", "status"),
    )


class CrawlRun(Base):
    """수집 실행 1회의 이력."""

    __tablename__ = "crawl_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    keyword_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # initial | scheduled | manual
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    # running | succeeded | partial | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # {discovered, rulePassed, transcribed, reviewed, published}
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    youtube_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 실패 시 사용자에게 그대로 보여줄 문장 (코드가 아니라 "다음에 무슨 일이 일어나는지")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_crawl_runs_started", "started_at"),)


class UsageLedger(Base):
    """일일 자원 사용량. 비용 폭주를 막는 유일한 장치입니다.

    외부 유료 호출 직전에 오늘 행을 잠그고 상한을 확인합니다.
    """

    __tablename__ = "usage_ledger"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    youtube_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stt_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 전문성 미달 조기 종료로 아낀 입력 토큰 (UI 가 "조기 종료 작동 중"을 판단하는 근거)
    early_exit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    early_exit_saved_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )


# ── 임시 층 (파이프라인 작업용) ──────────────────────────────


class Video(Base):
    """영상 원천. 키워드와 N:M 입니다.

    PK 가 YouTube video ID 라서, 두 키워드가 같은 영상을 찾아와도 자막 수집과
    요약은 한 번만 일어납니다.
    """

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    default_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    has_official_caption: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # DISCOVERED | RULE_PASSED | TRANSCRIBED | REVIEWING | PUBLISHED | REJECTED | FAILED
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="DISCOVERED")
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_kst, onupdate=now_kst
    )

    __table_args__ = (
        Index("ix_videos_state_discovered", "state", "discovered_at"),
        # 좀비 회수: 오래 REVIEWING 인 항목 조회
        Index("ix_videos_state_updated", "state", "updated_at"),
    )


class VideoKeyword(Base):
    """어떤 키워드가 어떤 영상을 데려왔는지."""

    __tablename__ = "video_keywords"

    video_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    keyword_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    search_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)

    __table_args__ = (Index("ix_video_keywords_keyword", "keyword_id", "discovered_at"),)


class Transcript(Base):
    """자막. 요약 생성 후 30일만 보관합니다 (docs/SPEC.md §8.3)."""

    __tablename__ = "transcripts"

    video_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    # youtube_manual | youtube_auto | whisper
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="ko")
    content: Mapped[str | None] = mapped_column(Text(length=16_777_215), nullable=True)  # MEDIUMTEXT
    # [{start, dur, text}] — 타임스탬프 링크용
    segments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    est_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)


class Evaluation(Base):
    """전문성 판정 결과. 재판정하면 행이 늘어납니다 (이력 보존)."""

    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    # expert | practical | introductory | promotional | irrelevant
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    expert_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # low | medium | high
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    # [{criterion, score, evidence}] — 6항목 고정
    criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    red_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    speaker_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)

    __table_args__ = (Index("ix_evaluations_video_created", "video_id", "created_at"),)


class PipelineEvent(Base):
    """상태 전이 이력. 감사·디버깅용."""

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(20), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    from_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    # discover | transcript | review | publish
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)

    __table_args__ = (Index("ix_pipeline_events_video_created", "video_id", "created_at"),)


# ── 정식 층 (사용자가 보는 유일한 테이블) ────────────────────


class Lecture(Base):
    """AI 검토를 통과한 것만. UI 의 모든 읽기는 여기서 끝납니다.

    `WHERE state = 'PUBLISHED'` 같은 조건을 UI 쿼리에 걸 필요가 없습니다 —
    조건을 빠뜨려 탈락분이 노출되는 실수를 구조로 막습니다.
    """

    __tablename__ = "lectures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # 정렬·필터에 쓰는 값은 JSON 밖에 둡니다 (MySQL 은 JSON 에 인덱스를 못 겁니다)
    expert_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False, default="expert")
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    one_liner: Mapped[str] = mapped_column(Text, nullable=False)
    # 개요. 지금은 흐름의 마디(abstract_beats)로 받고, 문단은 검색·목록용으로
    # 마디를 이어 붙여 채웁니다. 시드로 넣은 옛 행은 문단만 갖고 있습니다.
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # [{label, text}] — 없으면 UI 가 abstract 문단으로 떨어집니다
    abstract_beats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # [{title, startSec, bullets[]}] — 요약의 본체.
    # 예전의 abstract·key_points·chapters 셋이 여기로 합쳐졌습니다.
    # 비어 있으면 옛 형식(시드 데이터)이라 UI 가 예전 배치로 떨어집니다.
    sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    closing: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_audience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prerequisites: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    key_points: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    chapters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    takeaways: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quotes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    user_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    coverage_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 전문 검색 대상. 제목·채널·요약·태그를 한 칸에 이어 붙여 둡니다 —
    # 여러 컬럼에 FULLTEXT 를 나눠 걸면 질의마다 MATCH 절이 갈라져 순위가 섞입니다.
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)

    video = relationship("Video", lazy="joined")

    __table_args__ = (
        UniqueConstraint("video_id", "version", name="uq_lectures_video_version"),
        Index("ix_lectures_published", "is_hidden", "published_at"),
        Index("ix_lectures_score", "is_hidden", "expert_score"),
        Index("ix_lectures_duration", "is_hidden", "duration_sec"),
    )


# ngram FULLTEXT 는 SQLAlchemy 의 Index() 로 표현할 수 없어 마이그레이션에서 겁니다.
LECTURE_FULLTEXT_INDEX = (
    "CREATE FULLTEXT INDEX ft_lectures_search ON lectures (search_text) WITH PARSER ngram"
)

# 편의: 스키마 존재 확인용
ALL_TABLES = [
    Keyword,
    CrawlRun,
    UsageLedger,
    Video,
    VideoKeyword,
    Transcript,
    Evaluation,
    PipelineEvent,
    Lecture,
]

__all__ = [
    "Base",
    "Keyword",
    "CrawlRun",
    "UsageLedger",
    "Video",
    "VideoKeyword",
    "Transcript",
    "Evaluation",
    "PipelineEvent",
    "Lecture",
    "LECTURE_FULLTEXT_INDEX",
    "ALL_TABLES",
    "new_id",
    "text",
]
