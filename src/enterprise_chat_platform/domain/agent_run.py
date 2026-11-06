from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from enterprise_chat_platform.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.domain.primitives import normalize_utc

MAX_FAILURE_CODE_LENGTH = 64
MAX_SAFE_FAILURE_MESSAGE_LENGTH = 512

_FAILURE_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_.-]*")


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentRunFailure:
    """Sanitized failure information safe for storage and clients."""

    code: str
    safe_message: str

    def __post_init__(self) -> None:
        if (
            len(self.code) > MAX_FAILURE_CODE_LENGTH
            or _FAILURE_CODE_PATTERN.fullmatch(self.code) is None
        ):
            raise DomainValidationError(
                "Agent-run failure code must use lowercase letters, "
                "numbers, dots, underscores, or hyphens"
            )

        if not self.safe_message.strip():
            raise DomainValidationError("Agent-run safe failure message cannot be blank")

        if len(self.safe_message) > MAX_SAFE_FAILURE_MESSAGE_LENGTH:
            raise DomainValidationError(
                "Agent-run safe failure message cannot exceed "
                f"{MAX_SAFE_FAILURE_MESSAGE_LENGTH} characters"
            )

        if not self.safe_message.isprintable():
            raise DomainValidationError("Agent-run safe failure message must be printable")


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRun:
    """One immutable snapshot of an agent execution attempt."""

    id: AgentRunId
    tenant_id: TenantId
    conversation_id: ConversationId
    input_message_id: MessageId
    created_at: datetime
    status: AgentRunStatus = AgentRunStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_message_id: MessageId | None = None
    failure: AgentRunFailure | None = None

    def __post_init__(self) -> None:
        created_at = normalize_utc(
            self.created_at,
            field_name="created_at",
        )
        started_at = self._normalize_optional_timestamp(
            self.started_at,
            field_name="started_at",
        )
        finished_at = self._normalize_optional_timestamp(
            self.finished_at,
            field_name="finished_at",
        )

        if started_at is not None and started_at < created_at:
            raise DomainValidationError("started_at cannot precede created_at")

        if finished_at is not None and finished_at < created_at:
            raise DomainValidationError("finished_at cannot precede created_at")

        if started_at is not None and finished_at is not None and finished_at < started_at:
            raise DomainValidationError("finished_at cannot precede started_at")

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)

        self._validate_state()

    def start(self, *, at: datetime) -> AgentRun:
        self._require_status(
            action="start",
            allowed=(AgentRunStatus.QUEUED,),
        )
        started_at = self._normalize_transition_timestamp(
            at,
            field_name="started_at",
        )

        return replace(
            self,
            status=AgentRunStatus.RUNNING,
            started_at=started_at,
        )

    def succeed(
        self,
        *,
        output_message_id: MessageId,
        at: datetime,
    ) -> AgentRun:
        self._require_status(
            action="succeed",
            allowed=(AgentRunStatus.RUNNING,),
        )
        finished_at = self._normalize_transition_timestamp(
            at,
            field_name="finished_at",
        )

        return replace(
            self,
            status=AgentRunStatus.SUCCEEDED,
            finished_at=finished_at,
            output_message_id=output_message_id,
        )

    def fail(
        self,
        *,
        failure: AgentRunFailure,
        at: datetime,
    ) -> AgentRun:
        self._require_status(
            action="fail",
            allowed=(
                AgentRunStatus.QUEUED,
                AgentRunStatus.RUNNING,
            ),
        )
        finished_at = self._normalize_transition_timestamp(
            at,
            field_name="finished_at",
        )

        return replace(
            self,
            status=AgentRunStatus.FAILED,
            finished_at=finished_at,
            failure=failure,
        )

    def cancel(self, *, at: datetime) -> AgentRun:
        self._require_status(
            action="cancel",
            allowed=(
                AgentRunStatus.QUEUED,
                AgentRunStatus.RUNNING,
            ),
        )
        finished_at = self._normalize_transition_timestamp(
            at,
            field_name="finished_at",
        )

        return replace(
            self,
            status=AgentRunStatus.CANCELLED,
            finished_at=finished_at,
        )

    @staticmethod
    def _normalize_optional_timestamp(
        value: datetime | None,
        *,
        field_name: str,
    ) -> datetime | None:
        if value is None:
            return None

        return normalize_utc(value, field_name=field_name)

    def _normalize_transition_timestamp(
        self,
        value: datetime,
        *,
        field_name: str,
    ) -> datetime:
        normalized = normalize_utc(
            value,
            field_name=field_name,
        )

        if normalized < self.created_at:
            raise DomainValidationError(f"{field_name} cannot precede created_at")

        if (
            field_name == "finished_at"
            and self.started_at is not None
            and normalized < self.started_at
        ):
            raise DomainValidationError("finished_at cannot precede started_at")

        return normalized

    def _require_status(
        self,
        *,
        action: str,
        allowed: tuple[AgentRunStatus, ...],
    ) -> None:
        if self.status not in allowed:
            raise InvalidStateTransitionError(f"Cannot {action} agent run from {self.status.value}")

    def _validate_state(self) -> None:
        if self.status is AgentRunStatus.QUEUED:
            if any(
                value is not None
                for value in (
                    self.started_at,
                    self.finished_at,
                    self.output_message_id,
                    self.failure,
                )
            ):
                raise DomainValidationError("Queued agent run cannot contain lifecycle results")
            return

        if self.status is AgentRunStatus.RUNNING:
            if self.started_at is None:
                raise DomainValidationError("Running agent run requires started_at")

            if any(
                value is not None
                for value in (
                    self.finished_at,
                    self.output_message_id,
                    self.failure,
                )
            ):
                raise DomainValidationError("Running agent run cannot contain terminal results")
            return

        if self.status is AgentRunStatus.SUCCEEDED:
            if self.started_at is None:
                raise DomainValidationError("Succeeded agent run requires started_at")

            if self.finished_at is None:
                raise DomainValidationError("Succeeded agent run requires finished_at")

            if self.output_message_id is None:
                raise DomainValidationError("Succeeded agent run requires an output message")

            if self.failure is not None:
                raise DomainValidationError("Succeeded agent run cannot contain a failure")
            return

        if self.status is AgentRunStatus.FAILED:
            if self.finished_at is None:
                raise DomainValidationError("Failed agent run requires finished_at")

            if self.failure is None:
                raise DomainValidationError("Failed agent run requires failure information")

            if self.output_message_id is not None:
                raise DomainValidationError("Failed agent run cannot contain an output message")
            return

        if self.status is AgentRunStatus.CANCELLED:
            if self.finished_at is None:
                raise DomainValidationError("Cancelled agent run requires finished_at")

            if self.output_message_id is not None or self.failure is not None:
                raise DomainValidationError("Cancelled agent run cannot contain terminal results")
            return

        raise DomainValidationError("Unsupported agent-run status")
