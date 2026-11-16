"""Durable workflow state for policy-triggered human review."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from enterprise_chat_platform.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId, UserId
from enterprise_chat_platform.domain.primitives import normalize_utc


class HumanReviewStatus(StrEnum):
    """Lifecycle state for one durable review case."""

    PENDING = "pending"
    RESOLVED = "resolved"


class HumanReviewResolution(StrEnum):
    """A review outcome that does not resume the terminal agent run."""

    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanReview:
    """One immutable snapshot of an escalation review workflow."""

    tenant_id: TenantId
    agent_run_id: AgentRunId
    status: HumanReviewStatus
    created_at: datetime
    version: int = 0
    resolved_by: UserId | None = None
    resolved_at: datetime | None = None
    resolution: HumanReviewResolution | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise DomainValidationError("Human review tenant identifier is invalid")
        if not isinstance(self.agent_run_id, AgentRunId):
            raise DomainValidationError("Human review agent-run identifier is invalid")
        if not isinstance(self.status, HumanReviewStatus):
            raise DomainValidationError("Human review status is invalid")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise DomainValidationError("Human review version cannot be negative")

        created_at = normalize_utc(
            self.created_at,
            field_name="human_review.created_at",
        )
        resolved_at = (
            normalize_utc(
                self.resolved_at,
                field_name="human_review.resolved_at",
            )
            if self.resolved_at is not None
            else None
        )
        if resolved_at is not None and resolved_at < created_at:
            raise DomainValidationError("Human review resolution cannot precede creation")

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "resolved_at", resolved_at)
        self._validate_state()

    @classmethod
    def pending(
        cls,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
        created_at: datetime,
    ) -> HumanReview:
        """Create the initial state for an escalated agent run."""
        return cls(
            tenant_id=tenant_id,
            agent_run_id=agent_run_id,
            status=HumanReviewStatus.PENDING,
            created_at=created_at,
        )

    def resolve(
        self,
        *,
        reviewer_id: UserId,
        resolution: HumanReviewResolution,
        at: datetime,
    ) -> HumanReview:
        """Resolve a pending case without restarting its terminal agent run."""
        if self.status is not HumanReviewStatus.PENDING:
            raise InvalidStateTransitionError("Only a pending human review can be resolved")
        if not isinstance(reviewer_id, UserId):
            raise DomainValidationError("Human review reviewer identifier is invalid")
        if not isinstance(resolution, HumanReviewResolution):
            raise DomainValidationError("Human review resolution is invalid")

        resolved_at = normalize_utc(at, field_name="human_review.resolved_at")
        if resolved_at < self.created_at:
            raise DomainValidationError("Human review resolution cannot precede creation")

        return replace(
            self,
            status=HumanReviewStatus.RESOLVED,
            version=self.version + 1,
            resolved_by=reviewer_id,
            resolved_at=resolved_at,
            resolution=resolution,
        )

    def _validate_state(self) -> None:
        terminal_values = (
            self.resolved_by,
            self.resolved_at,
            self.resolution,
        )
        if self.status is HumanReviewStatus.PENDING:
            if self.version != 0:
                raise DomainValidationError("Pending human review must have version zero")
            if any(value is not None for value in terminal_values):
                raise DomainValidationError(
                    "Pending human review cannot contain resolution information"
                )
            return

        if self.status is HumanReviewStatus.RESOLVED:
            if self.version != 1:
                raise DomainValidationError("Resolved human review must have version one")
            if any(value is None for value in terminal_values):
                raise DomainValidationError(
                    "Resolved human review requires complete resolution information"
                )
            return

        raise DomainValidationError("Unsupported human review status")
