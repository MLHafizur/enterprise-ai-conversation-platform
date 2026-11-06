import pytest
from pydantic import ValidationError

from enterprise_chat_platform.settings import Settings, get_settings


def test_settings_load_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_ENVIRONMENT", "test")
    monkeypatch.setenv("CHAT_SERVICE_NAME", "configured-chat-api")
    monkeypatch.setenv("CHAT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("CHAT_DEBUG", "false")

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.service_name == "configured-chat-api"
    assert settings.log_level == "WARNING"
    assert settings.debug is False


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
        )
