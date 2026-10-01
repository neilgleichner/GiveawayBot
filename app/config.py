from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr = Field(alias="BOT_TOKEN")
    database_url: SecretStr = Field(alias="DATABASE_URL")
    redis_url: SecretStr = Field(alias="REDIS_URL")
    admin_telegram_ids: set[int] = Field(alias="ADMIN_TELEGRAM_IDS")
    channel_username: str = Field(alias="CHANNEL_USERNAME")
    comment_chat_id: int = Field(alias="COMMENT_CHAT_ID")
    timezone: str = Field(default="Asia/Tehran", alias="TIMEZONE")
    webhook_secret: SecretStr = Field(alias="WEBHOOK_SECRET")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    export_ttl_hours: int = Field(default=24, alias="EXPORT_TTL_HOURS", ge=1, le=168)

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: str | int | set[int] | list[int]) -> set[int]:
        """Accept a single ID or a comma-separated allowlist from .env."""
        if isinstance(value, int):
            return {value}
        if isinstance(value, (set, list)):
            return {int(item) for item in value}
        return {int(item.strip()) for item in value.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
