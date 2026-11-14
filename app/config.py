from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://jobradar:jobradar@127.0.0.1:5432/jobradar"
    http_user_agent: str = "PersonalJobRadar/0.1 (+local; single-user)"
    # Global in-flight HTTP ops across all hosts
    max_concurrency: int = 96
    # Concurrent requests per hostname
    host_concurrency: int = 6
    # Background scheduler claim batch
    scheduler_tick_seconds: float = 5.0
    scheduler_claim_limit: int = 24
    # Manual "refresh all" company parallelism
    manual_concurrency: int = 48
    # Parallel detail fetches within one company
    detail_concurrency: int = 12
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    migrations_dir: str = "migrations"
    web_dist_dir: str = "web/dist"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    close_miss_threshold: int = 2
    request_timeout_seconds: float = 15.0
    # Drop non-tech roles at ingest
    tech_roles_only: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
