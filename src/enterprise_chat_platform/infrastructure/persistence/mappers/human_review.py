"""Map human-review workflow state between domain and persistence forms."""

from datetime import datetime

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.governance import PolicyAction
from enterprise_chat_platform.domain.human_review import (
    HumanReview,
    HumanReviewResolution,
    HumanReviewStatus,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId, UserId
from enterprise_chat_platform.infrastructure.persistence.models import HumanReviewRecord


def human_review_to_pending_record(
    *,
    tenant_id: TenantId,
    agent_run_id: AgentRunId,
    created_at: datetime,
) -> HumanReviewRecord:
    """Create a new transient pending record from validated domain values."""
    review = HumanReview.pending(
        tenant_id=tenant_id,
        agent_run_id=agent_run_id,
        created_at=created_at,
    )
    return HumanReviewRecord(
        tenant_id=review.tenant_id.value,
        agent_run_id=review.agent_run_id.value,
        policy_action=PolicyAction.ESCALATE.value,
        status=review.status.value,
        version=review.version,
        created_at=review.created_at,
        resolved_by=None,
        resolved_at=None,
        resolution=None,
    )


def human_review_from_record(record: HumanReviewRecord) -> HumanReview:
    """Rebuild validated workflow state from one database record."""
    if record.policy_action != PolicyAction.ESCALATE.value:
        raise DomainValidationError("Human review must reference an escalation decision")

    try:
        status = HumanReviewStatus(record.status)
    except ValueError:
        raise DomainValidationError("Human review status is invalid") from None

    resolution: HumanReviewResolution | None = None
    if record.resolution is not None:
        try:
            resolution = HumanReviewResolution(record.resolution)
        except ValueError:
            raise DomainValidationError("Human review resolution is invalid") from None

    return HumanReview(
        tenant_id=TenantId(record.tenant_id),
        agent_run_id=AgentRunId(record.agent_run_id),
        status=status,
        created_at=record.created_at,
        version=record.version,
        resolved_by=UserId(record.resolved_by) if record.resolved_by is not None else None,
        resolved_at=record.resolved_at,
        resolution=resolution,
    )
