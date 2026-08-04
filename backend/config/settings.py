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
    # **5시간 창 단위**로 셉니다. 구독 사용량이 그 주기로 풀려서, 하루로
    # 재면 오전에 다 쓰고 저녁 내내 놀거나 그 반대가 됩니다.
    # 유튜브 쿼터는 여전히 하루 단위입니다(구글이 그렇게 리셋합니다) —
    # 그래서 장부를 갈라 둡니다.
    # 화면에서 바꿀 수 있습니다 — 여기 값은 **아직 정하지 않았을 때의
    # 기본값**입니다. 실제 값은 app_state 에 있고 usage.limit() 이 읽습니다.
    token_limit_per_window: int = 3_000_000
    token_window_hours: int = 5
    youtube_unit_limit: int = 10_000

    # 유튜브 Data API v3 키 (Google Cloud Console 에서 발급, 무료)
    youtube_api_key: str = ""

    # ── 사람 ────────────────────────────────────────────────
    # 1인당 활성 키워드 상한. **주인은 예외입니다** — 상한을 넣었다고 이미
    # 쓰고 있는 것을 지우라고 할 수는 없습니다 (routes/keywords.py).
    #
    # 왜 이 숫자인가: 요약 한 편에 8만 토큰, 5시간 300만이면 하루 178편이
    # 한계입니다. 지금 키워드 13개가 하루 36편을 만드니 키워드 60개쯤에서
    # 상한이 걸립니다 — 1인 10개면 대여섯 명입니다. 모자라면 창 상한을
    # 화면에서 올리면 됩니다.
    max_keywords_per_user: int = 10

    # ── 룰 필터 기본값 (2026-08-01 확정, ROADMAP §3.1) ──────
    # 키워드별 값이 있으면 그쪽이 우선하고, 여기 값은 나머지를 채웁니다.
    #
    # **조회수는 끕니다(0).** 1,000 → 300 으로 낮춰도 여전히 문제였습니다.
    # 330건 중 157건이 여기서 떨어졌는데 하필 틈새 전문 콘텐츠였습니다
    # (인프라 해부학 EP.03 287회 24분 같은 것). 틈새 전문 강의는 정의상
    # 조회수가 적어서, 이걸 품질 신호로 쓰면 목적과 반대로 걸립니다.
    # 켜고 싶으면 .env 의 RULE_MIN_VIEW_COUNT 로 올리면 됩니다.
    rule_min_view_count: int = 0
    # 6개월. 2년으로 두었더니 오래된 영상이 줄의 앞을 차지했습니다 —
    # 기술이든 시사든 반년 지난 이야기는 지금 볼 이유가 적습니다.
    rule_max_age_days: int = 180
    # 제목에 이게 들어가면 강의가 아니라 홍보로 봅니다.
    # `쇼츠`는 뺐습니다 — 길이로 거르는 편이 정확하고, 제목에 "쇼츠"가
    # 들어간 긴 영상까지 같이 떨어뜨릴 이유가 없습니다.
    rule_title_blocklist: str = (
        "무료특강,수강신청,할인,이벤트,쿠폰,모집,설명회,체험단,광고,협찬,"
        "라이브방송,다시보기"
    )

    # ── AI 검토 (M4) ────────────────────────────────────────
    # 인증은 구독입니다 — ANTHROPIC_API_KEY 를 쓰지 않습니다 (ROADMAP §3-9).
    review_model: str = "claude-opus-5"
    # **누가 요약하는가.** 요약 워커를 여러 개 띄워 서로 다른 회사 모델에
    # 나눠 맡길 수 있게 하는 값입니다. 프로세스마다 다르게 주고 띄웁니다.
    #
    #   REVIEW_PROVIDER=claude       python -m scripts.worker --only review
    #   REVIEW_PROVIDER=antigravity  python -m scripts.worker --only review
    #
    # 세 군데를 가릅니다 — 워커 락(같으면 둘이 번갈아 하나씩만 돕니다),
    # 토큰 장부(합치면 한쪽이 상한을 채워 멀쩡한 쪽까지 멈춥니다),
    # 그리고 좀비 회수 범위(runner.recover_zombies).
    review_provider: str = "claude"

    # ── 안티그래비티 (두 번째 회사) ──────────────────────────
    # 구독 사용량이 모자라 요약 줄이 286건까지 밀렸습니다. 다른 회사를
    # 붙여 같은 줄에서 나눠 가져갑니다 (AI-PIPELINE §8.2.1).
    #
    # 클로드와 달리 SDK 가 아니라 **CLI 한 번 실행**입니다. 결과는 도구가
    # 아니라 `--json-schema` 구조화 출력으로 받습니다.
    agy_bin: str = "agy"
    # effort 가 모델 이름에 붙어 있습니다 (`agy models` 참고). 그래서
    # --effort 를 같이 주면 충돌합니다.
    agy_model: str = "gemini-3.1-pro-high"
    # CLI 기본값이 5분인데, 60분 강의 자막을 다 읽고 요약을 쓰기에는
    # 빠듯합니다. 클로드 쪽 실측이 편당 2~5분이라 넉넉히 잡습니다.
    agy_timeout_sec: int = 900
    # **자막은 제3자가 통제하는 텍스트입니다.** 클로드 경로는 도구를
    # Read 하나로 좁히고 경로 가드를 걸지만(llm/guard.py), agy 에는 그런
    # 손잡이가 없습니다 — 실측에서 작업 폴더 밖의 `.env` 를 그대로 읽어
    # 냈습니다. macOS 샌드박스로 홈 디렉터리를 통째로 막습니다.
    agy_sandbox: bool = True

    @property
    def active_review_model(self) -> str:
        """지금 회사가 실제로 쓰는 모델. `evaluations.model` 에 적힙니다."""
        return self.agy_model if self.review_provider == "antigravity" else self.review_model
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
    # 대기 시간 어림에 씁니다. M4 실측이 처음엔 12~18배속이었는데, 검토가
    # 같이 돌면서 4~5배속으로 떨어졌습니다. 보수적으로 잡아야 "생각보다
    # 오래 걸린다"는 실망이 없습니다.
    asr_realtime_factor: float = 5.0
    # 안전장치. 시간뿐 아니라 **메모리** 때문에도 낮춰야 했습니다 —
    # 위스퍼는 오디오를 통째로 16kHz float32 로 올려서 두 시간이면 그것만
    # 460MB 이고, 그 순간 요약 프로세스가 못 떠서 죽었습니다(16GB 기계).
    asr_max_duration_sec: int = 90 * 60

    @property
    def title_blocklist(self) -> list[str]:
        return [w.strip().lower() for w in self.rule_title_blocklist.split(",") if w.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
