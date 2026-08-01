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
    daily_token_limit: int = 10_000_000
    youtube_unit_limit: int = 10_000

    # 유튜브 Data API v3 키 (Google Cloud Console 에서 발급, 무료)
    youtube_api_key: str = ""

    # ── 룰 필터 기본값 (2026-08-01 확정, ROADMAP §3.1) ──────
    # 비용을 좌우하는 가장 큰 손잡이입니다. AI 호출 수를 직접 줄입니다.
    # 키워드별 값이 있으면 그쪽이 우선하고, 여기 값은 나머지를 채웁니다.
    # 300 은 실측값입니다. 1,000 으로 두니 한국 기술 컨퍼런스 세션 발표가
    # 통째로 걸렸습니다 — 조회수 300~400 대인데 전문성은 오히려 높은 쪽입니다.
    rule_min_view_count: int = 300
    rule_max_age_days: int = 730  # 2년
    # 제목에 이게 들어가면 강의가 아니라 홍보로 봅니다
    rule_title_blocklist: str = (
        "무료특강,수강신청,할인,이벤트,쿠폰,모집,설명회,체험단,광고,협찬,"
        "라이브방송,다시보기,shorts,쇼츠"
    )

    # ── AI 검토 (M4) ────────────────────────────────────────
    # 인증은 구독입니다 — ANTHROPIC_API_KEY 를 쓰지 않습니다 (ROADMAP §3-9).
    review_model: str = "claude-opus-5"
    # 판정 위주 작업이라 최고 강도가 필요 없습니다 (ROADMAP §3-5)
    review_effort: str = "medium"
    review_max_turns: int = 15
    # 영상 1건당 격리 작업 폴더가 만들어지는 곳. 실행 후 삭제합니다.
    jobs_dir: str = "/tmp/dukgotgan-jobs"
    # 헤드리스 고유 오버헤드 실측값 (AI-PIPELINE §5.1). 조기 종료 판정에
    # 씁니다 — 총 입력에서 이만큼을 빼야 "실제로 읽은 자막"이 나옵니다.
    overhead_tokens: int = 18_700

    # ── 오디오 받아쓰기 (자막 경로가 막혔을 때의 폴백) ──────────
    asr_enabled: bool = True
    # 애플 실리콘 GPU 를 그대로 씁니다. M4 실측 12.5배속.
    asr_model: str = "mlx-community/whisper-large-v3-turbo"
    # 사이클당 받아쓰기에 쓸 시간. 이게 없으면 대기 91건이 한 사이클에
    # 다섯 시간을 잡아먹고 그동안 "지금 실행"에 반응하지 못합니다.
    asr_budget_sec: int = 20 * 60
    # 안전장치. 세 시간짜리 하나가 하룻밤을 통째로 먹는 일을 막습니다.
    asr_max_duration_sec: int = 3 * 3600

    @property
    def title_blocklist(self) -> list[str]:
        return [w.strip().lower() for w in self.rule_title_blocklist.split(",") if w.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
