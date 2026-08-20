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

    # ── 유튜브 로그인 쿠키 ──────────────────────────────────
    # 비워 두면 지금까지처럼 **쿠키 없이** 돕니다. 붙이면 자막·오디오
    # 요청이 로그인 세션으로 나가서, IP 단위로 걸리는 상한(429·403)이
    # 계정 기준으로 올라갑니다 (collector/cookies.py 에 자세히).
    #
    # 둘 중 하나만 쓰면 됩니다. 파일 쪽이 확실합니다 — 브라우저를 켜 둘
    # 필요도, 키체인을 열 필요도 없습니다.
    youtube_cookies_file: str = ""
    # "chrome" · "safari" · "chrome:Profile 1"
    youtube_cookies_browser: str = ""

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
    # **업로드 시점은 여기 없습니다.** 전역 하나(180일)로 두었더니 `경제`·
    # `주식` 은 반년 치가 검색 상위를 차지해 오늘 것을 못 보고, 그렇다고
    # 짧게 잡으면 `면역력`·`과학` 이 굶었습니다. 지금은 키워드마다 정하고
    # (`keywords.search_window_days`), 상한 석 달은 collector/rules.py 의
    # `WINDOW_MAX_DAYS` 에 있습니다 — .env 로 넘길 수 있는 값이 아닙니다.
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
    # 한 번에 메모리로 올리는 길이. **상한이 아니라 이 값이 메모리를
    # 정합니다** — 위스퍼는 오디오를 통째로 16kHz float32 로 올려서 20분이면
    # 77MB 이고, 이보다 긴 영상은 이 길이로 잘라 한 조각씩 올립니다
    # (collector/asr.py `_transcribe_file`). 그래서 세 시간짜리도 20분짜리와
    # 같은 메모리를 씁니다.
    asr_chunk_sec: int = 20 * 60
    # 조각 하나가 조용해도 되는 시간에 얹는 여유. 모델을 올리고(몇 초)
    # 오디오를 잘라 내는(몇 초) 준비 시간입니다 — 조각 길이 자체는
    # `asr_chunk_sec` 이 정합니다 (collector/asr.py `_stall_sec`).
    asr_stall_grace_sec: int = 3 * 60
    # 안전장치. 예전에는 이 값이 **메모리** 상한이기도 해서 90분에 묶여
    # 있었고, 95~142분짜리 33편이 여기 걸려 탈락했습니다 — 길수록 값어치
    # 있는 강의인데 그게 통째로 빠졌습니다. 나눠서 받아쓰게 된 지금은
    # 메모리와 무관하고, **시간만** 봅니다: 3시간짜리가 M4 실측 5배속이면
    # 36분입니다.
    asr_max_duration_sec: int = 180 * 60

    # ── 블로그 발행 (.spec/tistory.md) ──────────────────────
    # 곳간에 쌓인 것을 티스토리로 내보냅니다. 곳간은 집 안에서만 열리는
    # 화면이라, 밖에서 읽으려면 블로그로 나가야 합니다.
    #
    # **기본은 꺼져 있습니다.** 공개 발행은 되돌리기 번거롭습니다 — 올라간
    # 글을 하나씩 내려야 합니다. 워커를 재시작했다는 이유만으로 블로그에
    # 글이 나가서는 안 되므로, 켜는 것은 사람이 한 번 명시적으로 합니다.
    blog_enabled: bool = False
    # 티스토리 Open API 는 2024년에 끝났습니다. 관리 화면이 쓰는 요청을
    # 로그인 세션으로 직접 부르는 CLI 를 씁니다.
    tistory_bin: str = "tistory"
    # 비우면 CLI 에 저장된 기본 블로그.
    tistory_blog: str = ""
    # public | protected | private. public 이면 `--publish -y` 입니다.
    blog_visibility: str = "public"
    # 어떤 판정까지 올릴 것인가. 공개 1,126편의 분포가
    # expert 167 · practical 232 · introductory 368 · irrelevant 272 ·
    # promotional 87 이라, 뒤의 셋까지 넣으면 블로그가 자동 생성물로 덮입니다.
    blog_verdicts: str = "expert,practical"
    # 한 편 올리고 쉬는 시간의 범위(분). 이 사이에서 랜덤으로 다음 차례를
    # 정해 `app_state` 에 적어 둡니다 — 메모리에 두면 워커 재시작마다
    # 쿨링이 없던 일이 됩니다.
    #
    # **티스토리가 하루 30편까지만 공개 발행을 받습니다.** 그래서 간격을
    # 좁혀 봐야 나가는 편수는 안 늘고, 새벽 몇 시간에 30편이 몰려 나간 뒤
    # 하루의 나머지를 통째로 쉽니다(2~10분일 때 03:07에 오늘 몫이 끝났습니다).
    #
    # 1440분 ÷ 30편 = 48분이 하루에 고르게 펴지는 값입니다. 30~60분이면
    # 평균 45분, 하루 32번 시도라 30편을 다 쓰고 조금 남습니다 — 상한에
    # 걸려도 자정까지 자고 다음 날 이어갑니다(blog/publish.py).
    blog_min_interval_min: int = 30
    blog_max_interval_min: int = 60
    # **티스토리가 정한 하루 공개 발행 상한.** 우리가 고를 수 있는 값이
    # 아니라 저쪽 규칙을 적어 두는 자리입니다 — 넘기면 글마다 403 이 옵니다.
    # 저쪽이 숫자를 바꾸면 .env 의 BLOG_DAILY_CAP 으로 맞춥니다.
    blog_daily_cap: int = 30
    # **채널 구독으로 들어온 것은 안 올립니다.**
    #
    # 검색 키워드는 주제를 정해 모은 것이라 글로 묶을 결이 있는데, 채널
    # 구독은 그 채널의 새 영상을 통째로 가져오는 것이라 결이 없습니다 —
    # 카테고리부터 주제가 아니라 채널 이름이 됩니다("박종훈의 지식한방").
    # 곳간에는 그대로 쌓이고, 밖으로 내보내지만 않습니다.
    blog_skip_channel: bool = True
    # 발행 한 건에 걸리는 시간. CLI 가 크로미움을 띄워 세션을 태우므로
    # 몇 초로는 부족합니다.
    blog_cli_timeout_sec: int = 180
    # 제목 생성에 쓰는 시간. 자막이 아니라 이미 만들어진 요약만 넘기므로
    # 요약 호출(900초)보다 훨씬 짧습니다 — 실측 26초.
    # **넉넉히 잡습니다.** 120초로 두었더니 실측에서 한 번 걸렸고, 그때
    # 폴백 제목이 `…5가지 오해 해소 및` 처럼 접속사에서 끊겨 나왔습니다.
    #
    # 발행 간격의 아래쪽(2분)보다 이 값이 큽니다. 제목 짓기가 먹통이 되면
    # 그 한 편이 3분 뒤로 밀린다는 뜻인데, 그래도 됩니다 — 다음 차례는
    # 시각으로 적혀 있어서 밀린 만큼만 늦고 줄이 엉키지는 않습니다.
    # 평소 실측은 26초라 이 일은 거의 일어나지 않습니다.
    blog_title_timeout_sec: int = 180
    # 제목 길이 상한 (공백 포함).
    blog_title_max_len: int = 30

    @property
    def blog_verdict_list(self) -> list[str]:
        return [v.strip() for v in self.blog_verdicts.split(",") if v.strip()]

    @property
    def title_blocklist(self) -> list[str]:
        return [w.strip().lower() for w in self.rule_title_blocklist.split(",") if w.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
