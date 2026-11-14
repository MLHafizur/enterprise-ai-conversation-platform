from functools import lru_cache
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _mask_secret_input(value: object) -> object:
    if isinstance(value, str):
        return SecretStr(value)

    return value


def _validate_async_postgres_url(value: SecretStr) -> SecretStr:
    try:
        database_url = make_url(value.get_secret_value())
    except (ArgumentError, ValueError):
        raise ValueError("Database URL must be a valid SQLAlchemy URL") from None

    if database_url.drivername != "postgresql+asyncpg":
        raise ValueError("Database URL must use the postgresql+asyncpg driver")

    if not database_url.username:
        raise ValueError("Database URL must include a username")

    if not database_url.password:
        raise ValueError("Database URL must include a password")

    if not database_url.host:
        raise ValueError("Database URL must include a host")

    if not database_url.database:
        raise ValueError("Database URL must include a database name")

    return value


type AsyncPostgresUrl = Annotated[
    SecretStr,
    AfterValidator(_validate_async_postgres_url),
    BeforeValidator(_mask_secret_input),
]


class _BaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CHAT_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class Settings(_BaseSettings):
    environment: Literal["local", "test", "staging", "production"] = "local"
    service_name: str = Field(
        default="enterprise-chat-api",
        min_length=1,
    )
    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"
    debug: bool = False
    database_url: AsyncPostgresUrl
    auth_mode: Literal["development", "jwt"] = "development"
    allow_insecure_development_auth: bool = False
    jwt_algorithm: Literal["HS256", "RS256", "ES256"] = "RS256"
    jwt_key: SecretStr | None = None
    jwt_issuer: str = Field(
        default="enterprise-chat-platform",
        min_length=1,
    )
    jwt_audience: str = Field(
        default="enterprise-chat-api",
        min_length=1,
    )
    worker_service_author_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    worker_lease_seconds: int = Field(default=60, ge=2, le=3600)
    provider_timeout_seconds: int = Field(default=30, ge=1, le=1800)
    worker_max_attempts: int = Field(default=5, ge=1, le=100)
    worker_initial_backoff_seconds: float = Field(default=1, gt=0, le=3600)
    worker_max_backoff_seconds: float = Field(default=60, gt=0, le=86_400)
    worker_jitter_fraction: float = Field(default=0.2, ge=0, le=1)
    provider_mode: Literal["disabled", "echo"] = "disabled"

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError("Debug mode must be disabled in production")

        is_deployed_environment = self.environment in {"staging", "production"}

        if is_deployed_environment and self.auth_mode != "jwt":
            raise ValueError("JWT authentication is required in staging and production")

        if is_deployed_environment and self.allow_insecure_development_auth:
            raise ValueError(
                "Development authentication opt-in is forbidden in staging and production"
            )

        if self.auth_mode == "jwt":
            if self.jwt_key is None or not self.jwt_key.get_secret_value().strip():
                raise ValueError("JWT key is required when JWT authentication is enabled")

            if is_deployed_environment and self.jwt_algorithm == "HS256":
                raise ValueError(
                    "Staging and production JWT authentication requires an asymmetric algorithm"
                )

        if self.worker_lease_seconds <= self.provider_timeout_seconds:
            raise ValueError("Worker lease must exceed the provider timeout")

        if self.worker_max_backoff_seconds < self.worker_initial_backoff_seconds:
            raise ValueError("Worker maximum backoff cannot be shorter than initial backoff")

        if is_deployed_environment and self.provider_mode == "echo":
            raise ValueError("The Echo chat provider is forbidden in staging and production")

        return self


class MigrationSettings(_BaseSettings):
    migration_database_url: AsyncPostgresUrl


@lru_cache
def get_settings() -> Settings:
    return Settings()
