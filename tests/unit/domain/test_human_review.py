from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from enterprise_chat_platform.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from enterprise_chat_platform.domain.human_review import (
    HumanReview,
    HumanReviewResolution,
    HumanReviewStatus,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId, UserId

CREATED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


def _pending() -> HumanReview:
    return HumanReview.pending(
        tenant_id=TenantId.new(),
        agent_run_id=AgentRunId.new(),
        created_at=CREATED_AT,
    )


def test_pending_review_has_exact_initial_state() -> None:
    review = _pending()

    assert review.status is HumanReviewStatus.PENDING
    assert review.version == 0
    assert review.resolved_by is None
    assert review.resolved_at is None
    assert review.resolution is None


def test_resolve_produces_one_terminal_version_without_resuming_agent() -> None:
    pending = _pending()
    reviewer_id = UserId.new()

    resolved = pending.resolve(
        reviewer_id=reviewer_id,
        resolution=HumanReviewResolution.CONFIRMED,
        at=CREATED_AT + timedelta(minutes=2),
    )

    assert resolved.status is HumanReviewStatus.RESOLVED
    assert resolved.version == 1
    assert resolved.resolved_by == reviewer_id
    assert resolved.resolution is HumanReviewResolution.CONFIRMED
    assert pending.status is HumanReviewStatus.PENDING


def test_resolution_normalizes_server_time_to_utc() -> None:
    resolved = _pending().resolve(
        reviewer_id=UserId.new(),
        resolution=HumanReviewResolution.DISMISSED,
        at=datetime(
            2026,
            11,
            16,
            15,
            5,
            tzinfo=timezone(timedelta(hours=-5)),
        ),
    )

    assert resolved.resolved_at == datetime(2026, 11, 16, 20, 5, tzinfo=UTC)
    assert resolved.resolved_at is not None
    assert resolved.resolved_at.tzinfo is UTC


def test_resolved_review_cannot_transition_again() -> None:
    resolved = _pending().resolve(
        reviewer_id=UserId.new(),
        resolution=HumanReviewResolution.CONFIRMED,
        at=CREATED_AT,
    )

    with pytest.raises(InvalidStateTransitionError, match="Only a pending"):
        resolved.resolve(
            reviewer_id=UserId.new(),
            resolution=HumanReviewResolution.DISMISSED,
            at=CREATED_AT,
        )


@pytest.mark.parametrize(
    ("status", "version"),
    [
        (HumanReviewStatus.PENDING, 1),
        (HumanReviewStatus.RESOLVED, 0),
        (HumanReviewStatus.RESOLVED, 2),
    ],
)
def test_each_state_has_one_exact_version(
    status: HumanReviewStatus,
    version: int,
) -> None:
    with pytest.raises(DomainValidationError, match="version"):
        if status is HumanReviewStatus.RESOLVED:
            HumanReview(
                tenant_id=TenantId.new(),
                agent_run_id=AgentRunId.new(),
                status=status,
                created_at=CREATED_AT,
                version=version,
                resolved_by=UserId.new(),
                resolved_at=CREATED_AT,
                resolution=HumanReviewResolution.CONFIRMED,
            )
        else:
            HumanReview(
                tenant_id=TenantId.new(),
                agent_run_id=AgentRunId.new(),
                status=status,
                created_at=CREATED_AT,
                version=version,
            )


def test_pending_and_resolved_state_shapes_are_enforced() -> None:
    with pytest.raises(DomainValidationError, match="Pending"):
        HumanReview(
            tenant_id=TenantId.new(),
            agent_run_id=AgentRunId.new(),
            status=HumanReviewStatus.PENDING,
            created_at=CREATED_AT,
            resolved_by=UserId.new(),
        )

    with pytest.raises(DomainValidationError, match="Resolved"):
        HumanReview(
            tenant_id=TenantId.new(),
            agent_run_id=AgentRunId.new(),
            status=HumanReviewStatus.RESOLVED,
            created_at=CREATED_AT,
            version=1,
        )


def test_resolution_cannot_precede_creation() -> None:
    with pytest.raises(DomainValidationError, match="cannot precede"):
        _pending().resolve(
            reviewer_id=UserId.new(),
            resolution=HumanReviewResolution.CONFIRMED,
            at=CREATED_AT - timedelta(seconds=1),
        )


def test_human_review_is_immutable() -> None:
    review = _pending()

    with pytest.raises(FrozenInstanceError):
        setattr(review, "version", 1)  # noqa: B010
