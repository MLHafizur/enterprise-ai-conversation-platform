"""Redis publisher for content-free code scan status updates."""

from __future__ import annotations

from typing import Literal, Protocol, cast

from enterprise_chat_platform.application.process_code_scan import CodeScanTerminalEvent
from enterprise_chat_platform.infrastructure.realtime.protocol import CodeScanNotification
from enterprise_chat_platform.infrastructure.realtime.subscriber import tenant_event_channel


class RedisPublisherClient(Protocol):
    async def publish(self, channel: str, message: str) -> object: ...


class RedisCodeScanEventPublisher:
    def __init__(self, client: RedisPublisherClient) -> None:
        self._client = client

    async def publish(self, event: CodeScanTerminalEvent) -> None:
        notification = CodeScanNotification(
            tenant_id=event.tenant_id.value,
            scan_id=event.scan_id.value,
            status=cast(Literal["succeeded", "failed"], event.status),
            gate_decision=(event.gate_decision.value if event.gate_decision is not None else None),
            total_findings=event.total_findings,
            occurred_at=event.occurred_at,
        )
        await self._client.publish(
            tenant_event_channel(event.tenant_id.value),
            notification.model_dump_json(),
        )
