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


# ── 사람 ─────────────────────────────────────────────────────


class User(Base):
    """곳간을 보는 사람.

    **비밀번호는 네 자리 숫자입니다.** 집 안 공유기 안에서만 쓰는 전제라
    긴 비밀번호는 매번 입력하는 비용만 큽니다. 대신 네 자리는 만 가지뿐이라
    무한정 찍으면 뚫리므로, `auth.py` 가 틀린 횟수를 세어 잠급니다 —
    **짧은 비밀번호를 쓸 수 있게 만드는 것이 그 잠금입니다.**

    `password_hash` 가 NULL 이면 비밀번호 없이 눌러서 들어갑니다. 관리자만은
    NULL 을 허용하지 않습니다 (`auth.set_pin` 이 막습니다) — 선택 화면에
    관리자가 그냥 떠 있는데 비밀번호가 없으면 "관리자만" 이라는 제한이 잠금이
    아니라 표시가 됩니다.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # 선택 화면이 이름으로 사람을 가리므로 비워 둘 수 없습니다.
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # scrypt. NULL 이면 비밀번호 없음.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 한 명뿐입니다. 수집을 직접 돌리는 버튼이 이 값을 봅니다.
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("name", name="uq_users_name"),)


class UserSession(Base):
    """쿠키에 들어가는 값.

    **사용자 ID 를 쿠키에 직접 넣지 않습니다.** 기기 하나를 끊고 싶을 때
    그 세션 행만 지우면 되고, 계정 자체는 그대로 남습니다. 쿠키 값이 새면
    세션 하나를 버리는 것으로 끝나는 것도 같은 이유입니다.
    """

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)

    __table_args__ = (Index("ix_user_sessions_user", "user_id"),)


class UserKeyword(Base):
    """누가 어떤 키워드를 구독하는가.

    **`keywords` 는 사람별로 쪼개지 않습니다.** 거기 걸린
    `UNIQUE(term)` 이 "같은 검색어는 한 번만 수집한다"는 뜻이고, 그것이
    사람이 늘어도 유튜브 호출과 요약 비용이 늘지 않는 이유입니다. 사람별로
    쪼개는 순간 셋이 같은 주제를 보면 비용도 셋이 됩니다.
    """

    __tablename__ = "user_keywords"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    keyword_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    # 구독을 끊은 시각. **행을 지우지 않습니다** — 지우면 그 키워드가 내
    # 삭제 영역에서도 사라져 되살릴 방법이 없어집니다. 예전에 키워드를
    # `archived` 로만 두고 지우지 않았던 것과 같은 이유입니다.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_user_keywords_keyword", "keyword_id"),)


class UserLecture(Base):
    """사람이 강의에 대해 누른 것 — 읽음·즐겨찾기·제외.

    **`lecture_id` 가 아니라 `video_id` 로 묶습니다.** 재요약하면
    `lectures` 에 새 버전 행이 생기는데, 읽음은 영상 단위 개념이라 버전이
    바뀔 때마다 안 읽음으로 되돌아가면 안 됩니다. 예전 코드도 PATCH 에서
    같은 video_id 의 모든 버전을 한꺼번에 고쳐 이 문제를 피하고 있었는데,
    키를 video_id 로 두면 그 손질 자체가 필요 없어집니다.
    """

    __tablename__ = "user_lectures"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # 기본 정렬이 "안 읽은 것 먼저" 라 사용자별 읽음을 자주 훑습니다.
        Index("ix_user_lectures_read", "user_id", "read_at"),
    )


# ── 설정·이력 ────────────────────────────────────────────────


class Keyword(Base):
    """사용자가 등록하는 관심 주제.

    status='pending' 이 수집 스케줄러의 트리거입니다. 신규 등록은 pending 으로
    들어가고, 첫 실행을 마치면 active 로 바뀝니다. 수집기가 아직 없는 동안에도
    pending 으로 남아 있을 뿐 UI 는 정상 동작합니다.
    """

    __tablename__ = "keywords"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # 검색어이거나 채널 핸들(@gaingetv). 사용자가 입력한 그대로 둡니다.
    term: Mapped[str] = mapped_column(String(190), nullable=False)
    # search  — 키워드로 검색 (search.list 100유닛)
    # channel — 채널의 업로드를 그대로 (playlistItems 1유닛, **50배 쌉니다**)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False, default="search")
    # 채널 구독일 때만. 등록 시 핸들을 해석해 채워 둡니다.
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # **누가 만들었는가.** 수집 설정은 키워드에 붙어 있어 고치면 같이 보는
    # 사람 모두에게 적용됩니다. 구독자면 누구나 고칠 수 있게 두었더니 남이
    # 정한 값을 모르고 바꾸는 일이 생겨서, 고칠 수 있는 사람을 만든 사람
    # 하나로 좁혔습니다. 구독은 그대로 누구나 합니다 — 막는 것은 수정뿐입니다.
    #
    # 사용자가 지워지면 NULL 이 되고, 그러면 아무도 못 고칩니다. 임자 없는
    # 설정이 모두에게 열려 있는 것보다 잠겨 있는 편이 낫습니다.
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
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
    # 어느 잡이 만든 기록인가 — discover · transcript · review.
    # 셋을 따로 돌리므로, 이게 없으면 실행 로그에서 무엇이 무엇인지
    # 구분되지 않고 끊긴 기록 정리도 남의 잡을 건드립니다.
    job: Mapped[str] = mapped_column(String(16), nullable=False, default="cycle")
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


class UsageWindow(Base):
    """토큰 사용량 — **5시간 창** 단위.

    일일 장부(`usage_ledger`)와 갈라 둡니다. 유튜브 쿼터는 구글이 하루
    주기로 풀지만 구독 사용량은 5시간 주기라, 한 테이블에 담으면 둘 중
    하나는 틀린 주기로 세게 됩니다.

    창의 시작 시각을 키로 씁니다. 자정에 맞추면 24가 5로 나눠떨어지지
    않아 마지막 칸만 짧아지므로, 고정 기준점에서 5시간씩 끊습니다.

    **회사별로 칸을 나눕니다.** 상한은 각 구독에 따로 걸리는데 한 줄에
    합치면, 한쪽이 많이 쓴 것 때문에 아직 여유가 있는 쪽까지 멈춥니다 —
    토큰이 모자라서 회사를 늘렸는데 정반대가 됩니다. 일일 장부
    (`usage_ledger`)는 "오늘 얼마나 했나"를 보는 곳이라 합친 채로 둡니다.
    """

    __tablename__ = "usage_window"

    start: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    provider: Mapped[str] = mapped_column(
        String(32), primary_key=True, nullable=False, default="claude"
    )
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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
    # **누가 이 영상을 붙들고 있는가.** 요약 워커를 둘 이상 띄우기 위한
    # 값입니다 — `<provider>:<host>:<pid>` 꼴이고, 잡을 때만 채워집니다.
    #
    # 상태만으로는 부족합니다. 상태는 "누군가 작업 중"까지만 말해 주는데,
    # 워커가 둘이면 **내가 잡은 것인지**를 알아야 합니다. 결과를 저장할 때
    # (llm/tools.py) 와 좀비를 회수할 때(llm/runner.py) 이 값을 봅니다.
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 붙든 시각. `updated_at` 과 갈라 둡니다 — updated_at 은 어떤 수정에도
    # 움직여서, "얼마나 오래 붙들고 있나"의 기준이 못 됩니다.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_kst, onupdate=now_kst
    )

    __table_args__ = (
        Index("ix_videos_state_discovered", "state", "discovered_at"),
        # 좀비 회수: 오래 REVIEWING 인 항목 조회
        Index("ix_videos_state_updated", "state", "updated_at"),
        # 좀비 회수는 이제 "누가 언제 붙들었나"로 봅니다
        Index("ix_videos_claimed", "state", "claimed_at"),
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
    # AI 가 본 실제 주제와 검색 키워드와의 관련도. 예전에는 red_flags 텍스트에만
    # 남기고 버렸는데, 채널 차단을 데이터로 굴리려면 값으로 있어야 합니다.
    topic: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    keyword_relevance: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
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


class AppState(Base):
    """프로세스 밖에 살아야 하는 값들.

    자막 냉각이 메모리 전역이었는데, **재시작하면 사라졌습니다.** launchd 가
    워커를 다시 띄울 때마다 냉각이 풀린 것처럼 되어 곧바로 차단된 문을 다시
    두드렸고, 지수 백오프는 한 번도 쌓이지 못했습니다. 화면 쪽은 더 나빴는데,
    API 프로세스의 전역을 읽으니 **거기에는 애초에 값이 없어** 냉각 안내가
    영영 뜨지 않았습니다.
    """

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)


class ChannelBlock(Base):
    """이 채널의 영상은 앞으로 수집하지 않습니다.

    유튜브 검색은 넓은 키워드에서 엉뚱한 채널을 계속 물어옵니다. 검색어를
    다듬어서는 안 됐습니다 — 실측에서 제외 연산자(`-노코드`)를 넣으니 관련도
    랭킹 자체가 무너졌습니다.

    대신 **AI 가 이미 내린 판정을 재사용합니다.** 같은 채널이 반복해서 무관·
    홍보로 걸리면 다음부터 룰 단계에서 거릅니다. AI 호출 한 번의 값이
    "이 영상 탈락"에서 "이 채널 영구 제외"로 커집니다.
    """

    __tablename__ = "channel_blocks"

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 사람에게 보여줄 차단 사유
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # True = 판정 이력으로 자동 차단, False = 사용자가 직접 막음
    auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 차단을 풀면 행을 지우지 않고 이 값을 내립니다. 지워 버리면 다음 탈락
    # 때 처음부터 세기 시작해 같은 채널이 또 자동 차단됩니다 — 사용자가
    # 한 번 "괜찮다"고 한 채널을 계속 되막는 셈입니다.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)


class UserChannelBlock(Base):
    """이 사람에게만 이 채널을 숨깁니다.

    **`channel_blocks` 와 나뉘어 있는 것이 핵심입니다.** 그쪽은 룰 단계에서
    걸려 **수집 자체를 멈추고**, 이쪽은 목록 쿼리에서만 걸려 **보이지 않게**
    합니다.

    나누지 않으면 이런 일이 납니다 — 아내가 어떤 채널을 세 번 빼면
    자동 차단이 걸리고, 그때부터 관리자도 그 채널 영상을 못 받습니다.
    받은 적이 없으니 화면에 안 나오고, 안 나오니 그런 일이 있었다는 것도
    모릅니다. 사람 한 명의 취향이 공용 수집을 조용히 바꾸는 셈입니다.

    모두에게서 막고 싶으면 채널 화면에서 관리자가 직접 `channel_blocks` 에
    올립니다 — 그건 비용을 줄이는 결정이라 관리자가 내려야 합니다.
    """

    __tablename__ = "user_channel_blocks"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_kst)

    __table_args__ = (Index("ix_user_channel_blocks_channel", "channel_id"),)


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
    # 읽은 시각. **불리언 대신 시각으로 둡니다** — 비용은 같은데 "언제 읽었나"를
    # 나중에 쓸 수 있고, NULL 하나로 "안 읽음"이 자연스럽게 표현됩니다.
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 사용자가 직접 뺀 것. **`is_hidden` 과 다릅니다** — 그건 재요약으로
    # 밀려난 옛 버전이라, 같이 쓰면 제외함에 옛 버전이 섞입니다.
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    User,
    UserSession,
    UserKeyword,
    UserLecture,
    UserChannelBlock,
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
    "User",
    "UserSession",
    "UserKeyword",
    "UserLecture",
    "UserChannelBlock",
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
