import json
from datetime import UTC, datetime

import pytest

from enterprise_chat_platform.application.process_code_scan import CodeScanTerminalEvent
from enterprise_chat_platform.domain.code_security import GateDecision
from enterprise_chat_platform.domain.identifiers import CodeScanId, TenantId
from enterprise_chat_platform.infrastructure.realtime.code_scan_publisher import (
    RedisCodeScanEventPublisher,
)


class _Redis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> object:
        self.messages.append((channel, message))
        return 1


@pytest.mark.asyncio
async def test_code_scan_publisher_excludes_source_and_findings() -> None:
    redis = _Redis()
    publisher = RedisCodeScanEventPublisher(redis)

    await publisher.publish(
        CodeScanTerminalEvent(
            tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
            scan_id=CodeScanId.parse("22222222-2222-4222-8222-222222222222"),
            status="succeeded",
            gate_decision=GateDecision.BLOCK,
            total_findings=2,
            occurred_at=datetime(2026, 11, 16, 20, 0, tzinfo=UTC),
        )
    )

    channel, raw_message = redis.messages[0]
    payload = json.loads(raw_message)
    assert channel == "chat:tenant:11111111-1111-4111-8111-111111111111:events"
    assert payload["type"] == "code_scan.status.v1"
    assert payload["gate_decision"] == "block"
    assert "source" not in payload
    assert "findings" not in payload
