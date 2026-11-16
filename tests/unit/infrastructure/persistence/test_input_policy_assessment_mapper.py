from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import inspect

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId
from enterprise_chat_platform.infrastructure.persistence.mappers.input_policy_assessment import (
    input_policy_assessment_from_record,
    input_policy_assessment_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
)

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
AGENT_RUN_UUID = UUID("22222222-2222-4222-8222-222222222222")
ASSESSED_AT = datetime(2026, 11, 16, 16, 0, tzinfo=UTC)
OFFSET_ASSESSED_AT = datetime(
    2026,
    11,
    16,
    11,
    0,
    tzinfo=timezone(timedelta(hours=-5)),
)


def _finding() -> PolicyFinding:
    return PolicyFinding(
        code="prompt.instruction_override",
        severity=PolicySeverity.HIGH,
        safe_message="The request conflicts with conversation policy",
    )


def _assessment() -> PolicyAssessment:
    return PolicyAssessment(
        action=PolicyAction.BLOCK,
        risk_score=0.95,
        policy_version="conversation-input.v1",
        findings=(_finding(),),
    )


def _record(
    *,
    action: str = "block",
    risk_score: object = 0.95,
    policy_version: str = "conversation-input.v1",
    findings: object = None,
    assessed_at: datetime = ASSESSED_AT,
) -> InputPolicyAssessmentRecord:
    stored_findings = (
        [
            {
                "code": "prompt.instruction_override",
                "severity": "high",
                "safe_message": "The request conflicts with conversation policy",
            }
        ]
        if findings is None
        else findings
    )
    return InputPolicyAssessmentRecord(
        tenant_id=TENANT_UUID,
        agent_run_id=AGENT_RUN_UUID,
        policy_version=policy_version,
        action=action,
        risk_score=risk_score,
        findings=stored_findings,
        assessed_at=assessed_at,
    )


def test_assessment_maps_to_allow_listed_transient_record() -> None:
    record = input_policy_assessment_to_new_record(
        tenant_id=TenantId(TENANT_UUID),
        agent_run_id=AgentRunId(AGENT_RUN_UUID),
        assessment=_assessment(),
        assessed_at=ASSESSED_AT,
    )

    assert record.tenant_id == TENANT_UUID
    assert record.agent_run_id == AGENT_RUN_UUID
    assert record.policy_version == "conversation-input.v1"
    assert record.action == "block"
    assert record.risk_score == 0.95
    assert record.findings == [
        {
            "code": "prompt.instruction_override",
            "severity": "high",
            "safe_message": "The request conflicts with conversation policy",
        }
    ]
    assert set(record.findings[0]) == {"code", "severity", "safe_message"}
    assert record.assessed_at == ASSESSED_AT
    assert inspect(record).transient is True


def test_round_trip_rebuilds_validated_domain_assessment() -> None:
    original = _assessment()
    record = input_policy_assessment_to_new_record(
        tenant_id=TenantId(TENANT_UUID),
        agent_run_id=AgentRunId(AGENT_RUN_UUID),
        assessment=original,
        assessed_at=ASSESSED_AT,
    )

    assert input_policy_assessment_from_record(record) == original


def test_forward_mapping_normalizes_assessed_at_to_utc() -> None:
    record = input_policy_assessment_to_new_record(
        tenant_id=TenantId(TENANT_UUID),
        agent_run_id=AgentRunId(AGENT_RUN_UUID),
        assessment=_assessment(),
        assessed_at=OFFSET_ASSESSED_AT,
    )

    assert record.assessed_at == ASSESSED_AT
    assert record.assessed_at.tzinfo is UTC


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_mapping_rejects_naive_assessed_at(direction: str) -> None:
    naive = datetime(2026, 11, 16, 16, 0)

    with pytest.raises(
        DomainValidationError,
        match="input_policy_assessment.assessed_at must be timezone-aware",
    ):
        if direction == "forward":
            input_policy_assessment_to_new_record(
                tenant_id=TenantId(TENANT_UUID),
                agent_run_id=AgentRunId(AGENT_RUN_UUID),
                assessment=_assessment(),
                assessed_at=naive,
            )
        else:
            input_policy_assessment_from_record(_record(assessed_at=naive))


@pytest.mark.parametrize(
    "findings",
    [
        {},
        ["not-an-object"],
        [{"code": "valid.code", "severity": "high"}],
        [
            {
                "code": "valid.code",
                "severity": "high",
                "safe_message": "Safe",
                "raw_content": "must never be accepted",
            }
        ],
    ],
    ids=["not-array", "not-object", "missing-field", "unknown-field"],
)
def test_reverse_mapping_rejects_malformed_or_expansive_findings(
    findings: object,
) -> None:
    with pytest.raises(DomainValidationError):
        input_policy_assessment_from_record(_record(findings=findings))


@pytest.mark.parametrize(
    "findings",
    [
        [{"code": "valid.code", "severity": "unknown", "safe_message": "Safe"}],
        [{"code": "INVALID CODE", "severity": "high", "safe_message": "Safe"}],
        [{"code": "valid.code", "severity": "high", "safe_message": "unsafe\nmessage"}],
        [
            {"code": "same.code", "severity": "high", "safe_message": "Safe"},
            {"code": "same.code", "severity": "low", "safe_message": "Also safe"},
        ],
    ],
    ids=["unknown-severity", "unsafe-code", "unsafe-message", "duplicate-code"],
)
def test_reverse_mapping_rejects_unsafe_finding_values(findings: object) -> None:
    with pytest.raises(DomainValidationError):
        input_policy_assessment_from_record(_record(findings=findings))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", "review"),
        ("risk_score", float("nan")),
        ("risk_score", 1.01),
        ("risk_score", True),
        ("policy_version", "INVALID VERSION"),
    ],
)
def test_reverse_mapping_rejects_invalid_assessment_values(
    field: str,
    value: object,
) -> None:
    record = _record()
    setattr(record, field, value)

    with pytest.raises(DomainValidationError):
        input_policy_assessment_from_record(record)


def test_reverse_mapping_rejects_non_allow_without_findings() -> None:
    with pytest.raises(DomainValidationError, match="require at least one finding"):
        input_policy_assessment_from_record(_record(findings=[]))


def test_forward_mapping_revalidates_corrupted_domain_object() -> None:
    assessment = replace(_assessment(), risk_score=0.5)
    object.__setattr__(assessment, "risk_score", float("nan"))

    with pytest.raises(DomainValidationError, match="between zero and one"):
        input_policy_assessment_to_new_record(
            tenant_id=TenantId(TENANT_UUID),
            agent_run_id=AgentRunId(AGENT_RUN_UUID),
            assessment=assessment,
            assessed_at=ASSESSED_AT,
        )
