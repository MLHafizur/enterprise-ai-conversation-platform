"""Versioned enterprise conversation input policy."""

from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyFinding,
    PolicySeverity,
)
from enterprise_chat_platform.domain.input_policy import (
    InputPolicyEngine,
    InputPolicyRule,
    RuleBasedInputPolicyEngine,
)

CONVERSATION_INPUT_POLICY_VERSION = "conversation-input.v1"


def _rule(
    *,
    code: str,
    severity: PolicySeverity,
    safe_message: str,
    action: PolicyAction,
    risk_score: float,
    phrases: tuple[str, ...],
) -> InputPolicyRule:
    return InputPolicyRule(
        finding=PolicyFinding(
            code=code,
            severity=severity,
            safe_message=safe_message,
        ),
        action=action,
        risk_score=risk_score,
        phrases=phrases,
    )


CONVERSATION_INPUT_POLICY_RULES = (
    _rule(
        code="prompt.instruction_override",
        severity=PolicySeverity.CRITICAL,
        safe_message="The request attempts to override protected instructions",
        action=PolicyAction.BLOCK,
        risk_score=0.98,
        phrases=(
            "ignore previous instructions",
            "ignore all previous instructions",
            "disregard previous instructions",
            "forget previous instructions",
            "override your instructions",
        ),
    ),
    _rule(
        code="security.credential_extraction",
        severity=PolicySeverity.CRITICAL,
        safe_message="The request asks for protected authentication material",
        action=PolicyAction.BLOCK,
        risk_score=0.99,
        phrases=(
            "share your password",
            "give me your password",
            "provide your credentials",
            "reveal your api key",
            "show me your api key",
            "give me your access token",
        ),
    ),
    _rule(
        code="security.privilege_escalation",
        severity=PolicySeverity.CRITICAL,
        safe_message="The request asks for unauthorized elevated access",
        action=PolicyAction.BLOCK,
        risk_score=1.0,
        phrases=(
            "give me admin access",
            "grant me admin access",
            "give me root access",
            "bypass access controls",
            "disable authentication",
        ),
    ),
    _rule(
        code="privacy.sensitive_data_request",
        severity=PolicySeverity.HIGH,
        safe_message="The request asks for protected personal data",
        action=PolicyAction.BLOCK,
        risk_score=0.95,
        phrases=(
            "show me customer social security numbers",
            "provide customer credit card numbers",
            "export customer passwords",
            "show me private customer data",
        ),
    ),
    _rule(
        code="conversation.legal_review",
        severity=PolicySeverity.HIGH,
        safe_message="A qualified reviewer must handle this legal request",
        action=PolicyAction.ESCALATE,
        risk_score=0.75,
        phrases=(
            "give me legal advice",
            "provide legal advice",
            "interpret this regulation for me",
            "make a legal determination",
        ),
    ),
    _rule(
        code="conversation.financial_review",
        severity=PolicySeverity.HIGH,
        safe_message="A qualified reviewer must handle this financial request",
        action=PolicyAction.ESCALATE,
        risk_score=0.75,
        phrases=(
            "tell me which stock to buy",
            "guarantee this investment",
            "make an investment decision for me",
        ),
    ),
    _rule(
        code="conversation.human_requested",
        severity=PolicySeverity.MEDIUM,
        safe_message="The user requested assistance from a human operator",
        action=PolicyAction.ESCALATE,
        risk_score=0.5,
        phrases=(
            "speak to a human",
            "talk to a human",
            "connect me to a human agent",
            "escalate this to a human",
            "I want a human representative",
        ),
    ),
)


def build_conversation_input_policy() -> InputPolicyEngine:
    """Build the server-controlled policy used for user messages."""
    return RuleBasedInputPolicyEngine(
        policy_version=CONVERSATION_INPUT_POLICY_VERSION,
        rules=CONVERSATION_INPUT_POLICY_RULES,
    )
