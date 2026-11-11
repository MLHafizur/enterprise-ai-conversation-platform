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

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.service_name == "configured-chat-api"
    assert settings.log_level == "WARNING"
    assert settings.debug is False
    assert settings.database_url.get_secret_value() == RUNTIME_DATABASE_URL


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
