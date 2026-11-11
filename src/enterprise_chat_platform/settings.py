from functools import lru_cache
from typing import Annotated, Literal, Self

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

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError("Debug mode must be disabled in production")
        return self


class MigrationSettings(_BaseSettings):
    migration_database_url: AsyncPostgresUrl


@lru_cache
def get_settings() -> Settings:
    return Settings()
