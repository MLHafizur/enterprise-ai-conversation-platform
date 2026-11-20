from typing import Literal

import pytest
from pydantic import SecretStr, ValidationError

from enterprise_chat_platform.settings import (
    MigrationSettings,
    Settings,
    get_settings,
)

RUNTIME_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)

MIGRATION_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_migrator:"
    "migration-test-password@127.0.0.1:55432/enterprise_chat"
)


def test_settings_load_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_ENVIRONMENT", "test")
    monkeypatch.setenv("CHAT_SERVICE_NAME", "configured-chat-api")
    monkeypatch.setenv("CHAT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("CHAT_DEBUG", "false")
    monkeypatch.setenv("CHAT_DATABASE_URL", RUNTIME_DATABASE_URL)
    monkeypatch.setenv("CHAT_AUTH_MODE", "jwt")
    monkeypatch.setenv("CHAT_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("CHAT_JWT_KEY", "environment-test-jwt-key")
    monkeypatch.setenv("CHAT_JWT_ISSUER", "configured-issuer")
    monkeypatch.setenv("CHAT_JWT_AUDIENCE", "configured-audience")

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.service_name == "configured-chat-api"
    assert settings.log_level == "WARNING"
    assert settings.debug is False
    assert settings.database_url.get_secret_value() == RUNTIME_DATABASE_URL
    assert settings.auth_mode == "jwt"
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_key is not None
    assert settings.jwt_key.get_secret_value() == "environment-test-jwt-key"
    assert settings.jwt_issuer == "configured-issuer"
    assert settings.jwt_audience == "configured-audience"


def test_migration_settings_load_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CHAT_MIGRATION_DATABASE_URL",
        MIGRATION_DATABASE_URL,
    )

    settings = MigrationSettings(_env_file=None)

    assert settings.migration_database_url.get_secret_value() == MIGRATION_DATABASE_URL


def test_runtime_database_url_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAT_DATABASE_URL", raising=False)

    with pytest.raises(
        ValidationError,
        match="database_url",
    ):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("database_url", "expected_message"),
    [
        (
            "postgresql://user:password@localhost/database",
            r"postgresql\+asyncpg",
        ),
        (
            "sqlite+aiosqlite:///database.db",
            r"postgresql\+asyncpg",
        ),
        (
            "postgresql+asyncpg://:password@localhost/database",
            "username",
        ),
        (
            "postgresql+asyncpg://user@localhost/database",
            "password",
        ),
        (
            "postgresql+asyncpg://user:password@/database",
            "host",
        ),
        (
            "postgresql+asyncpg://user:password@localhost",
            "database name",
        ),
    ],
)
def test_invalid_runtime_database_url_is_rejected(
    database_url: str,
    expected_message: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match=expected_message,
    ):
        Settings(
            database_url=SecretStr(database_url),
            _env_file=None,
        )


def test_database_secret_is_not_exposed() -> None:
    settings = Settings(
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        _env_file=None,
    )

    rendered_settings = f"{settings!r}\n{settings.model_dump_json()}"

    assert "runtime-test-password" not in rendered_settings


def test_invalid_database_url_error_does_not_expose_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_password = "must-not-appear"
    invalid_url = (
        f"postgresql+asyncpg://enterprise_chat_app:{secret_password}"
        "@127.0.0.1:not-a-port/enterprise_chat"
    )
    monkeypatch.setenv("CHAT_DATABASE_URL", invalid_url)

    with pytest.raises(ValidationError) as captured_error:
        Settings(_env_file=None)

    rendered_error = "\n".join(
        (
            str(captured_error.value),
            captured_error.value.json(),
            repr(captured_error.value.errors()),
        )
    )

    assert "valid SQLAlchemy URL" in rendered_error
    assert secret_password not in rendered_error


def test_debug_is_rejected_in_production() -> None:
    with pytest.raises(
        ValidationError,
        match="Debug mode must be disabled in production",
    ):
        Settings(
            environment="production",
            service_name="enterprise-chat-api",
            log_level="INFO",
            debug=True,
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            _env_file=None,
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_development_authentication_is_rejected_in_deployed_environments(
    environment: Literal["staging", "production"],
) -> None:
    with pytest.raises(
        ValidationError,
        match="JWT authentication is required",
    ):
        Settings(
            environment=environment,
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="development",
            _env_file=None,
        )


def test_jwt_authentication_requires_a_key() -> None:
    with pytest.raises(
        ValidationError,
        match="JWT key is required",
    ):
        Settings(
            environment="test",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_key=None,
            _env_file=None,
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_symmetric_jwt_is_rejected_in_deployed_environments(
    environment: Literal["staging", "production"],
) -> None:
    with pytest.raises(
        ValidationError,
        match="requires an asymmetric algorithm",
    ):
        Settings(
            environment=environment,
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_algorithm="HS256",
            jwt_key=SecretStr("test-shared-secret"),
            _env_file=None,
        )


def test_symmetric_jwt_is_allowed_in_test_environment() -> None:
    settings = Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt",
        jwt_algorithm="HS256",
        jwt_key=SecretStr("test-shared-secret"),
        _env_file=None,
    )

    assert settings.auth_mode == "jwt"
    assert settings.jwt_algorithm == "HS256"


def test_asymmetric_jwt_is_allowed_in_production() -> None:
    settings = Settings(
        environment="production",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt",
        jwt_algorithm="RS256",
        jwt_key=SecretStr("public-verification-key"),
        _env_file=None,
    )

    assert settings.auth_mode == "jwt"
    assert settings.jwt_algorithm == "RS256"


def test_jwt_key_is_not_exposed() -> None:
    secret_key = "jwt-key-must-not-appear"
    settings = Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt",
        jwt_algorithm="HS256",
        jwt_key=SecretStr(secret_key),
        _env_file=None,
    )

    rendered_settings = f"{settings!r}\n{settings.model_dump_json()}"

    assert secret_key not in rendered_settings


def test_demo_authentication_requires_local_symmetric_jwt() -> None:
    with pytest.raises(
        ValidationError,
        match="Demo authentication requires JWT authentication mode",
    ):
        Settings(
            environment="test",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            demo_auth_enabled=True,
            _env_file=None,
        )

    with pytest.raises(
        ValidationError,
        match="Demo authentication requires the local HS256 algorithm",
    ):
        Settings(
            environment="test",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_algorithm="RS256",
            jwt_key=SecretStr("verification-key"),
            demo_auth_enabled=True,
            _env_file=None,
        )


def test_demo_authentication_is_forbidden_when_deployed() -> None:
    with pytest.raises(
        ValidationError,
        match="Demo authentication is forbidden",
    ):
        Settings(
            environment="production",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_algorithm="RS256",
            jwt_key=SecretStr("verification-key"),
            demo_auth_enabled=True,
            _env_file=None,
        )


def test_realtime_requires_jwt_and_an_origin_allowlist() -> None:
    with pytest.raises(
        ValidationError,
        match="Realtime WebSockets require JWT authentication mode",
    ):
        Settings(
            environment="test",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            realtime_enabled=True,
            _env_file=None,
        )

    with pytest.raises(
        ValidationError,
        match="at least one allowed origin",
    ):
        Settings(
            environment="test",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_algorithm="HS256",
            jwt_key=SecretStr("test-shared-secret"),
            realtime_enabled=True,
            websocket_allowed_origins=" , ",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "origin",
    [
        "javascript:alert(1)",
        "https://user:password@example.test",
        "https://example.test/path",
        "https://example.test?query=value",
        "https://example.test:not-a-port",
    ],
)
def test_invalid_websocket_origins_are_rejected(origin: str) -> None:
    with pytest.raises(
        ValidationError,
        match="valid HTTP origins",
    ):
        Settings(
            environment="test",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_algorithm="HS256",
            jwt_key=SecretStr("test-shared-secret"),
            realtime_enabled=True,
            websocket_allowed_origins=origin,
            _env_file=None,
        )


def test_websocket_origins_are_normalized_and_deployed_wildcards_are_rejected() -> None:
    local_settings = Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt",
        jwt_algorithm="HS256",
        jwt_key=SecretStr("test-shared-secret"),
        realtime_enabled=True,
        websocket_allowed_origins=" https://example.test/ , * ",
        _env_file=None,
    )

    assert local_settings.allowed_websocket_origins == {"https://example.test", "*"}

    with pytest.raises(
        ValidationError,
        match="Wildcard WebSocket origins are forbidden",
    ):
        Settings(
            environment="production",
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            auth_mode="jwt",
            jwt_algorithm="RS256",
            jwt_key=SecretStr("verification-key"),
            realtime_enabled=True,
            websocket_allowed_origins="*",
            _env_file=None,
        )


def test_invalid_redis_url_is_rejected_without_exposing_credentials() -> None:
    secret = "redis-password-must-not-appear"

    with pytest.raises(ValidationError) as captured_error:
        Settings(
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            redis_url=SecretStr(f"redis://default:{secret}@localhost:not-a-port/0"),
            _env_file=None,
        )

    rendered_error = str(captured_error.value)
    assert "Redis URL must be a valid URL" in rendered_error
    assert secret not in rendered_error


def test_code_security_webhook_secret_requires_adequate_entropy_budget() -> None:
    with pytest.raises(
        ValidationError,
        match="webhook secret must contain at least 32 characters",
    ):
        Settings(
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            code_security_webhook_secret=SecretStr("too-short"),
            _env_file=None,
        )


def test_code_security_lease_covers_git_and_analysis_timeouts() -> None:
    with pytest.raises(
        ValidationError,
        match="complete repository operation budget",
    ):
        Settings(
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            code_security_clone_timeout_seconds=30,
            code_security_scan_timeout_seconds=120,
            code_security_worker_lease_seconds=240,
            _env_file=None,
        )


def test_code_security_findings_limit_matches_the_persistence_and_ui_contract() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 10000"):
        Settings(
            database_url=SecretStr(RUNTIME_DATABASE_URL),
            code_security_max_findings_per_scan=10_001,
            _env_file=None,
        )
