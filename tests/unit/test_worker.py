import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from enterprise_chat_platform.application.process_agent_run import AgentRunWorkResult
from enterprise_chat_platform.settings import Settings
from enterprise_chat_platform.worker import _chat_provider, _worker_config, run_worker

DATABASE_URL = "postgresql+asyncpg://user:password@127.0.0.1:55432/chat"


def test_worker_config_uses_validated_settings() -> None:
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        worker_lease_seconds=90,
        provider_timeout_seconds=20,
        worker_max_attempts=7,
        worker_initial_backoff_seconds=2,
        worker_max_backoff_seconds=30,
        worker_jitter_fraction=0.1,
    )

    config = _worker_config(settings)

    assert config.lease_duration.total_seconds() == 90
    assert config.provider_timeout.total_seconds() == 20
    assert config.max_attempts == 7
    assert config.initial_backoff.total_seconds() == 2
    assert config.max_backoff.total_seconds() == 30
    assert config.jitter_fraction == 0.1


@pytest.mark.asyncio
async def test_worker_stops_and_disposes_engine() -> None:
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        provider_mode="echo",
        _env_file=None,
    )
    stop_event = asyncio.Event()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory = MagicMock()
    process_worker = MagicMock()
    input_policy = MagicMock()
    prompt_builder = MagicMock()

    async def process_next() -> AgentRunWorkResult:
        stop_event.set()
        return AgentRunWorkResult.NO_WORK

    process_worker.process_next = process_next

    with (
        patch(
            "enterprise_chat_platform.worker.create_database_engine",
            return_value=engine,
        ),
        patch(
            "enterprise_chat_platform.worker.create_session_factory",
            return_value=session_factory,
        ),
        patch(
            "enterprise_chat_platform.worker.ProcessAgentRunWorker",
            return_value=process_worker,
        ) as worker_constructor,
        patch(
            "enterprise_chat_platform.worker.build_conversation_input_policy",
            return_value=input_policy,
        ) as build_input_policy,
        patch(
            "enterprise_chat_platform.worker.TenantPromptBuilder",
            return_value=prompt_builder,
        ) as prompt_builder_constructor,
    ):
        await run_worker(settings=settings, stop_event=stop_event)

    build_input_policy.assert_called_once_with()
    prompt_builder_constructor.assert_called_once_with()
    assert worker_constructor.call_args.kwargs["input_policy"] is input_policy
    assert worker_constructor.call_args.kwargs["prompt_builder"] is prompt_builder
    engine.dispose.assert_awaited_once_with()


def test_worker_provider_is_disabled_by_default() -> None:
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="No chat provider is enabled"):
        _chat_provider(settings)


def test_deployed_environment_rejects_echo_provider() -> None:
    with pytest.raises(ValueError, match="Echo chat provider is forbidden"):
        Settings(
            database_url=SecretStr(DATABASE_URL),
            environment="production",
            auth_mode="jwt",
            jwt_key=SecretStr("public-key-placeholder"),
            provider_mode="echo",
            _env_file=None,
        )
