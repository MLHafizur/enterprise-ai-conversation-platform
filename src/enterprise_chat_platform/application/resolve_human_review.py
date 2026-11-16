"""Application command for resolving one policy-escalation review."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.human_review import (
    HumanReview,
    HumanReviewResolution,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId, UserId
from enterprise_chat_platform.domain.primitives import normalize_utc


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolveHumanReviewCommand:
    """Trusted command values for one optimistic review resolution."""

    agent_run_id: AgentRunId
    expected_version: int
    resolution: HumanReviewResolution

    def __post_init__(self) -> None:
        if not isinstance(self.agent_run_id, AgentRunId):
            raise DomainValidationError("Human review agent-run identifier is invalid")
        if (
            isinstance(self.expected_version, bool)
            or not isinstance(self.expected_version, int)
            or self.expected_version < 0
        ):
            raise DomainValidationError("Expected human review version cannot be negative")
        if not isinstance(self.resolution, HumanReviewResolution):
            raise DomainValidationError("Human review resolution is invalid")


class HumanReviewResolutionStore(Protocol):
    """Persistence boundary for an optimistic review transition."""

    async def resolve(
        self,
        *,
        tenant_id: TenantId,
        reviewer_id: UserId,
        command: ResolveHumanReviewCommand,
        resolved_at: datetime,
    ) -> HumanReview: ...


class ResolveHumanReviewHandler:
    """Resolve a case using server-controlled identity and time."""

    def __init__(self, *, store: HumanReviewResolutionStore) -> None:
        self._store = store

    async def handle(
        self,
        *,
        tenant_id: TenantId,
        reviewer_id: UserId,
        command: ResolveHumanReviewCommand,
        resolved_at: datetime,
    ) -> HumanReview:
        return await self._store.resolve(
            tenant_id=tenant_id,
            reviewer_id=reviewer_id,
            command=command,
            resolved_at=normalize_utc(
                resolved_at,
                field_name="human_review_resolution.resolved_at",
            ),
        )
