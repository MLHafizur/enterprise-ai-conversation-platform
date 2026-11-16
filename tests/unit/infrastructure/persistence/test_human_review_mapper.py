from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import inspect

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.human_review import (
    HumanReviewResolution,
    HumanReviewStatus,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId
from enterprise_chat_platform.infrastructure.persistence.mappers.human_review import (
    human_review_from_record,
    human_review_to_pending_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import HumanReviewRecord

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
RUN_UUID = UUID("22222222-2222-4222-8222-222222222222")
REVIEWER_UUID = UUID("33333333-3333-4333-8333-333333333333")
CREATED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


def _record(**overrides: object) -> HumanReviewRecord:
    values: dict[str, object] = {
        "tenant_id": TENANT_UUID,
        "agent_run_id": RUN_UUID,
        "policy_action": "escalate",
        "status": "pending",
        "version": 0,
        "created_at": CREATED_AT,
        "resolved_by": None,
        "resolved_at": None,
        "resolution": None,
    }
    values.update(overrides)
    return HumanReviewRecord(**values)


def test_forward_mapping_creates_only_pending_escalation_state() -> None:
    record = human_review_to_pending_record(
        tenant_id=TenantId(TENANT_UUID),
        agent_run_id=AgentRunId(RUN_UUID),
        created_at=CREATED_AT,
    )

    assert record.policy_action == "escalate"
    assert record.status == "pending"
    assert record.version == 0
    assert record.resolved_by is None
    assert record.resolved_at is None
    assert record.resolution is None
    assert inspect(record).transient is True


def test_reverse_mapping_rebuilds_pending_and_resolved_states() -> None:
    pending = human_review_from_record(_record())
    resolved = human_review_from_record(
        _record(
            status="resolved",
            version=1,
            resolved_by=REVIEWER_UUID,
            resolved_at=CREATED_AT,
            resolution="confirmed",
        )
    )

    assert pending.status is HumanReviewStatus.PENDING
    assert resolved.status is HumanReviewStatus.RESOLVED
    assert resolved.resolution is HumanReviewResolution.CONFIRMED
    assert resolved.resolved_by is not None
    assert resolved.resolved_by.value == REVIEWER_UUID


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("policy_action", "block", "escalation decision"),
        ("status", "claimed", "status"),
        ("resolution", "approved", "resolution"),
        ("version", 2, "version"),
    ],
)
def test_reverse_mapping_rejects_corrupt_workflow_state(
    field: str,
    value: object,
    message: str,
) -> None:
    values: dict[str, object] = {}
    if field == "resolution":
        values.update(
            status="resolved",
            version=1,
            resolved_by=REVIEWER_UUID,
            resolved_at=CREATED_AT,
        )
    values[field] = value

    with pytest.raises(DomainValidationError, match=message):
        human_review_from_record(_record(**values))
