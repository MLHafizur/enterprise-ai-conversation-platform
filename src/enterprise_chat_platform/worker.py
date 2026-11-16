"""Runnable background worker for durable queued agent runs."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

import httpx

from enterprise_chat_platform.application.process_agent_run import (
    AgentRunWorkerConfig,
    AgentRunWorkResult,
    ChatProvider,
    ProcessAgentRunWorker,
)
from enterprise_chat_platform.application.tenant_prompting import TenantPromptBuilder
from enterprise_chat_platform.domain.conversation_policy import (
    build_conversation_input_policy,
)
from enterprise_chat_platform.domain.identifiers import MessageId, UserId
from enterprise_chat_platform.infrastructure.chat_provider import EchoChatProvider
from enterprise_chat_platform.infrastructure.gemini_provider import GeminiChatProvider
from enterprise_chat_platform.infrastructure.persistence.agent_run_job_store import (
    SqlAlchemyAgentRunJobStore,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock
from enterprise_chat_platform.observability import configure_logging
from enterprise_chat_platform.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class UuidWorkerIds:
    """Generate server-controlled assistant message identifiers."""

    def new_message_id(self) -> MessageId:
        return MessageId.new()


def _worker_config(settings: Settings) -> AgentRunWorkerConfig:
    return AgentRunWorkerConfig(
        lease_duration=timedelta(seconds=settings.worker_lease_seconds),
        provider_timeout=timedelta(seconds=settings.provider_timeout_seconds),
        max_attempts=settings.worker_max_attempts,
        initial_backoff=timedelta(seconds=settings.worker_initial_backoff_seconds),
        max_backoff=timedelta(seconds=settings.worker_max_backoff_seconds),
        jitter_fraction=settings.worker_jitter_fraction,
    )


def _chat_provider(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ChatProvider:
    """Fail closed unless an explicitly enabled provider adapter is available."""
    if settings.provider_mode == "echo":
        return EchoChatProvider()
    if settings.provider_mode == "gemini":
        if http_client is None:
            raise RuntimeError("Gemini chat provider requires an HTTP client")
        key = settings.gemini_api_key
        if key is None:
            raise RuntimeError("Validated Gemini provider is missing its API key")
        return GeminiChatProvider(
            client=http_client,
            api_key=key.get_secret_value(),
            model=settings.gemini_model,
            timeout_seconds=settings.provider_timeout_seconds,
            api_base_url=settings.gemini_api_base_url,
            transient_transport_errors=(
                TimeoutError,
                OSError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ),
        )
    raise RuntimeError("No chat provider is enabled for this worker")


async def run_worker(
    *,
    settings: Settings | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll durable work until shutdown, isolating transient poll failures."""
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)
    shutdown = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()

    if stop_event is None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, shutdown.set)
            except NotImplementedError:  # pragma: no cover - Windows fallback
                pass

    engine = create_database_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    http_client = httpx.AsyncClient() if resolved_settings.provider_mode == "gemini" else None
    worker = ProcessAgentRunWorker(
        store=SqlAlchemyAgentRunJobStore(session_factory),
        provider=_chat_provider(resolved_settings, http_client=http_client),
        input_policy=build_conversation_input_policy(),
        clock=UtcSystemClock(),
        ids=UuidWorkerIds(),
        service_author_id=UserId(resolved_settings.worker_service_author_id),
        config=_worker_config(resolved_settings),
        prompt_builder=TenantPromptBuilder(),
    )

    logger.info("agent worker started")
    try:
        while not shutdown.is_set():
            try:
                result = await worker.process_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("agent worker poll failed")
                result = AgentRunWorkResult.NO_WORK

            if result is AgentRunWorkResult.NO_WORK:
                try:
                    await asyncio.wait_for(
                        shutdown.wait(),
                        timeout=resolved_settings.worker_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        if http_client is not None:
            await http_client.aclose()
        await engine.dispose()
        logger.info("agent worker stopped")


def main() -> None:
    """Console-script entry point."""
    asyncio.run(run_worker())


if __name__ == "__main__":  # pragma: no cover
    main()
