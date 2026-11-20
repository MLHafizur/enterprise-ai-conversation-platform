from datetime import timedelta

from pydantic import SecretStr

from enterprise_chat_platform.application.code_security_ai import (
    DisabledSecurityExplanationProvider,
    LocalSecurityExplanationProvider,
)
from enterprise_chat_platform.code_security_worker import (
    _explanation_provider,
    _worker_config,
)
from enterprise_chat_platform.settings import Settings

DATABASE_URL = "postgresql+asyncpg://app:password@127.0.0.1:55432/chat"


def test_worker_config_uses_validated_security_settings() -> None:
    settings = Settings(database_url=SecretStr(DATABASE_URL))

    config = _worker_config(settings)

    assert config.lease_duration == timedelta(seconds=900)
    assert config.max_attempts == 3
    assert config.allowed_git_hosts == frozenset({"github.com"})


def test_local_explanations_do_not_require_network_client() -> None:
    settings = Settings(database_url=SecretStr(DATABASE_URL))

    provider = _explanation_provider(settings, http_client=None)

    assert isinstance(provider, LocalSecurityExplanationProvider)


def test_disabled_explanations_fail_closed_without_network_client() -> None:
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        code_security_ai_mode="disabled",
    )

    provider = _explanation_provider(settings, http_client=None)

    assert isinstance(provider, DisabledSecurityExplanationProvider)
