from functools import lru_cache
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
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


def _validate_redis_url(value: SecretStr) -> SecretStr:
    try:
        parsed = urlsplit(value.get_secret_value())
        _ = parsed.port
    except ValueError:
        raise ValueError("Redis URL must be a valid URL") from None

    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError("Redis URL must use the redis or rediss scheme")
    if parsed.hostname is None:
        raise ValueError("Redis URL must include a host")
    if parsed.fragment:
        raise ValueError("Redis URL cannot include a fragment")
    return value


type RedisUrl = Annotated[
    SecretStr,
    AfterValidator(_validate_redis_url),
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
    demo_auth_enabled: bool = False
    access_token_expire_minutes: int = Field(default=480, ge=1, le=1440)

    realtime_enabled: bool = False
    redis_url: RedisUrl = SecretStr("redis://127.0.0.1:6379/0")
    websocket_auth_timeout_seconds: float = Field(default=10, gt=0, le=60)
    websocket_max_message_bytes: int = Field(default=32_768, ge=1024, le=1_048_576)
    websocket_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    connection_lease_seconds: int = Field(default=90, ge=10, le=3600)
    per_user_messages_per_minute: int = Field(default=10, ge=1, le=10_000)
    realtime_relay_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    realtime_relay_lease_seconds: int = Field(default=30, ge=2, le=3600)
    realtime_relay_initial_backoff_seconds: float = Field(default=1, gt=0, le=3600)
    realtime_relay_max_backoff_seconds: float = Field(default=30, gt=0, le=86_400)

    worker_service_author_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    worker_lease_seconds: int = Field(default=60, ge=2, le=3600)
    provider_timeout_seconds: int = Field(default=30, ge=1, le=1800)
    worker_max_attempts: int = Field(default=5, ge=1, le=100)
    worker_initial_backoff_seconds: float = Field(default=1, gt=0, le=3600)
    worker_max_backoff_seconds: float = Field(default=60, gt=0, le=86_400)
    worker_jitter_fraction: float = Field(default=0.2, ge=0, le=1)
    provider_mode: Literal["disabled", "echo", "gemini"] = "disabled"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # Day 10 secure-code-analysis bounded context. Repository ingestion runs in
    # its own leased worker because cloning and AST analysis must never block an
    # API request or share the chat worker's provider budget.
    code_security_enabled: bool = True
    code_security_ai_mode: Literal["disabled", "echo", "gemini"] = "echo"
    code_security_webhook_secret: SecretStr | None = None
    code_security_allowed_git_hosts: str = "github.com"
    code_security_clone_timeout_seconds: int = Field(default=30, ge=1, le=600)
    code_security_scan_timeout_seconds: int = Field(default=120, ge=2, le=3600)
    code_security_worker_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    code_security_worker_lease_seconds: int = Field(default=900, ge=3, le=7200)
    code_security_worker_max_attempts: int = Field(default=3, ge=1, le=20)
    code_security_worker_initial_backoff_seconds: float = Field(default=2, gt=0, le=3600)
    code_security_worker_max_backoff_seconds: float = Field(default=60, gt=0, le=86_400)
    code_security_max_file_size_bytes: int = Field(
        default=10_485_760,
        ge=1_024,
        le=10_485_760,
    )
    code_security_max_repository_files: int = Field(default=2_000, ge=1, le=100_000)
    code_security_max_repository_bytes: int = Field(
        default=52_428_800,
        ge=1_048_576,
        le=1_073_741_824,
    )
    code_security_max_findings_per_scan: int = Field(default=10_000, ge=1, le=10_000)
    code_security_github_token: SecretStr | None = None
    code_security_github_api_base_url: str = "https://api.github.com"
    code_security_slack_webhook_url: SecretStr | None = None

    @property
    def allowed_websocket_origins(self) -> frozenset[str]:
        """Return the normalized origin allowlist used during WS upgrades."""
        return frozenset(
            origin.strip().rstrip("/")
            for origin in self.websocket_allowed_origins.split(",")
            if origin.strip()
        )

    @property
    def allowed_code_repository_hosts(self) -> frozenset[str]:
        """Return normalized HTTPS Git hosts that repository jobs may contact."""
        return frozenset(
            host.strip().lower().rstrip(".")
            for host in self.code_security_allowed_git_hosts.split(",")
            if host.strip()
        )

    @staticmethod
    def _validate_websocket_origin(origin: str) -> None:
        if origin == "*":
            return

        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except ValueError:
            raise ValueError("WebSocket origins must be valid HTTP origins") from None

        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("WebSocket origins must be valid HTTP origins")

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

        if is_deployed_environment and self.demo_auth_enabled:
            raise ValueError("Demo authentication is forbidden in staging and production")

        if self.demo_auth_enabled:
            if self.auth_mode != "jwt":
                raise ValueError("Demo authentication requires JWT authentication mode")
            if self.jwt_algorithm != "HS256":
                raise ValueError("Demo authentication requires the local HS256 algorithm")

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

        if self.realtime_relay_max_backoff_seconds < self.realtime_relay_initial_backoff_seconds:
            raise ValueError("Relay maximum backoff cannot be shorter than initial backoff")

        if self.realtime_relay_lease_seconds <= self.realtime_relay_poll_interval_seconds:
            raise ValueError("Relay lease must exceed its polling interval")

        if self.realtime_enabled and self.auth_mode != "jwt":
            raise ValueError("Realtime WebSockets require JWT authentication mode")

        if self.realtime_enabled and not self.allowed_websocket_origins:
            raise ValueError("Realtime WebSockets require at least one allowed origin")

        for origin in self.allowed_websocket_origins:
            self._validate_websocket_origin(origin)

        if is_deployed_environment and "*" in self.allowed_websocket_origins:
            raise ValueError("Wildcard WebSocket origins are forbidden when deployed")

        if is_deployed_environment and self.provider_mode == "echo":
            raise ValueError("The Echo chat provider is forbidden in staging and production")

        if self.provider_mode == "gemini":
            if self.gemini_api_key is None or not self.gemini_api_key.get_secret_value().strip():
                raise ValueError("Gemini API key is required when Gemini is enabled")
            if not self.gemini_api_base_url.startswith("https://"):
                raise ValueError("Gemini API base URL must use HTTPS")

        if self.code_security_enabled and not self.allowed_code_repository_hosts:
            raise ValueError("Code security requires at least one allowed Git host")
        for host in self.allowed_code_repository_hosts:
            if (
                host == "*"
                or ":" in host
                or "/" in host
                or host.startswith(".")
                or host.endswith(".")
                or any(
                    not label or not label.replace("-", "").isalnum() for label in host.split(".")
                )
            ):
                raise ValueError("Allowed Git hosts must be explicit DNS hostnames")

        # A pull-request scan may need bounded history deepening before a merge
        # base exists. Budget every Git subprocess timeout, the analyzer, the
        # optional advisory provider, and a small commit/notification margin.
        worst_case_scan_seconds = (
            self.code_security_scan_timeout_seconds
            + (20 * self.code_security_clone_timeout_seconds)
            + self.provider_timeout_seconds
            + 30
        )
        if self.code_security_worker_lease_seconds <= worst_case_scan_seconds:
            raise ValueError(
                "Code security worker lease must exceed its complete repository operation budget"
            )
        if (
            self.code_security_worker_max_backoff_seconds
            < self.code_security_worker_initial_backoff_seconds
        ):
            raise ValueError(
                "Code security worker maximum backoff cannot be shorter than initial backoff"
            )

        if self.code_security_ai_mode == "gemini":
            if self.gemini_api_key is None or not self.gemini_api_key.get_secret_value().strip():
                raise ValueError("Gemini API key is required for code security AI")
            if not self.gemini_api_base_url.startswith("https://"):
                raise ValueError("Gemini API base URL must use HTTPS")

        if self.code_security_webhook_secret is not None:
            webhook_secret = self.code_security_webhook_secret.get_secret_value()
            if len(webhook_secret) < 32:
                raise ValueError("Code security webhook secret must contain at least 32 characters")

        if not self.code_security_github_api_base_url.startswith("https://"):
            raise ValueError("GitHub API base URL must use HTTPS")
        if self.code_security_slack_webhook_url is not None:
            slack_url = self.code_security_slack_webhook_url.get_secret_value()
            if slack_url and not slack_url.startswith("https://hooks.slack.com/"):
                raise ValueError("Slack webhook URL must use the official HTTPS endpoint")

        return self


class MigrationSettings(_BaseSettings):
    migration_database_url: AsyncPostgresUrl


@lru_cache
def get_settings() -> Settings:
    return Settings()
