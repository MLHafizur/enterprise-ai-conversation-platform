from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from enterprise_chat_platform.application.realtime_events import (
    AGENT_RUN_TERMINAL_EVENT_TYPE,
    AGENT_RUN_TERMINAL_PAYLOAD_FIELDS,
    AGENT_RUN_TERMINAL_SCHEMA_VERSION,
    AgentRunTerminalEvent,
    agent_run_terminal_payload,
)
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)

OCCURRED_AT = datetime(2026, 11, 14, 14, 0, tzinfo=UTC)


def _succeeded_event() -> AgentRunTerminalEvent:
    return AgentRunTerminalEvent(
        id=EventId.new(),
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        agent_run_id=AgentRunId.new(),
        status=AgentRunStatus.SUCCEEDED,
        output_message_id=MessageId.new(),
        conversation_version=8,
        message_sequence=12,
        occurred_at=OCCURRED_AT,
    )


def test_terminal_event_contract_is_stable_and_contains_no_content() -> None:
    event = _succeeded_event()

    assert AGENT_RUN_TERMINAL_EVENT_TYPE == "agent_run.terminal.v1"
    assert AGENT_RUN_TERMINAL_SCHEMA_VERSION == 1
    assert agent_run_terminal_payload(event) == {
        "agent_run_id": str(event.agent_run_id),
        "status": "succeeded",
        "output_message_id": str(event.output_message_id),
        "conversation_version": 8,
        "message_sequence": 12,
    }
    assert set(agent_run_terminal_payload(event)) == AGENT_RUN_TERMINAL_PAYLOAD_FIELDS
    assert "content" not in agent_run_terminal_payload(event)


def test_terminal_event_is_immutable_and_normalizes_time_to_utc() -> None:
    event = replace(
        _succeeded_event(),
        occurred_at=datetime(
            2026,
            11,
            14,
            9,
            0,
            tzinfo=timezone(timedelta(hours=-5)),
        ),
    )

    assert event.occurred_at == OCCURRED_AT
    assert event.occurred_at.tzinfo is UTC

    with pytest.raises(FrozenInstanceError):
        setattr(event, "status", AgentRunStatus.FAILED)  # noqa: B010


@pytest.mark.parametrize("status", [AgentRunStatus.QUEUED, AgentRunStatus.RUNNING])
def test_terminal_event_rejects_nonterminal_status(status: AgentRunStatus) -> None:
    with pytest.raises(DomainValidationError, match="terminal agent-run status"):
        replace(_succeeded_event(), status=status)


@pytest.mark.parametrize("conversation_version", [0, -1, True])
def test_terminal_event_rejects_invalid_conversation_version(
    conversation_version: int,
) -> None:
    with pytest.raises(DomainValidationError, match="version must be positive"):
        replace(_succeeded_event(), conversation_version=conversation_version)


@pytest.mark.parametrize("message_sequence", [0, -1, True])
def test_terminal_event_rejects_invalid_message_sequence(message_sequence: int) -> None:
    with pytest.raises(DomainValidationError, match="sequence must be positive"):
        replace(_succeeded_event(), message_sequence=message_sequence)


@pytest.mark.parametrize("status", [AgentRunStatus.FAILED, AgentRunStatus.CANCELLED])
def test_failed_or_cancelled_event_has_nullable_output_fields(
    status: AgentRunStatus,
) -> None:
    event = replace(
        _succeeded_event(),
        status=status,
        output_message_id=None,
        message_sequence=None,
    )

    assert agent_run_terminal_payload(event)["output_message_id"] is None
    assert agent_run_terminal_payload(event)["message_sequence"] is None


def test_succeeded_event_requires_output_message_and_sequence() -> None:
    with pytest.raises(DomainValidationError, match="output message identifier"):
        replace(_succeeded_event(), output_message_id=None)

    with pytest.raises(DomainValidationError, match="output message sequence"):
        replace(_succeeded_event(), message_sequence=None)


def test_failed_event_rejects_output_message_data() -> None:
    with pytest.raises(DomainValidationError, match="cannot contain output"):
        replace(_succeeded_event(), status=AgentRunStatus.FAILED)


def test_terminal_event_rejects_naive_time() -> None:
    with pytest.raises(DomainValidationError, match="must be timezone-aware"):
        replace(_succeeded_event(), occurred_at=OCCURRED_AT.replace(tzinfo=None))
