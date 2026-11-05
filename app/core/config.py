from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = SettingsConfigDict(
    env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
)


class DatabaseSettings(BaseSettings):
    """마이그레이션처럼 DB 주소만 필요한 곳에서 사용 (봇 토큰 등이 없어도 동작)."""

    model_config = _ENV

    database_url: str


class Settings(BaseSettings):
    """환경변수(.env)에서 읽는 애플리케이션 설정. 각 항목은 .env.example에 문서화한다."""

    model_config = _ENV

    discord_token: str
    guild_id: int
    member_role_id: int
    admin_role_id: int
    admin_channel_id: int
    report_channel_id: int
    verification_channel_id: int | None = None

    database_url: str

    initial_weekly_target: int = Field(default=4, ge=1, le=7)
    text_min_length: int = Field(default=10, ge=0)
    image_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    phash_distance_threshold: int = Field(default=4, ge=0, le=64)
    image_dir: str = "/data/images"
    image_retention_days: int = Field(default=30, ge=1)
    report_hour: int = Field(default=9, ge=0, le=23)

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
