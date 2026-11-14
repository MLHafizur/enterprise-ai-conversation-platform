from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from enterprise_chat_platform.application.realtime_events import (
    AGENT_RUN_TERMINAL_EVENT_TYPE,
    AgentRunTerminalEvent,
)
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.persistence.agent_run_job_store import (
    _terminal_notification_record,
)
from enterprise_chat_platform.infrastructure.persistence.notification_relay_store import (
    ClaimedTerminalNotification,
    _pending_terminal_notification_statement,
    _terminal_event_from_record,
)

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
LEASE_TOKEN = UUID("77777777-7777-4777-8777-777777777777")


def _event() -> AgentRunTerminalEvent:
    return AgentRunTerminalEvent(
        id=EventId.parse("11111111-1111-4111-8111-111111111111"),
        tenant_id=TenantId.parse("22222222-2222-4222-8222-222222222222"),
        conversation_id=ConversationId.parse("33333333-3333-4333-8333-333333333333"),
        agent_run_id=AgentRunId.parse("44444444-4444-4444-8444-444444444444"),
        status=AgentRunStatus.SUCCEEDED,
        output_message_id=MessageId.parse("55555555-5555-4555-8555-555555555555"),
        conversation_version=9,
        message_sequence=14,
        occurred_at=NOW,
    )


def test_pending_notification_query_filters_type_and_uses_skip_locked() -> None:
    statement = _pending_terminal_notification_statement(now=NOW)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert f"outbox_events.event_type = '{AGENT_RUN_TERMINAL_EVENT_TYPE}'" in sql
    assert "outbox_events.published_at IS NULL" in sql
    assert "outbox_events.available_at <=" in sql
    assert "outbox_events.lease_token IS NULL" in sql
    assert "outbox_events.lease_expires_at <=" in sql
    assert "ORDER BY chat.outbox_events.available_at" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 1" in sql


def test_terminal_record_round_trip_preserves_strict_event() -> None:
    event = _event()
    record = _terminal_notification_record(event=event, aggregate_version=8)

    assert record.aggregate_version == 8
    assert record.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE
    assert record.attempt_count == 0
    assert record.lease_token is None
    assert record.published_at is None
    assert _terminal_event_from_record(record) == event


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_run_id", "not-a-uuid"),
        ("status", "running"),
        ("conversation_version", 0),
        ("message_sequence", 0),
    ],
)
def test_reverse_mapping_rejects_invalid_payload(field: str, value: object) -> None:
    record = _terminal_notification_record(event=_event(), aggregate_version=8)
    record.payload = {**record.payload, field: value}

    with pytest.raises((RuntimeError, ValueError)):
        _terminal_event_from_record(record)


def test_reverse_mapping_rejects_payload_extensions() -> None:
    record = _terminal_notification_record(event=_event(), aggregate_version=8)
    record.payload = {**record.payload, "content": "must never be relayed"}

    with pytest.raises(RuntimeError, match="unsupported shape"):
        _terminal_event_from_record(record)


def test_claimed_notification_requires_valid_fencing_state() -> None:
    event = _event()
    claim = ClaimedTerminalNotification(
        event=event,
        lease_token=LEASE_TOKEN,
        attempt_count=1,
    )

    assert claim.event == event

    with pytest.raises(ValueError, match="attempt count"):
        ClaimedTerminalNotification(
            event=event,
            lease_token=LEASE_TOKEN,
            attempt_count=0,
        )

    with pytest.raises(ValueError, match="non-nil"):
        ClaimedTerminalNotification(
            event=event,
            lease_token=UUID(int=0),
            attempt_count=1,
        )


def test_terminal_notification_record_rejects_invalid_logical_version() -> None:
    with pytest.raises(ValueError, match="aggregate version"):
        _terminal_notification_record(event=_event(), aggregate_version=0)


def test_claimed_notification_is_immutable() -> None:
    notification = ClaimedTerminalNotification(
        event=_event(),
        lease_token=LEASE_TOKEN,
        attempt_count=1,
    )

    with pytest.raises(AttributeError):
        setattr(notification, "attempt_count", 2)  # noqa: B010


def test_claim_query_accepts_aware_clock_values() -> None:
    statement = _pending_terminal_notification_statement(now=NOW + timedelta(seconds=1))

    assert statement is not None
