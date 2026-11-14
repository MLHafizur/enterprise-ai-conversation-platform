from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import inspect

from enterprise_chat_platform.application.accept_user_turn import (
    AGENT_RUN_QUEUED_EVENT_TYPE,
    AgentRunQueuedEvent,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.outbox_event import (
    agent_run_queued_event_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    OUTBOX_AGGREGATE_TYPE,
)

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_UUID = UUID("22222222-2222-4222-8222-222222222222")
AGENT_RUN_UUID = UUID("33333333-3333-4333-8333-333333333333")
INPUT_MESSAGE_UUID = UUID("44444444-4444-4444-8444-444444444444")
EVENT_UUID = UUID("55555555-5555-4555-8555-555555555555")
OCCURRED_AT = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)


def _event(
    *,
    conversation_version: int = 7,
    occurred_at: datetime = OCCURRED_AT,
) -> AgentRunQueuedEvent:
    return AgentRunQueuedEvent(
        id=EventId(EVENT_UUID),
        tenant_id=TenantId(TENANT_UUID),
        conversation_id=ConversationId(CONVERSATION_UUID),
        agent_run_id=AgentRunId(AGENT_RUN_UUID),
        input_message_id=MessageId(INPUT_MESSAGE_UUID),
        conversation_version=conversation_version,
        occurred_at=occurred_at,
    )


def test_queued_event_maps_to_initially_pending_outbox_record() -> None:
    record = agent_run_queued_event_to_new_record(_event())

    assert isinstance(record, OutboxEventRecord)
    assert record.tenant_id == TENANT_UUID
    assert record.id == EVENT_UUID
    assert record.aggregate_type == OUTBOX_AGGREGATE_TYPE
    assert record.aggregate_id == CONVERSATION_UUID
    assert record.aggregate_version == 7
    assert record.event_type == AGENT_RUN_QUEUED_EVENT_TYPE
    assert record.schema_version == 1
    assert record.payload == {
        "agent_run_id": str(AGENT_RUN_UUID),
        "input_message_id": str(INPUT_MESSAGE_UUID),
    }
    assert record.occurred_at == OCCURRED_AT
    assert record.available_at == OCCURRED_AT
    assert record.attempt_count == 0
    assert record.last_attempt_at is None
    assert record.lease_token is None
    assert record.lease_expires_at is None
    assert record.published_at is None


def test_queued_event_mapper_normalizes_timestamp_to_utc() -> None:
    offset_timestamp = datetime(
        2026,
        11,
        13,
        13,
        0,
        tzinfo=timezone(-timedelta(hours=5)),
    )

    record = agent_run_queued_event_to_new_record(
        _event(occurred_at=offset_timestamp),
    )

    assert record.occurred_at == OCCURRED_AT
    assert record.occurred_at.tzinfo is UTC
    assert record.available_at == OCCURRED_AT
    assert record.available_at.tzinfo is UTC


def test_queued_event_mapper_rejects_naive_timestamp() -> None:
    naive_event = _event(
        occurred_at=datetime(2026, 11, 13, 18, 0),
    )

    with pytest.raises(
        DomainValidationError,
        match="agent_run_queued_event.occurred_at must be timezone-aware",
    ):
        agent_run_queued_event_to_new_record(naive_event)


@pytest.mark.parametrize("conversation_version", [0, -1])
def test_queued_event_mapper_rejects_nonpositive_conversation_version(
    conversation_version: int,
) -> None:
    invalid_event = replace(
        _event(),
        conversation_version=conversation_version,
    )

    with pytest.raises(
        ValueError,
        match="Conversation version must be positive",
    ):
        agent_run_queued_event_to_new_record(invalid_event)


def test_queued_event_mapper_returns_unsaved_transient_record() -> None:
    record = agent_run_queued_event_to_new_record(_event())

    state = inspect(record)

    assert state.transient is True
    assert state.session is None
