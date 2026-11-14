"""Stable integration events used to notify realtime transports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final, TypedDict

from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.domain.primitives import normalize_utc

AGENT_RUN_TERMINAL_EVENT_TYPE: Final = "agent_run.terminal.v1"
AGENT_RUN_TERMINAL_SCHEMA_VERSION: Final = 1
AGENT_RUN_TERMINAL_PAYLOAD_FIELDS: Final = frozenset(
    {
        "agent_run_id",
        "status",
        "output_message_id",
        "conversation_version",
        "message_sequence",
    }
)

_TERMINAL_STATUSES: Final = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)


class AgentRunTerminalPayload(TypedDict):
    """Exact JSON object persisted in the transactional outbox."""

    agent_run_id: str
    status: str
    output_message_id: str | None
    conversation_version: int
    message_sequence: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRunTerminalEvent:
    """A durable notice that one agent run reached a terminal state."""

    id: EventId
    tenant_id: TenantId
    conversation_id: ConversationId
    agent_run_id: AgentRunId
    status: AgentRunStatus
    output_message_id: MessageId | None
    conversation_version: int
    message_sequence: int | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        self._validate_identifier(self.id, EventId, "id")
        self._validate_identifier(self.tenant_id, TenantId, "tenant_id")
        self._validate_identifier(
            self.conversation_id,
            ConversationId,
            "conversation_id",
        )
        self._validate_identifier(self.agent_run_id, AgentRunId, "agent_run_id")

        if not isinstance(self.status, AgentRunStatus) or self.status not in _TERMINAL_STATUSES:
            raise DomainValidationError("Terminal event requires a terminal agent-run status")

        if (
            not isinstance(self.conversation_version, int)
            or isinstance(self.conversation_version, bool)
            or self.conversation_version <= 0
        ):
            raise DomainValidationError("Terminal event conversation version must be positive")

        if self.message_sequence is not None and (
            not isinstance(self.message_sequence, int)
            or isinstance(self.message_sequence, bool)
            or self.message_sequence <= 0
        ):
            raise DomainValidationError("Terminal event message sequence must be positive")

        if self.status is AgentRunStatus.SUCCEEDED:
            if not isinstance(self.output_message_id, MessageId):
                raise DomainValidationError(
                    "Succeeded terminal event requires an output message identifier"
                )
            if self.message_sequence is None:
                raise DomainValidationError(
                    "Succeeded terminal event requires an output message sequence"
                )
        elif self.output_message_id is not None or self.message_sequence is not None:
            raise DomainValidationError(
                "Failed or cancelled terminal event cannot contain output message data"
            )

        if not isinstance(self.occurred_at, datetime):
            raise DomainValidationError("Terminal event occurred_at must be a datetime")

        occurred_at = normalize_utc(
            self.occurred_at,
            field_name="agent_run_terminal_event.occurred_at",
        )
        object.__setattr__(self, "occurred_at", occurred_at)

    @staticmethod
    def _validate_identifier(
        value: object,
        expected_type: type[object],
        field_name: str,
    ) -> None:
        if not isinstance(value, expected_type):
            raise DomainValidationError(
                f"Terminal event {field_name} must be a {expected_type.__name__}"
            )


def agent_run_terminal_payload(event: AgentRunTerminalEvent) -> AgentRunTerminalPayload:
    """Build the only payload shape allowed for a terminal event."""

    return AgentRunTerminalPayload(
        agent_run_id=str(event.agent_run_id),
        status=event.status.value,
        output_message_id=(
            str(event.output_message_id) if event.output_message_id is not None else None
        ),
        conversation_version=event.conversation_version,
        message_sequence=event.message_sequence,
    )
