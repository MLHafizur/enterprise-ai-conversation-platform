from dataclasses import FrozenInstanceError

import pytest

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.governance import (
    MAX_POLICY_FINDINGS,
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
)


def _finding(
    *,
    code: str = "prompt.instruction_override",
    severity: PolicySeverity = PolicySeverity.HIGH,
) -> PolicyFinding:
    return PolicyFinding(
        code=code,
        severity=severity,
        safe_message="The request conflicts with conversation policy",
    )


def test_allow_decision_permits_model_call() -> None:
    assessment = PolicyAssessment(
        action=PolicyAction.ALLOW,
        risk_score=0.05,
        policy_version="conversation-input.v1",
    )

    assert assessment.allows_model_call is True
    assert assessment.requires_human_review is False
    assert assessment.findings == ()


def test_block_decision_prevents_model_call() -> None:
    finding = _finding()

    assessment = PolicyAssessment(
        action=PolicyAction.BLOCK,
        risk_score=0.95,
        policy_version="conversation-input.v1",
        findings=(finding,),
    )

    assert assessment.allows_model_call is False
    assert assessment.requires_human_review is False
    assert assessment.findings == (finding,)


def test_escalation_requires_human_review() -> None:
    assessment = PolicyAssessment(
        action=PolicyAction.ESCALATE,
        risk_score=0.75,
        policy_version="conversation-input.v1",
        findings=(
            _finding(
                code="conversation.legal_review",
                severity=PolicySeverity.HIGH,
            ),
        ),
    )

    assert assessment.allows_model_call is False
    assert assessment.requires_human_review is True


@pytest.mark.parametrize(
    "action",
    [
        PolicyAction.BLOCK,
        PolicyAction.ESCALATE,
    ],
)
def test_non_allow_decision_requires_a_finding(action: PolicyAction) -> None:
    with pytest.raises(
        DomainValidationError,
        match="require at least one finding",
    ):
        PolicyAssessment(
            action=action,
            risk_score=0.8,
            policy_version="conversation-input.v1",
        )


@pytest.mark.parametrize(
    "risk_score",
    [
        -0.01,
        1.01,
        float("nan"),
        float("inf"),
    ],
)
def test_risk_score_must_be_finite_and_bounded(risk_score: float) -> None:
    with pytest.raises(
        DomainValidationError,
        match="between zero and one",
    ):
        PolicyAssessment(
            action=PolicyAction.ALLOW,
            risk_score=risk_score,
            policy_version="conversation-input.v1",
        )


def test_high_severity_finding_cannot_be_allowed() -> None:
    with pytest.raises(
        DomainValidationError,
        match="cannot produce an allow decision",
    ):
        PolicyAssessment(
            action=PolicyAction.ALLOW,
            risk_score=0.9,
            policy_version="conversation-input.v1",
            findings=(_finding(),),
        )


def test_duplicate_finding_codes_are_rejected() -> None:
    finding = _finding()

    with pytest.raises(
        DomainValidationError,
        match="must be unique",
    ):
        PolicyAssessment(
            action=PolicyAction.BLOCK,
            risk_score=0.9,
            policy_version="conversation-input.v1",
            findings=(finding, finding),
        )


@pytest.mark.parametrize(
    ("code", "safe_message"),
    [
        ("", "Safe message"),
        ("INVALID CODE", "Safe message"),
        ("valid.code", ""),
        ("valid.code", "unsafe\nmessage"),
    ],
)
def test_finding_rejects_unsafe_values(
    code: str,
    safe_message: str,
) -> None:
    with pytest.raises(DomainValidationError):
        PolicyFinding(
            code=code,
            severity=PolicySeverity.HIGH,
            safe_message=safe_message,
        )


def test_assessment_is_immutable() -> None:
    assessment = PolicyAssessment(
        action=PolicyAction.ALLOW,
        risk_score=0.05,
        policy_version="conversation-input.v1",
    )

    with pytest.raises(FrozenInstanceError):
        setattr(assessment, "risk_score", 1.0)  # noqa: B010


def test_assessment_rejects_too_many_findings() -> None:
    findings = tuple(
        PolicyFinding(
            code=f"policy.finding_{index}",
            severity=PolicySeverity.LOW,
            safe_message="A bounded policy finding",
        )
        for index in range(MAX_POLICY_FINDINGS + 1)
    )

    with pytest.raises(
        DomainValidationError,
        match="cannot exceed",
    ):
        PolicyAssessment(
            action=PolicyAction.ALLOW,
            risk_score=0.1,
            policy_version="conversation-input.v1",
            findings=findings,
        )
