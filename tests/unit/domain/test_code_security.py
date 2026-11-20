from dataclasses import FrozenInstanceError

import pytest

from enterprise_chat_platform.domain.code_security import (
    CodeScanPhase,
    CodeScanSource,
    CodeScanStatus,
    GateDecision,
    RuleSetVersion,
    SecurityAnalysisSettings,
    SecurityDetector,
    SecurityFinding,
    SecurityGatePolicy,
    SecurityRule,
    SecuritySeverity,
    SecuritySummary,
    scan_scoped_finding_id,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import CodeScanId, SecurityFindingId


def _finding(
    *,
    severity: SecuritySeverity = SecuritySeverity.HIGH,
    fingerprint: str = "a" * 64,
    file_path: str = "src/app.py",
) -> SecurityFinding:
    return SecurityFinding(
        finding_id=SecurityFindingId.new(),
        fingerprint=fingerprint,
        rule_id="sql_injection",
        rule_set_version=RuleSetVersion("day10.v1"),
        severity=severity,
        detector=SecurityDetector.AST,
        safe_message="Dynamic SQL may contain untrusted input",
        recommendation="Use a parameterized query",
        file_path=file_path,
        line_number=3,
        column_number=4,
        code_snippet='cursor.execute("[REDACTED]" + user_id)',
        cwe_id="CWE-89",
    )


def test_security_enum_contracts_are_stable() -> None:
    assert tuple(SecuritySeverity) == (
        SecuritySeverity.INFO,
        SecuritySeverity.LOW,
        SecuritySeverity.MEDIUM,
        SecuritySeverity.HIGH,
        SecuritySeverity.CRITICAL,
    )
    assert SecurityDetector.AST.value == "ast"
    assert SecurityDetector.PATTERN.value == "pattern"
    assert CodeScanSource.PULL_REQUEST.value == "pull_request"
    assert CodeScanStatus.SUCCEEDED.value == "succeeded"
    assert CodeScanPhase.DEDUPLICATING.value == "deduplicating"
    assert GateDecision.REVIEW_REQUIRED.value == "review_required"


@pytest.mark.parametrize("value", ["", " Day10", "UPPER", "bad/version", "x" * 65])
def test_rule_set_version_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(DomainValidationError, match="version"):
        RuleSetVersion(value)


def test_security_rule_validates_detector_and_cwe_contracts() -> None:
    with pytest.raises(DomainValidationError, match="pattern detector"):
        SecurityRule(
            rule_id="bad_rule",
            title="Bad rule",
            severity=SecuritySeverity.HIGH,
            detectors=(SecurityDetector.AST,),
            safe_message="A safe message",
            recommendation="A safe recommendation",
            pattern=r"eval\(",
        )

    with pytest.raises(DomainValidationError, match="CWE"):
        SecurityRule(
            rule_id="bad_rule",
            title="Bad rule",
            severity=SecuritySeverity.HIGH,
            detectors=(SecurityDetector.AST,),
            safe_message="A safe message",
            recommendation="A safe recommendation",
            cwe_id="cwe-95",
        )


@pytest.mark.parametrize(
    "file_path",
    ["/src/app.py", "../app.py", "src\\app.py", "src//app.py", "src/./app.py"],
)
def test_security_finding_rejects_unsafe_paths(file_path: str) -> None:
    with pytest.raises(DomainValidationError, match="path"):
        _finding(file_path=file_path)


@pytest.mark.parametrize(("line", "column"), [(True, 0), (1.5, 0), (1, False), (1, 2.5)])
def test_security_finding_rejects_noninteger_locations(
    line: object,
    column: object,
) -> None:
    with pytest.raises(DomainValidationError, match="number"):
        SecurityFinding(
            finding_id=SecurityFindingId.new(),
            fingerprint="a" * 64,
            rule_id="sql_injection",
            rule_set_version=RuleSetVersion("day10.v1"),
            severity=SecuritySeverity.HIGH,
            detector=SecurityDetector.AST,
            safe_message="Dynamic SQL may contain untrusted input",
            recommendation="Use a parameterized query",
            file_path="src/app.py",
            line_number=line,  # type: ignore[arg-type]
            column_number=column,  # type: ignore[arg-type]
            code_snippet="cursor.execute(query)",
        )


def test_security_finding_is_immutable() -> None:
    finding = _finding()

    with pytest.raises(FrozenInstanceError):
        setattr(finding, "safe_message", "changed")  # noqa: B010


def test_finding_row_identity_is_stable_within_scan_and_unique_across_scans() -> None:
    first_scan = CodeScanId.parse("11111111-1111-4111-8111-111111111111")
    second_scan = CodeScanId.parse("22222222-2222-4222-8222-222222222222")

    first_id = scan_scoped_finding_id(scan_id=first_scan, fingerprint="a" * 64)

    assert first_id == scan_scoped_finding_id(scan_id=first_scan, fingerprint="a" * 64)
    assert first_id != scan_scoped_finding_id(
        scan_id=second_scan,
        fingerprint="a" * 64,
    )


def test_security_summary_counts_and_highest_severity() -> None:
    findings = (
        _finding(severity=SecuritySeverity.MEDIUM, fingerprint="a" * 64),
        _finding(severity=SecuritySeverity.CRITICAL, fingerprint="b" * 64),
    )

    summary = SecuritySummary.from_findings(findings)

    assert summary.total_findings == 2
    assert summary.medium == 1
    assert summary.critical == 1
    assert summary.highest_severity is SecuritySeverity.CRITICAL


def test_security_summary_rejects_duplicate_fingerprints() -> None:
    with pytest.raises(DomainValidationError, match="deduplicated"):
        SecuritySummary.from_findings((_finding(), _finding()))


def test_gate_policy_blocks_before_review_and_approves_clean_scan() -> None:
    policy = SecurityGatePolicy()

    assert policy.decide(()) is GateDecision.APPROVE
    assert (
        policy.decide((_finding(severity=SecuritySeverity.HIGH),)) is GateDecision.REVIEW_REQUIRED
    )
    assert (
        policy.decide(
            (
                _finding(severity=SecuritySeverity.HIGH, fingerprint="a" * 64),
                _finding(severity=SecuritySeverity.CRITICAL, fingerprint="b" * 64),
            )
        )
        is GateDecision.BLOCK
    )


def test_gate_policy_rejects_fail_open_severity_configuration() -> None:
    with pytest.raises(DomainValidationError, match="Critical"):
        SecurityGatePolicy(block_severities=(), review_severities=(SecuritySeverity.HIGH,))
    with pytest.raises(DomainValidationError, match="High"):
        SecurityGatePolicy(
            block_severities=(SecuritySeverity.CRITICAL,),
            review_severities=(),
        )


def test_analysis_settings_are_frozen_and_redaction_is_mandatory() -> None:
    value = SecurityAnalysisSettings()
    with pytest.raises(FrozenInstanceError):
        setattr(value, "max_findings", 1)  # noqa: B010
    with pytest.raises(DomainValidationError, match="redaction"):
        SecurityAnalysisSettings(redact_secrets=False)
    with pytest.raises(DomainValidationError, match="detector"):
        SecurityAnalysisSettings(ast_enabled=False, pattern_enabled=False)
