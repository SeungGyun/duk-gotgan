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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
