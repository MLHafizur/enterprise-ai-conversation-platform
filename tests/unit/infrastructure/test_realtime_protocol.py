from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_chat_platform.infrastructure.realtime.protocol import (
    AuthenticateFrame,
    CodeScanNotification,
    HeartbeatFrame,
    SubscribeFrame,
    TerminalNotification,
    authenticated_client_frame_adapter,
)

CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_authentication_frame_is_strict_and_bounded() -> None:
    frame = AuthenticateFrame.model_validate(
        {"type": "authenticate", "token": "a-valid-token-value"}
    )

    assert frame.token == "a-valid-token-value"

    with pytest.raises(ValidationError):
        AuthenticateFrame.model_validate({"type": "authenticate", "token": "short"})

    with pytest.raises(ValidationError):
        AuthenticateFrame.model_validate(
            {
                "type": "authenticate",
                "token": "a-valid-token-value",
                "tenant_id": "client-controlled",
            }
        )


def test_authenticated_frame_adapter_discriminates_strict_events() -> None:
    subscribe = authenticated_client_frame_adapter.validate_json(
        '{"type":"subscribe","conversation_id":"33333333-3333-4333-8333-333333333333"}'
    )
    heartbeat = authenticated_client_frame_adapter.validate_json('{"type":"heartbeat"}')

    assert subscribe == SubscribeFrame(
        type="subscribe",
        conversation_id=CONVERSATION_ID,
    )
    assert heartbeat == HeartbeatFrame(type="heartbeat")

    with pytest.raises(ValidationError):
        authenticated_client_frame_adapter.validate_json(
            '{"type":"subscribe","conversation_id":123}'
        )

    with pytest.raises(ValidationError):
        authenticated_client_frame_adapter.validate_json(
            '{"type":"heartbeat","tenant_id":"client-controlled"}'
        )

    with pytest.raises(ValidationError):
        authenticated_client_frame_adapter.validate_json('{"type":"unknown"}')


def test_terminal_notification_websocket_payload_excludes_tenant_routing_data() -> None:
    notification = TerminalNotification(
        event_id=UUID("11111111-1111-4111-8111-111111111111"),
        tenant_id=UUID("22222222-2222-4222-8222-222222222222"),
        conversation_id=CONVERSATION_ID,
        agent_run_id=UUID("44444444-4444-4444-8444-444444444444"),
        status="succeeded",
        output_message_id=UUID("55555555-5555-4555-8555-555555555555"),
        conversation_version=7,
        message_sequence=4,
        occurred_at=datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
    )

    payload = notification.websocket_payload()

    assert payload["type"] == "agent_run.terminal.v1"
    assert payload["conversation_id"] == str(CONVERSATION_ID)
    assert "tenant_id" not in payload
    assert "content" not in payload


def test_code_scan_notification_contains_only_safe_status_metadata() -> None:
    notification = CodeScanNotification(
        tenant_id=UUID("22222222-2222-4222-8222-222222222222"),
        scan_id=UUID("66666666-6666-4666-8666-666666666666"),
        status="succeeded",
        gate_decision="block",
        total_findings=3,
        occurred_at=datetime(2026, 11, 16, 20, 0, tzinfo=UTC),
    )

    payload = notification.websocket_payload()

    assert payload["type"] == "code_scan.status.v1"
    assert payload["gate_decision"] == "block"
    assert "tenant_id" not in payload
    assert "code" not in payload
    assert "findings" not in payload
