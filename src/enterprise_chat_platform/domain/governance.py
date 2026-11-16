"""Conversation-governance value objects."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from enterprise_chat_platform.domain.errors import DomainValidationError

MAX_POLICY_CODE_LENGTH = 64
MAX_POLICY_VERSION_LENGTH = 64
MAX_SAFE_FINDING_MESSAGE_LENGTH = 256
MAX_POLICY_FINDINGS = 32

_POLICY_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_POLICY_VERSION_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class PolicyAction(StrEnum):
    """Authoritative action selected by a policy evaluation."""

    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class PolicySeverity(StrEnum):
    """Business severity of one policy finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyFinding:
    """One safe, machine-readable reason produced by policy evaluation."""

    code: str
    severity: PolicySeverity
    safe_message: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.code, str)
            or len(self.code) > MAX_POLICY_CODE_LENGTH
            or _POLICY_CODE_PATTERN.fullmatch(self.code) is None
        ):
            raise DomainValidationError(
                "Policy finding code must use lowercase letters, numbers, "
                "dots, underscores, or hyphens"
            )

        if not isinstance(self.severity, PolicySeverity):
            raise DomainValidationError("Policy finding severity is invalid")

        if not isinstance(self.safe_message, str) or not self.safe_message.strip():
            raise DomainValidationError("Policy finding safe message cannot be blank")

        if self.safe_message != self.safe_message.strip():
            raise DomainValidationError(
                "Policy finding safe message cannot have surrounding whitespace"
            )

        if len(self.safe_message) > MAX_SAFE_FINDING_MESSAGE_LENGTH:
            raise DomainValidationError(
                "Policy finding safe message cannot exceed "
                f"{MAX_SAFE_FINDING_MESSAGE_LENGTH} characters"
            )

        if not self.safe_message.isprintable():
            raise DomainValidationError("Policy finding safe message must be printable")


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyAssessment:
    """Immutable outcome of evaluating content against one policy version."""

    action: PolicyAction
    risk_score: float
    policy_version: str
    findings: tuple[PolicyFinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.action, PolicyAction):
            raise DomainValidationError("Policy action is invalid")

        if isinstance(self.risk_score, bool) or not isinstance(
            self.risk_score,
            (int, float),
        ):
            raise DomainValidationError("Policy risk score must be numeric")

        normalized_risk = float(self.risk_score)
        if not math.isfinite(normalized_risk) or not 0 <= normalized_risk <= 1:
            raise DomainValidationError("Policy risk score must be between zero and one")

        if (
            not isinstance(self.policy_version, str)
            or len(self.policy_version) > MAX_POLICY_VERSION_LENGTH
            or _POLICY_VERSION_PATTERN.fullmatch(self.policy_version) is None
        ):
            raise DomainValidationError("Policy version is invalid")

        try:
            normalized_findings = tuple(self.findings)
        except TypeError:
            raise DomainValidationError("Policy findings are invalid") from None
        if len(normalized_findings) > MAX_POLICY_FINDINGS:
            raise DomainValidationError(
                f"Policy assessment cannot exceed {MAX_POLICY_FINDINGS} findings"
            )
        if any(not isinstance(finding, PolicyFinding) for finding in normalized_findings):
            raise DomainValidationError("Policy findings are invalid")

        finding_codes = [finding.code for finding in normalized_findings]
        if len(finding_codes) != len(set(finding_codes)):
            raise DomainValidationError("Policy finding codes must be unique")

        if self.action is not PolicyAction.ALLOW and not normalized_findings:
            raise DomainValidationError(
                "Blocking and escalation decisions require at least one finding"
            )

        if self.action is PolicyAction.ALLOW and any(
            finding.severity in {PolicySeverity.HIGH, PolicySeverity.CRITICAL}
            for finding in normalized_findings
        ):
            raise DomainValidationError(
                "High or critical findings cannot produce an allow decision"
            )

        object.__setattr__(self, "risk_score", normalized_risk)
        object.__setattr__(self, "findings", normalized_findings)

    @property
    def allows_model_call(self) -> bool:
        return self.action is PolicyAction.ALLOW

    @property
    def requires_human_review(self) -> bool:
        return self.action is PolicyAction.ESCALATE
