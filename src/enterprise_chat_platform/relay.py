"""Runnable relay from PostgreSQL's durable outbox to Redis Pub/Sub."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable
from datetime import timedelta
from typing import cast

from enterprise_chat_platform.application.process_terminal_notifications import (
    ProcessTerminalNotificationRelay,
    TerminalNotificationRelayConfig,
    TerminalNotificationRelayResult,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.notification_relay_store import (
    SqlAlchemyNotificationRelayStore,
)
from enterprise_chat_platform.infrastructure.realtime.publisher import (
    RedisPublisherClient,
    RedisTerminalNotificationPublisher,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import create_redis_client
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock
from enterprise_chat_platform.observability import configure_logging
from enterprise_chat_platform.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def _relay_config(settings: Settings) -> TerminalNotificationRelayConfig:
    return TerminalNotificationRelayConfig(
        lease_duration=timedelta(seconds=settings.realtime_relay_lease_seconds),
        initial_backoff=timedelta(seconds=settings.realtime_relay_initial_backoff_seconds),
        max_backoff=timedelta(seconds=settings.realtime_relay_max_backoff_seconds),
    )


async def run_relay(
    *,
    settings: Settings | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)
    if not resolved_settings.realtime_enabled:
        raise RuntimeError("Realtime must be enabled to run the notification relay")

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
    redis_client = create_redis_client(resolved_settings)
    relay = ProcessTerminalNotificationRelay(
        store=SqlAlchemyNotificationRelayStore(session_factory),
        publisher=RedisTerminalNotificationPublisher(cast(RedisPublisherClient, redis_client)),
        clock=UtcSystemClock(),
        config=_relay_config(resolved_settings),
    )

    try:
        async with asyncio.timeout(2):
            await cast(Awaitable[bool], redis_client.ping())
        logger.info("notification relay started")
        while not shutdown.is_set():
            try:
                result = await relay.process_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notification relay poll failed")
                result = TerminalNotificationRelayResult.NO_WORK

            if result is TerminalNotificationRelayResult.NO_WORK:
                try:
                    await asyncio.wait_for(
                        shutdown.wait(),
                        timeout=resolved_settings.realtime_relay_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        await redis_client.aclose()
        await engine.dispose()
        logger.info("notification relay stopped")


def main() -> None:
    asyncio.run(run_relay())


if __name__ == "__main__":  # pragma: no cover
    main()
