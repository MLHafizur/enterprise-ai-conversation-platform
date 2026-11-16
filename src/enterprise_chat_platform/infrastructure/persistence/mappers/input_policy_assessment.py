"""Map input-policy assessments between domain and persistence forms."""

from __future__ import annotations

from datetime import datetime
from typing import Final

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
)

_FINDING_FIELDS: Final = frozenset({"code", "severity", "safe_message"})


def _validated_assessment(assessment: PolicyAssessment) -> PolicyAssessment:
    """Rebuild an assessment so corrupted in-memory objects cannot cross the boundary."""
    if not isinstance(assessment, PolicyAssessment):
        raise DomainValidationError("Input policy assessment is invalid")

    return PolicyAssessment(
        action=assessment.action,
        risk_score=assessment.risk_score,
        policy_version=assessment.policy_version,
        findings=assessment.findings,
    )


def _serialize_findings(
    findings: tuple[PolicyFinding, ...],
) -> list[dict[str, str]]:
    """Produce the small, allow-listed JSON representation stored in PostgreSQL."""
    return [
        {
            "code": finding.code,
            "severity": finding.severity.value,
            "safe_message": finding.safe_message,
        }
        for finding in findings
    ]


def _finding_from_json(value: object, *, index: int) -> PolicyFinding:
    if not isinstance(value, dict) or set(value) != _FINDING_FIELDS:
        raise DomainValidationError(
            f"Input policy finding at index {index} must contain exactly "
            "code, severity, and safe_message"
        )

    code = value["code"]
    severity_value = value["severity"]
    safe_message = value["safe_message"]

    if not isinstance(code, str) or not isinstance(safe_message, str):
        raise DomainValidationError(
            f"Input policy finding at index {index} contains invalid values"
        )

    if not isinstance(severity_value, str):
        raise DomainValidationError(
            f"Input policy finding at index {index} has an invalid severity"
        )

    try:
        severity = PolicySeverity(severity_value)
    except ValueError:
        raise DomainValidationError(
            f"Input policy finding at index {index} has an invalid severity"
        ) from None

    return PolicyFinding(
        code=code,
        severity=severity,
        safe_message=safe_message,
    )


def _findings_from_json(value: object) -> tuple[PolicyFinding, ...]:
    if not isinstance(value, list):
        raise DomainValidationError("Input policy findings must be a JSON array")

    return tuple(_finding_from_json(item, index=index) for index, item in enumerate(value))


def input_policy_assessment_to_new_record(
    *,
    tenant_id: TenantId,
    agent_run_id: AgentRunId,
    assessment: PolicyAssessment,
    assessed_at: datetime,
) -> InputPolicyAssessmentRecord:
    """Create an immutable transient record from one validated policy decision."""
    validated_tenant_id = TenantId(tenant_id.value)
    validated_agent_run_id = AgentRunId(agent_run_id.value)
    validated_assessment = _validated_assessment(assessment)
    normalized_assessed_at = normalize_utc(
        assessed_at,
        field_name="input_policy_assessment.assessed_at",
    )

    return InputPolicyAssessmentRecord(
        tenant_id=validated_tenant_id.value,
        agent_run_id=validated_agent_run_id.value,
        policy_version=validated_assessment.policy_version,
        action=validated_assessment.action.value,
        risk_score=validated_assessment.risk_score,
        findings=_serialize_findings(validated_assessment.findings),
        assessed_at=normalized_assessed_at,
    )


def input_policy_assessment_from_record(
    record: InputPolicyAssessmentRecord,
) -> PolicyAssessment:
    """Rebuild a validated policy decision from an immutable database record."""
    # Validate scope fields even though the returned domain value does not carry them.
    # Repository callers already know the requested scope, while these checks ensure
    # malformed database rows never become trusted policy decisions.
    TenantId(record.tenant_id)
    AgentRunId(record.agent_run_id)
    normalize_utc(
        record.assessed_at,
        field_name="input_policy_assessment.assessed_at",
    )

    try:
        action = PolicyAction(record.action)
    except ValueError:
        raise DomainValidationError("Input policy assessment action is invalid") from None

    return PolicyAssessment(
        action=action,
        risk_score=record.risk_score,
        policy_version=record.policy_version,
        findings=_findings_from_json(record.findings),
    )
