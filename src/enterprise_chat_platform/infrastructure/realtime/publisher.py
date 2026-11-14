"""Redis publisher for content-free terminal notifications."""

from __future__ import annotations

from typing import Literal, Protocol, cast

from enterprise_chat_platform.application.realtime_events import (
    AgentRunTerminalEvent,
)
from enterprise_chat_platform.infrastructure.realtime.protocol import TerminalNotification
from enterprise_chat_platform.infrastructure.realtime.subscriber import tenant_event_channel


class RedisPublisherClient(Protocol):
    async def publish(self, channel: str, message: str) -> object: ...


class RedisTerminalNotificationPublisher:
    """Publish routing metadata only; message content remains in PostgreSQL."""

    def __init__(self, client: RedisPublisherClient) -> None:
        self._client = client

    async def publish(self, event: AgentRunTerminalEvent) -> None:
        notification = TerminalNotification(
            event_id=event.id.value,
            tenant_id=event.tenant_id.value,
            conversation_id=event.conversation_id.value,
            agent_run_id=event.agent_run_id.value,
            status=cast(
                Literal["succeeded", "failed", "cancelled"],
                event.status.value,
            ),
            output_message_id=(
                event.output_message_id.value if event.output_message_id is not None else None
            ),
            conversation_version=event.conversation_version,
            message_sequence=event.message_sequence,
            occurred_at=event.occurred_at,
        )
        await self._client.publish(
            tenant_event_channel(event.tenant_id.value),
            notification.model_dump_json(),
        )
