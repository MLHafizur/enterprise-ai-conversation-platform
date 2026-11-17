from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from fastapi import WebSocket

from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.tenant_catalog import DEMO_TENANTS
from enterprise_chat_platform.domain.identifiers import UserId
from enterprise_chat_platform.infrastructure.realtime.connection_manager import (
    RealtimeConnectionManager,
)
from enterprise_chat_platform.infrastructure.realtime.protocol import (
    CodeScanNotification,
    TerminalNotification,
)

CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")


class _FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.sent: list[dict[str, object]] = []
        self.close_codes: list[int] = []

    async def send_json(self, data: dict[str, object]) -> None:
        if self.fail_send:
            raise RuntimeError("socket send failed")
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


def _actor(*, tenant_index: int, user: str) -> ActorContext:
    return ActorContext(
        tenant_id=DEMO_TENANTS[tenant_index].id,
        actor_id=UserId.parse(user),
    )


def _notification() -> TerminalNotification:
    return TerminalNotification(
        event_id=UUID("11111111-1111-4111-8111-111111111111"),
        tenant_id=DEMO_TENANTS[0].id.value,
        conversation_id=CONVERSATION_ID,
        agent_run_id=UUID("44444444-4444-4444-8444-444444444444"),
        status="failed",
        output_message_id=None,
        conversation_version=7,
        message_sequence=None,
        occurred_at=datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_broadcast_is_scoped_to_tenant_and_subscription() -> None:
    manager = RealtimeConnectionManager()
    recipient = _FakeWebSocket()
    unsubscribed = _FakeWebSocket()
    other_tenant = _FakeWebSocket()
    actor_one = _actor(
        tenant_index=0,
        user="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    actor_two = _actor(
        tenant_index=1,
        user="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )

    await manager.register(
        connection_id="recipient",
        websocket=cast(WebSocket, recipient),
        actor=actor_one,
        tenant=DEMO_TENANTS[0],
    )
    await manager.register(
        connection_id="unsubscribed",
        websocket=cast(WebSocket, unsubscribed),
        actor=actor_one,
        tenant=DEMO_TENANTS[0],
    )
    await manager.register(
        connection_id="other-tenant",
        websocket=cast(WebSocket, other_tenant),
        actor=actor_two,
        tenant=DEMO_TENANTS[1],
    )
    await manager.subscribe(
        connection_id="recipient",
        conversation_id=CONVERSATION_ID,
    )
    await manager.subscribe(
        connection_id="other-tenant",
        conversation_id=CONVERSATION_ID,
    )

    await manager.broadcast(_notification())

    assert len(recipient.sent) == 1
    assert recipient.sent[0]["type"] == "agent_run.terminal.v1"
    assert "tenant_id" not in recipient.sent[0]
    assert unsubscribed.sent == []
    assert other_tenant.sent == []


@pytest.mark.asyncio
async def test_failed_send_removes_and_closes_connection() -> None:
    manager = RealtimeConnectionManager(send_timeout_seconds=1)
    websocket = _FakeWebSocket(fail_send=True)

    await manager.register(
        connection_id="failing",
        websocket=cast(WebSocket, websocket),
        actor=_actor(
            tenant_index=0,
            user="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        ),
        tenant=DEMO_TENANTS[0],
    )

    first_result = await manager.send(
        connection_id="failing",
        payload={"type": "heartbeat_ack"},
    )
    second_result = await manager.send(
        connection_id="failing",
        payload={"type": "heartbeat_ack"},
    )

    assert first_result is False
    assert second_result is False
    assert websocket.close_codes == [1012]


@pytest.mark.asyncio
async def test_code_scan_broadcast_requires_agent_or_admin_role() -> None:
    manager = RealtimeConnectionManager()
    agent_socket = _FakeWebSocket()
    user_socket = _FakeWebSocket()
    actor = _actor(
        tenant_index=0,
        user="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    await manager.register(
        connection_id="agent",
        websocket=cast(WebSocket, agent_socket),
        actor=actor,
        tenant=DEMO_TENANTS[0],
        roles=frozenset({"agent"}),
    )
    await manager.register(
        connection_id="user",
        websocket=cast(WebSocket, user_socket),
        actor=actor,
        tenant=DEMO_TENANTS[0],
        roles=frozenset({"user"}),
    )

    await manager.broadcast_code_scan(
        CodeScanNotification(
            tenant_id=DEMO_TENANTS[0].id.value,
            scan_id=UUID("66666666-6666-4666-8666-666666666666"),
            status="succeeded",
            gate_decision="review_required",
            total_findings=1,
            occurred_at=datetime(2026, 11, 16, 20, 0, tzinfo=UTC),
        )
    )

    assert agent_socket.sent[0]["type"] == "code_scan.status.v1"
    assert user_socket.sent == []


@pytest.mark.asyncio
async def test_duplicate_registration_is_rejected_and_close_all_is_idempotent() -> None:
    manager = RealtimeConnectionManager()
    websocket = _FakeWebSocket()
    actor = _actor(
        tenant_index=0,
        user="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )

    await manager.register(
        connection_id="duplicate",
        websocket=cast(WebSocket, websocket),
        actor=actor,
        tenant=DEMO_TENANTS[0],
    )
    with pytest.raises(RuntimeError, match="Duplicate server connection"):
        await manager.register(
            connection_id="duplicate",
            websocket=cast(WebSocket, _FakeWebSocket()),
            actor=actor,
            tenant=DEMO_TENANTS[0],
        )

    await manager.close_all()
    await manager.close_all()

    assert websocket.close_codes == [1012]
