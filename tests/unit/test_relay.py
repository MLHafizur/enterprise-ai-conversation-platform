import pytest
from pydantic import SecretStr

from enterprise_chat_platform.relay import _relay_config, run_relay
from enterprise_chat_platform.settings import Settings

DATABASE_URL = "postgresql+asyncpg://user:password@127.0.0.1:55432/chat"


def test_relay_config_uses_validated_settings() -> None:
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        realtime_relay_lease_seconds=45,
        realtime_relay_initial_backoff_seconds=2,
        realtime_relay_max_backoff_seconds=20,
    )

    config = _relay_config(settings)

    assert config.lease_duration.total_seconds() == 45
    assert config.initial_backoff.total_seconds() == 2
    assert config.max_backoff.total_seconds() == 20


@pytest.mark.asyncio
async def test_relay_fails_closed_when_realtime_is_disabled() -> None:
    settings = Settings(database_url=SecretStr(DATABASE_URL))

    with pytest.raises(RuntimeError, match="Realtime must be enabled"):
        await run_relay(settings=settings)
