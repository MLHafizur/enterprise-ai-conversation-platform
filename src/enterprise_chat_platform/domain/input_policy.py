"""Deterministic, versioned input-policy evaluation."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
)

MAX_INPUT_POLICY_RULES = 32
MAX_RULE_PHRASES = 32
MAX_POLICY_PHRASE_LENGTH = 256

_ACTION_PRECEDENCE = {
    PolicyAction.ALLOW: 0,
    PolicyAction.ESCALATE: 1,
    PolicyAction.BLOCK: 2,
}


def _normalize_text(value: str) -> str:
    """Produce stable text for case-insensitive phrase matching."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_format_controls = "".join(
        " " if unicodedata.category(character) == "Cf" else character for character in normalized
    )
    return " ".join(without_format_controls.split())


@dataclass(frozen=True, slots=True, kw_only=True)
class InputPolicyRule:
    """One immutable rule in a versioned input-policy pack."""

    finding: PolicyFinding
    action: PolicyAction
    risk_score: float
    phrases: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.finding, PolicyFinding):
            raise DomainValidationError("Input policy finding is invalid")

        if not isinstance(self.action, PolicyAction):
            raise DomainValidationError("Input policy action is invalid")

        if isinstance(self.risk_score, bool) or not isinstance(
            self.risk_score,
            (int, float),
        ):
            raise DomainValidationError("Input policy risk score must be numeric")

        normalized_risk = float(self.risk_score)
        if not math.isfinite(normalized_risk) or not 0 <= normalized_risk <= 1:
            raise DomainValidationError("Input policy risk score must be between zero and one")

        if not isinstance(self.phrases, tuple) or not self.phrases:
            raise DomainValidationError("Input policy rule requires at least one phrase")

        if len(self.phrases) > MAX_RULE_PHRASES:
            raise DomainValidationError(
                f"Input policy rule cannot exceed {MAX_RULE_PHRASES} phrases"
            )

        normalized_phrases: list[str] = []

        for phrase in self.phrases:
            if not isinstance(phrase, str) or not phrase.strip():
                raise DomainValidationError("Input policy phrases cannot be blank")

            if not phrase.isprintable():
                raise DomainValidationError("Input policy phrases must be printable")

            normalized_phrase = _normalize_text(phrase)

            if len(normalized_phrase) > MAX_POLICY_PHRASE_LENGTH:
                raise DomainValidationError(
                    f"Input policy phrase cannot exceed {MAX_POLICY_PHRASE_LENGTH} characters"
                )

            normalized_phrases.append(normalized_phrase)

        if len(normalized_phrases) != len(set(normalized_phrases)):
            raise DomainValidationError("Input policy phrases must be unique within a rule")

        if self.action is PolicyAction.ALLOW and self.finding.severity in {
            PolicySeverity.HIGH,
            PolicySeverity.CRITICAL,
        }:
            raise DomainValidationError("High or critical findings cannot produce an allow rule")

        object.__setattr__(self, "risk_score", normalized_risk)
        object.__setattr__(
            self,
            "phrases",
            tuple(sorted(normalized_phrases)),
        )


class InputPolicyEngine(Protocol):
    """Application-facing contract for input-policy evaluation."""

    @property
    def policy_version(self) -> str: ...

    def evaluate(self, *, content: str) -> PolicyAssessment: ...


class RuleBasedInputPolicyEngine:
    """Evaluate input using deterministic, order-independent phrase rules."""

    def __init__(
        self,
        *,
        policy_version: str,
        rules: tuple[InputPolicyRule, ...],
    ) -> None:
        validated_version = PolicyAssessment(
            action=PolicyAction.ALLOW,
            risk_score=0,
            policy_version=policy_version,
        ).policy_version

        if not isinstance(rules, tuple) or not rules:
            raise DomainValidationError("Input policy engine requires at least one rule")

        if len(rules) > MAX_INPUT_POLICY_RULES:
            raise DomainValidationError(
                f"Input policy engine cannot exceed {MAX_INPUT_POLICY_RULES} rules"
            )

        if any(not isinstance(rule, InputPolicyRule) for rule in rules):
            raise DomainValidationError("Input policy rules are invalid")

        finding_codes = [rule.finding.code for rule in rules]
        if len(finding_codes) != len(set(finding_codes)):
            raise DomainValidationError("Input policy rule finding codes must be unique")

        phrases = [phrase for rule in rules for phrase in rule.phrases]
        if len(phrases) != len(set(phrases)):
            raise DomainValidationError("Input policy phrases must be unique across rules")

        self._policy_version = validated_version
        self._rules = tuple(
            sorted(
                rules,
                key=lambda rule: rule.finding.code,
            )
        )

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def evaluate(self, *, content: str) -> PolicyAssessment:
        if not isinstance(content, str) or not content.strip():
            raise DomainValidationError("Input policy content cannot be blank")

        if len(content) > MAX_USER_MESSAGE_LENGTH:
            raise DomainValidationError(
                f"Input policy content cannot exceed {MAX_USER_MESSAGE_LENGTH} characters"
            )

        normalized_content = _normalize_text(content)
        if not normalized_content:
            raise DomainValidationError("Input policy content cannot be blank")

        matched_rules = tuple(
            rule
            for rule in self._rules
            if any(phrase in normalized_content for phrase in rule.phrases)
        )

        if not matched_rules:
            return PolicyAssessment(
                action=PolicyAction.ALLOW,
                risk_score=0,
                policy_version=self._policy_version,
            )

        action = max(
            (rule.action for rule in matched_rules),
            key=_ACTION_PRECEDENCE.__getitem__,
        )

        return PolicyAssessment(
            action=action,
            risk_score=max(rule.risk_score for rule in matched_rules),
            policy_version=self._policy_version,
            findings=tuple(rule.finding for rule in matched_rules),
        )
