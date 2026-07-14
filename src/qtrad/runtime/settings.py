"""Validated runtime configuration."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QTRAD_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://qtrad:qtrad-dev-only@db:5432/qtrad"
    migration_database_url: str = "postgresql+psycopg://qtrad:qtrad-dev-only@db:5432/qtrad"
    research_root: Path = Path("data/research")
    capture_universe_path: Path = Path("config/capture-v1.toml")
    capture_source_id: str = "local-development"
    log_level: str = "INFO"

    ig_username: str | None = None
    ig_password: SecretStr | None = None
    ig_api_key: SecretStr | None = None
    ig_account_id: str | None = None
    ig_environment: Literal["demo"] = "demo"

    @field_validator("database_url")
    @classmethod
    def async_postgres_only(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database URL must use postgresql+asyncpg")
        return value

    @field_validator("capture_source_id")
    @classmethod
    def valid_capture_source_id(cls, value: str) -> str:
        if not value or len(value) > 64:
            raise ValueError("capture source ID must contain between 1 and 64 characters")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in value):
            raise ValueError(
                "capture source ID must use lowercase letters, digits, '.', '_' or '-'"
            )
        return value

    def require_ig_credentials(self) -> tuple[str, str, str, str | None]:
        if self.ig_username is None or self.ig_password is None or self.ig_api_key is None:
            raise ValueError(
                "IG demo requires QTRAD_IG_USERNAME, QTRAD_IG_PASSWORD and QTRAD_IG_API_KEY"
            )
        return (
            self.ig_username,
            self.ig_password.get_secret_value(),
            self.ig_api_key.get_secret_value(),
            self.ig_account_id,
        )
