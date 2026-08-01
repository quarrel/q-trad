"""Validated runtime configuration."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QTRAD_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://qtrad:qtrad-dev-only@db:5432/qtrad"
    migration_database_url: str = "postgresql+psycopg://qtrad:qtrad-dev-only@db:5432/qtrad"
    research_root: Path = Path("data/research")
    capture_universe_path: Path = Path("config/capture-v1.toml")
    capture_source_id: str = "local-development"
    image: str = "qtrad-app:local"
    log_level: str = "INFO"

    ig_username: str | None = None
    ig_password: SecretStr | None = None
    ig_api_key: SecretStr | None = None
    ig_account_id: str | None = None
    ig_environment: Literal["demo"] = "demo"

    ibkr_gateway_host: str = "127.0.0.1"
    ibkr_gateway_port: int = 4002
    ibkr_client_id: int = 71
    ibkr_api_version: Literal["10.49", "10.45"] = "10.49"
    ibkr_gateway_version: Literal["10.49", "10.45"] = "10.49"
    ibkr_api_package_fingerprint: str | None = None
    ibkr_connect_timeout_seconds: float = 5.0
    ibkr_handshake_timeout_seconds: float = 15.0
    ibkr_server_time_timeout_seconds: float = 10.0
    ibkr_contract_timeout_seconds: float = 30.0
    ibkr_historical_timeout_seconds: float = 60.0
    ibkr_upstream_recovery_timeout_seconds: float = 180.0
    ibkr_gateway_restart_after_seconds: float = 300.0
    ibkr_gateway_restart_cooldown_seconds: float = 900.0
    ibkr_gateway_restart_limit_per_hour: int = 3
    ibkr_checkpoint_root: Path = Path("data/ibkr/checkpoints")

    @model_validator(mode="after")
    def validate_ibkr_stack(self) -> "Settings":
        if self.ibkr_api_version != self.ibkr_gateway_version:
            raise ValueError("IBKR Gateway and API versions must match")
        timeouts = (
            self.ibkr_connect_timeout_seconds,
            self.ibkr_handshake_timeout_seconds,
            self.ibkr_server_time_timeout_seconds,
            self.ibkr_contract_timeout_seconds,
            self.ibkr_historical_timeout_seconds,
            self.ibkr_upstream_recovery_timeout_seconds,
            self.ibkr_gateway_restart_after_seconds,
            self.ibkr_gateway_restart_cooldown_seconds,
        )
        if any(timeout <= 0 for timeout in timeouts) or (
            self.ibkr_gateway_restart_limit_per_hour <= 0
        ):
            raise ValueError("IBKR session timeouts and restart limit must be positive")
        return self

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

    @field_validator("ibkr_gateway_host")
    @classmethod
    def valid_ibkr_gateway_host(cls, value: str) -> str:
        if not value or len(value) > 253 or any(character.isspace() for character in value):
            raise ValueError("IBKR Gateway host must be a bounded non-whitespace value")
        return value

    @field_validator("ibkr_gateway_port")
    @classmethod
    def valid_ibkr_gateway_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("IBKR Gateway port must be between 1 and 65535")
        return value

    @field_validator("ibkr_client_id")
    @classmethod
    def valid_ibkr_client_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("IBKR client ID must be positive; client ID zero is not permitted")
        return value

    @field_validator("image")
    @classmethod
    def valid_image_identity(cls, value: str) -> str:
        if not value or len(value) > 500 or any(character.isspace() for character in value):
            raise ValueError("application image identity must be a bounded non-whitespace value")
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
