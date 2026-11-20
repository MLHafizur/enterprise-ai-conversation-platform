"""Redis Pub/Sub subscriber that accelerates durable REST recovery paths."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from redis.exceptions import RedisError

from enterprise_chat_platform.infrastructure.realtime.connection_manager import (
    RealtimeConnectionManager,
)
from enterprise_chat_platform.infrastructure.realtime.protocol import (
    CodeScanNotification,
    realtime_notification_adapter,
)

logger = logging.getLogger(__name__)

TENANT_EVENT_CHANNEL_PATTERN = "chat:tenant:*:events"


def tenant_event_channel(tenant_id: UUID) -> str:
    return f"chat:tenant:{tenant_id}:events"


class RedisPubSub(Protocol):
    async def psubscribe(self, *patterns: str) -> object: ...

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,  # noqa: ASYNC109 -- mirrors the redis-py keyword API
    ) -> dict[str, object] | None: ...

    async def aclose(self) -> None: ...


class RedisSubscriberClient(Protocol):
    def pubsub(self) -> RedisPubSub: ...


class RealtimeNotificationSubscriber:
    """Reconnect to disposable Pub/Sub while clients retain REST fallback."""

    def __init__(
        self,
        *,
        client: RedisSubscriberClient,
        manager: RealtimeConnectionManager,
        retry_seconds: float = 1.0,
    ) -> None:
        self._client = client
        self._manager = manager
        self._retry_seconds = retry_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            pubsub: RedisPubSub | None = None
            try:
                pubsub = self._client.pubsub()
                await pubsub.psubscribe(TENANT_EVENT_CHANNEL_PATTERN)
                while not stop_event.is_set():
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message is not None:
                        await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except RedisError:
                logger.warning("realtime subscriber unavailable")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._retry_seconds)
                except TimeoutError:
                    pass
            finally:
                if pubsub is not None:
                    await pubsub.aclose()

    async def _handle_message(self, message: dict[str, object]) -> None:
        raw_data = message.get("data")
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")
        if not isinstance(raw_data, str):
            return

        try:
            notification = realtime_notification_adapter.validate_json(raw_data)
        except ValidationError:
            logger.warning("discarded invalid realtime notification")
            return

        raw_channel = message.get("channel")
        if isinstance(raw_channel, bytes):
            raw_channel = raw_channel.decode("utf-8")
        if raw_channel != tenant_event_channel(notification.tenant_id):
            logger.warning("discarded mismatched realtime notification")
            return

        if isinstance(notification, CodeScanNotification):
            await self._manager.broadcast_code_scan(notification)
        else:
            await self._manager.broadcast(notification)
