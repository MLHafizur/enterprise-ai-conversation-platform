import json
from datetime import UTC, datetime

import pytest

from enterprise_chat_platform.application.realtime_events import AgentRunTerminalEvent
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.realtime.protocol import TerminalNotification
from enterprise_chat_platform.infrastructure.realtime.publisher import (
    RedisTerminalNotificationPublisher,
)
from enterprise_chat_platform.infrastructure.realtime.subscriber import tenant_event_channel


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.calls.append((channel, message))
        return 0


@pytest.mark.asyncio
async def test_publisher_sends_only_routing_and_result_metadata() -> None:
    tenant_id = TenantId.parse("11111111-1111-4111-8111-111111111111")
    event = AgentRunTerminalEvent(
        id=EventId.parse("22222222-2222-4222-8222-222222222222"),
        tenant_id=tenant_id,
        conversation_id=ConversationId.parse("33333333-3333-4333-8333-333333333333"),
        agent_run_id=AgentRunId.parse("44444444-4444-4444-8444-444444444444"),
        status=AgentRunStatus.FAILED,
        output_message_id=None,
        conversation_version=4,
        message_sequence=None,
        occurred_at=datetime(2026, 11, 14, 18, 0, tzinfo=UTC),
    )
    redis = FakeRedis()

    await RedisTerminalNotificationPublisher(redis).publish(event)

    assert len(redis.calls) == 1
    channel, raw_message = redis.calls[0]
    assert channel == tenant_event_channel(tenant_id.value)
    payload = json.loads(raw_message)
    assert set(payload) == set(TerminalNotification.model_fields)
    assert "content" not in payload
    assert TerminalNotification.model_validate_json(raw_message).status == "failed"
