from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 데이터베이스 — 3307 은 이 프로젝트 전용 MySQL (docker-compose.yml)
    database_url: str = (
        "mysql+pymysql://dukgotgan:dukgotgan1234@localhost:3307/dukgotgan?charset=utf8mb4"
    )

    # 서버
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 상한 — UI 미터의 분모. 0 이면 상한 없음(UI 가 미터를 숨김)
    daily_token_limit: int = 1_000_000
    youtube_unit_limit: int = 10_000

    # 유튜브 Data API v3 키 (Google Cloud Console 에서 발급, 무료)
    youtube_api_key: str = ""

    # ── 룰 필터 기본값 (2026-08-01 확정, ROADMAP §3.1) ──────
    # 비용을 좌우하는 가장 큰 손잡이입니다. AI 호출 수를 직접 줄입니다.
    # 키워드별 값이 있으면 그쪽이 우선하고, 여기 값은 나머지를 채웁니다.
    rule_min_view_count: int = 1_000
    rule_max_age_days: int = 730  # 2년
    # 제목에 이게 들어가면 강의가 아니라 홍보로 봅니다
    rule_title_blocklist: str = (
        "무료특강,수강신청,할인,이벤트,쿠폰,모집,설명회,체험단,광고,협찬,"
        "라이브방송,다시보기,shorts,쇼츠"
    )

    @property
    def title_blocklist(self) -> list[str]:
        return [w.strip().lower() for w in self.rule_title_blocklist.split(",") if w.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
