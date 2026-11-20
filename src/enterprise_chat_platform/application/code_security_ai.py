"""Provider-neutral contracts for explaining deterministic security findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityFindingContext:
    """Redacted finding evidence safe to pass to an explanation provider."""

    rule_id: str
    severity: str
    message: str
    file_path: str
    line_number: int
    code_snippet: str
    recommendation: str
    cwe_id: str | None
    owasp_category: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityFindingExplanation:
    """Structured advisory explanation; never an authoritative gate decision."""

    meaning: str
    exploitation: str
    remediation_steps: tuple[str, ...]
    secure_example: str


class SecurityExplanationProvider(Protocol):
    async def explain(
        self,
        *,
        finding: SecurityFindingContext,
    ) -> SecurityFindingExplanation: ...

    async def suggest_fix(
        self,
        *,
        finding: SecurityFindingContext,
    ) -> str: ...

    async def generate_report(
        self,
        *,
        repository_name: str | None,
        scan_status: str,
        findings: tuple[SecurityFindingContext, ...],
    ) -> str: ...


class DisabledSecurityExplanationProvider:
    """Fail-closed provider used when advisory AI is globally disabled."""

    @staticmethod
    def _disabled() -> RuntimeError:
        return RuntimeError("Security explanation provider is disabled")

    async def explain(
        self,
        *,
        finding: SecurityFindingContext,
    ) -> SecurityFindingExplanation:
        del finding
        raise self._disabled()

    async def suggest_fix(
        self,
        *,
        finding: SecurityFindingContext,
    ) -> str:
        del finding
        raise self._disabled()

    async def generate_report(
        self,
        *,
        repository_name: str | None,
        scan_status: str,
        findings: tuple[SecurityFindingContext, ...],
    ) -> str:
        del repository_name, scan_status, findings
        raise self._disabled()


class LocalSecurityExplanationProvider:
    """Deterministic local provider used for learning and offline tests."""

    async def explain(
        self,
        *,
        finding: SecurityFindingContext,
    ) -> SecurityFindingExplanation:
        cwe = f" ({finding.cwe_id})" if finding.cwe_id else ""
        return SecurityFindingExplanation(
            meaning=f"{finding.message}{cwe} was detected by rule {finding.rule_id}.",
            exploitation=(
                "An attacker may be able to influence this operation when untrusted input "
                "reaches the highlighted code path. Confirm the data flow during review."
            ),
            remediation_steps=(
                finding.recommendation,
                "Add a regression test that exercises hostile input.",
                "Re-run the deterministic scan before approving the change.",
            ),
            secure_example=(
                "The exact secure replacement depends on the surrounding application. "
                "Apply the recommendation with framework-native safe APIs."
            ),
        )

    async def suggest_fix(
        self,
        *,
        finding: SecurityFindingContext,
    ) -> str:
        return (
            f"Recommended change for {finding.rule_id}: {finding.recommendation}. "
            "Preserve behavior with a focused security regression test."
        )

    async def generate_report(
        self,
        *,
        repository_name: str | None,
        scan_status: str,
        findings: tuple[SecurityFindingContext, ...],
    ) -> str:
        subject = repository_name or "submitted code"
        lines = [
            "# Security analysis report",
            "",
            f"- Scope: {subject}",
            f"- Gate status: {scan_status}",
            f"- Findings: {len(findings)}",
            "",
            "## Findings",
        ]
        if not findings:
            lines.append("No deterministic findings were recorded.")
        for index, finding in enumerate(findings, 1):
            location = f"{finding.file_path}:{finding.line_number}"
            cwe = f" / {finding.cwe_id}" if finding.cwe_id else ""
            lines.extend(
                [
                    "",
                    f"### {index}. {finding.message}",
                    f"- Severity: {finding.severity}{cwe}",
                    f"- Location: {location}",
                    f"- Recommendation: {finding.recommendation}",
                ]
            )
        lines.extend(
            [
                "",
                "## Next steps",
                "Fix blocked findings first, review high-risk findings, and re-run the scan.",
            ]
        )
        return "\n".join(lines)
