"""Strict domain contracts for deterministic source-code security analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import ClassVar
from uuid import uuid5

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import CodeScanId, SecurityFindingId

MAX_RULE_ID_LENGTH = 64
MAX_RULE_TITLE_LENGTH = 128
MAX_RULE_SET_VERSION_LENGTH = 64
MAX_SAFE_MESSAGE_LENGTH = 512
MAX_RECOMMENDATION_LENGTH = 1_024
MAX_FILE_PATH_LENGTH = 512
MAX_CODE_SNIPPET_LENGTH = 512
MAX_CWE_ID_LENGTH = 32
MAX_PATTERN_LENGTH = 512
MAX_SECURITY_RULES = 128
MAX_SECURITY_FINDINGS = 10_000
MAX_SECURITY_FILE_BYTES = 10_485_760
MAX_ANALYSIS_CODE_BYTES = MAX_SECURITY_FILE_BYTES
MAX_ANALYSIS_CACHE_ENTRIES = 4_096
MAX_SECURITY_REPOSITORY_FILES = 100_000
MAX_SECURITY_REPOSITORY_BYTES = 1_073_741_824

# Shared persistence boundaries. They live in the domain so every adapter applies
# the same limits before values reach a database, queue, or third-party API.
MAX_REPOSITORY_DISPLAY_NAME_LENGTH = 128
MAX_REPOSITORY_URL_LENGTH = 2_048
MAX_GIT_REF_LENGTH = 255
MAX_SOURCE_LABEL_LENGTH = 256
MAX_CONTENT_HASH_LENGTH = 64
MAX_SAFE_AI_SUMMARY_LENGTH = 2_000
MAX_SAFE_SCAN_ERROR_LENGTH = 512
MAX_WEBHOOK_DELIVERY_ID_LENGTH = 128
MAX_OWASP_CATEGORY_LENGTH = 32

DEFAULT_MAX_CODE_BYTES = 1_000_000
DEFAULT_MAX_FINDINGS = 10_000
DEFAULT_ANALYSIS_CACHE_ENTRIES = 128

_RULE_ID_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_RULE_SET_VERSION_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
_CWE_ID_PATTERN = re.compile(r"CWE-[1-9][0-9]{0,4}")


def scan_scoped_finding_id(
    *,
    scan_id: CodeScanId,
    fingerprint: str,
) -> SecurityFindingId:
    """Derive a stable row ID that remains unique across repeated scans."""
    if not isinstance(scan_id, CodeScanId):
        raise DomainValidationError("Security finding scan ID is invalid")
    if not isinstance(fingerprint, str) or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise DomainValidationError("Security finding fingerprint is invalid")
    return SecurityFindingId(uuid5(scan_id.value, f"security-finding:{fingerprint}"))


class SecuritySeverity(StrEnum):
    """Deterministic impact level assigned by a versioned security rule."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Return a stable ordering suitable for summaries and gate policy."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[SecuritySeverity, int] = {
    SecuritySeverity.INFO: 0,
    SecuritySeverity.LOW: 1,
    SecuritySeverity.MEDIUM: 2,
    SecuritySeverity.HIGH: 3,
    SecuritySeverity.CRITICAL: 4,
}


class SecurityDetector(StrEnum):
    """Deterministic detector that produced a finding."""

    AST = "ast"
    PATTERN = "pattern"


class CodeRepositoryProvider(StrEnum):
    """Supported repository origin categories."""

    GITHUB = "github"
    GITLAB = "gitlab"
    GENERIC_GIT = "generic_git"


class CodeScanSource(StrEnum):
    """Trusted description of how code entered the scanning workflow."""

    SNIPPET = "snippet"
    FILE_UPLOAD = "file_upload"
    REPOSITORY = "repository"
    PULL_REQUEST = "pull_request"
    WEBHOOK = "webhook"
    CI = "ci"


class CodeScanStatus(StrEnum):
    """Lifecycle status of one code scan."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CodeScanPhase(StrEnum):
    """Fine-grained, non-secret progress phase for a code scan."""

    RECEIVED = "received"
    VALIDATING = "validating"
    PARSING = "parsing"
    DETECTING = "detecting"
    DEDUPLICATING = "deduplicating"
    REDACTING = "redacting"
    GATING = "gating"
    COMPLETED = "completed"


class GateDecision(StrEnum):
    """Authoritative deterministic result of applying a security gate policy."""

    APPROVE = "approve"
    REVIEW_REQUIRED = "review_required"
    BLOCK = "block"


class SecurityReviewDecision(StrEnum):
    """Terminal decision recorded by an authorized human reviewer."""

    APPROVED = "approved"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RuleSetVersion:
    """Opaque version included in cache keys and finding fingerprints."""

    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or len(self.value) > MAX_RULE_SET_VERSION_LENGTH
            or _RULE_SET_VERSION_PATTERN.fullmatch(self.value) is None
        ):
            raise DomainValidationError("Security rule-set version is invalid")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityRule:
    """Immutable definition of one deterministic security control."""

    rule_id: str
    title: str
    severity: SecuritySeverity
    detectors: tuple[SecurityDetector, ...]
    safe_message: str
    recommendation: str
    cwe_id: str | None = None
    pattern: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.rule_id, str)
            or len(self.rule_id) > MAX_RULE_ID_LENGTH
            or _RULE_ID_PATTERN.fullmatch(self.rule_id) is None
        ):
            raise DomainValidationError("Security rule ID is invalid")

        _validate_safe_text(
            self.title,
            field_name="Security rule title",
            maximum_length=MAX_RULE_TITLE_LENGTH,
        )
        _validate_safe_text(
            self.safe_message,
            field_name="Security rule safe message",
            maximum_length=MAX_SAFE_MESSAGE_LENGTH,
        )
        _validate_safe_text(
            self.recommendation,
            field_name="Security rule recommendation",
            maximum_length=MAX_RECOMMENDATION_LENGTH,
        )

        if not isinstance(self.severity, SecuritySeverity):
            raise DomainValidationError("Security rule severity is invalid")

        try:
            normalized_detectors = tuple(self.detectors)
        except TypeError:
            raise DomainValidationError("Security rule detectors are invalid") from None
        if not normalized_detectors or any(
            not isinstance(detector, SecurityDetector) for detector in normalized_detectors
        ):
            raise DomainValidationError("Security rule requires valid detectors")
        if len(set(normalized_detectors)) != len(normalized_detectors):
            raise DomainValidationError("Security rule detectors must be unique")

        if self.cwe_id is not None and (
            not isinstance(self.cwe_id, str)
            or len(self.cwe_id) > MAX_CWE_ID_LENGTH
            or _CWE_ID_PATTERN.fullmatch(self.cwe_id) is None
        ):
            raise DomainValidationError("Security rule CWE ID is invalid")

        if self.pattern is not None:
            if (
                not isinstance(self.pattern, str)
                or not self.pattern
                or len(self.pattern) > MAX_PATTERN_LENGTH
            ):
                raise DomainValidationError("Security rule pattern is invalid")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise DomainValidationError("Security rule pattern is invalid") from exc
            if SecurityDetector.PATTERN not in normalized_detectors:
                raise DomainValidationError("Security rule pattern requires the pattern detector")

        if not isinstance(self.enabled, bool):
            raise DomainValidationError("Security rule enabled flag is invalid")

        object.__setattr__(self, "detectors", normalized_detectors)


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityFinding:
    """One redacted, stable, deduplicated security finding."""

    finding_id: SecurityFindingId
    fingerprint: str
    rule_id: str
    rule_set_version: RuleSetVersion
    severity: SecuritySeverity
    detector: SecurityDetector
    safe_message: str
    recommendation: str
    file_path: str
    line_number: int
    column_number: int
    code_snippet: str
    cwe_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.finding_id, SecurityFindingId):
            raise DomainValidationError("Security finding ID is invalid")
        if (
            not isinstance(self.fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(self.fingerprint) is None
        ):
            raise DomainValidationError("Security finding fingerprint is invalid")
        if (
            not isinstance(self.rule_id, str)
            or len(self.rule_id) > MAX_RULE_ID_LENGTH
            or _RULE_ID_PATTERN.fullmatch(self.rule_id) is None
        ):
            raise DomainValidationError("Security finding rule ID is invalid")
        if not isinstance(self.rule_set_version, RuleSetVersion):
            raise DomainValidationError("Security finding rule-set version is invalid")
        if not isinstance(self.severity, SecuritySeverity):
            raise DomainValidationError("Security finding severity is invalid")
        if not isinstance(self.detector, SecurityDetector):
            raise DomainValidationError("Security finding detector is invalid")

        _validate_safe_text(
            self.safe_message,
            field_name="Security finding safe message",
            maximum_length=MAX_SAFE_MESSAGE_LENGTH,
        )
        _validate_safe_text(
            self.recommendation,
            field_name="Security finding recommendation",
            maximum_length=MAX_RECOMMENDATION_LENGTH,
        )
        _validate_safe_text(
            self.file_path,
            field_name="Security finding file path",
            maximum_length=MAX_FILE_PATH_LENGTH,
        )
        _validate_relative_posix_path(self.file_path)
        _validate_safe_text(
            self.code_snippet,
            field_name="Security finding code snippet",
            maximum_length=MAX_CODE_SNIPPET_LENGTH,
        )
        if "\n" in self.code_snippet or "\r" in self.code_snippet:
            raise DomainValidationError("Security finding code snippet must be one line")
        if (
            isinstance(self.line_number, bool)
            or not isinstance(self.line_number, int)
            or self.line_number < 1
        ):
            raise DomainValidationError("Security finding line number must be positive")
        if (
            isinstance(self.column_number, bool)
            or not isinstance(self.column_number, int)
            or self.column_number < 0
        ):
            raise DomainValidationError("Security finding column number cannot be negative")
        if self.cwe_id is not None and (
            not isinstance(self.cwe_id, str)
            or len(self.cwe_id) > MAX_CWE_ID_LENGTH
            or _CWE_ID_PATTERN.fullmatch(self.cwe_id) is None
        ):
            raise DomainValidationError("Security finding CWE ID is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecuritySummary:
    """Bounded severity counts for a completed deterministic analysis."""

    total_findings: int
    info: int = 0
    low: int = 0
    medium: int = 0
    high: int = 0
    critical: int = 0

    _COUNT_FIELDS: ClassVar[tuple[str, ...]] = (
        "info",
        "low",
        "medium",
        "high",
        "critical",
    )

    def __post_init__(self) -> None:
        counts = tuple(getattr(self, field) for field in self._COUNT_FIELDS)
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts
        ):
            raise DomainValidationError("Security summary counts must be nonnegative integers")
        if (
            isinstance(self.total_findings, bool)
            or not isinstance(self.total_findings, int)
            or self.total_findings < 0
            or self.total_findings > MAX_SECURITY_FINDINGS
        ):
            raise DomainValidationError("Security summary total is invalid")
        if sum(counts) != self.total_findings:
            raise DomainValidationError("Security summary counts must equal the total")

    @classmethod
    def from_findings(cls, findings: tuple[SecurityFinding, ...]) -> SecuritySummary:
        """Build a summary without exposing source text or mutable collections."""
        if len(findings) > MAX_SECURITY_FINDINGS:
            raise DomainValidationError(
                f"Security summary cannot exceed {MAX_SECURITY_FINDINGS} findings"
            )
        if any(not isinstance(finding, SecurityFinding) for finding in findings):
            raise DomainValidationError("Security summary findings are invalid")
        fingerprints = [finding.fingerprint for finding in findings]
        if len(fingerprints) != len(set(fingerprints)):
            raise DomainValidationError("Security summary findings must be deduplicated")
        counts = {severity: 0 for severity in SecuritySeverity}
        for finding in findings:
            counts[finding.severity] += 1
        return cls(
            total_findings=len(findings),
            info=counts[SecuritySeverity.INFO],
            low=counts[SecuritySeverity.LOW],
            medium=counts[SecuritySeverity.MEDIUM],
            high=counts[SecuritySeverity.HIGH],
            critical=counts[SecuritySeverity.CRITICAL],
        )

    @property
    def highest_severity(self) -> SecuritySeverity | None:
        """Return the highest present severity, or None for a clean scan."""
        for severity, count in (
            (SecuritySeverity.CRITICAL, self.critical),
            (SecuritySeverity.HIGH, self.high),
            (SecuritySeverity.MEDIUM, self.medium),
            (SecuritySeverity.LOW, self.low),
            (SecuritySeverity.INFO, self.info),
        ):
            if count:
                return severity
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityAnalysisSettings:
    """Resource bounds and detector switches for deterministic analysis."""

    max_code_bytes: int = DEFAULT_MAX_CODE_BYTES
    max_findings: int = DEFAULT_MAX_FINDINGS
    cache_entries: int = DEFAULT_ANALYSIS_CACHE_ENTRIES
    ast_enabled: bool = True
    pattern_enabled: bool = True
    redact_secrets: bool = True

    def __post_init__(self) -> None:
        _validate_bounded_integer(
            self.max_code_bytes,
            field_name="Maximum code bytes",
            minimum=1,
            maximum=MAX_ANALYSIS_CODE_BYTES,
        )
        _validate_bounded_integer(
            self.max_findings,
            field_name="Maximum security findings",
            minimum=1,
            maximum=MAX_SECURITY_FINDINGS,
        )
        _validate_bounded_integer(
            self.cache_entries,
            field_name="Analysis cache entries",
            minimum=1,
            maximum=MAX_ANALYSIS_CACHE_ENTRIES,
        )
        for value, field_name in (
            (self.ast_enabled, "AST detector flag"),
            (self.pattern_enabled, "Pattern detector flag"),
            (self.redact_secrets, "Secret-redaction flag"),
        ):
            if not isinstance(value, bool):
                raise DomainValidationError(f"{field_name} is invalid")
        if not self.ast_enabled and not self.pattern_enabled:
            raise DomainValidationError("At least one security detector must be enabled")
        if not self.redact_secrets:
            raise DomainValidationError("Secret redaction cannot be disabled")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityGatePolicy:
    """Deterministic, fail-closed mapping from findings to a gate decision."""

    block_severities: tuple[SecuritySeverity, ...] = (SecuritySeverity.CRITICAL,)
    review_severities: tuple[SecuritySeverity, ...] = (SecuritySeverity.HIGH,)

    def __post_init__(self) -> None:
        block = _normalize_severities(self.block_severities, field_name="Block severities")
        review = _normalize_severities(self.review_severities, field_name="Review severities")
        if set(block) & set(review):
            raise DomainValidationError("Block and review severities cannot overlap")
        if SecuritySeverity.CRITICAL not in block:
            raise DomainValidationError("Critical findings must always be blocked")
        if SecuritySeverity.HIGH not in set(block).union(review):
            raise DomainValidationError("High findings must block or require review")
        object.__setattr__(self, "block_severities", block)
        object.__setattr__(self, "review_severities", review)

    def decide(self, findings: tuple[SecurityFinding, ...]) -> GateDecision:
        """Apply blocking before review so the strongest action always wins."""
        if any(not isinstance(finding, SecurityFinding) for finding in findings):
            raise DomainValidationError("Security gate findings are invalid")
        severities = {finding.severity for finding in findings}
        if severities.intersection(self.block_severities):
            return GateDecision.BLOCK
        if severities.intersection(self.review_severities):
            return GateDecision.REVIEW_REQUIRED
        return GateDecision.APPROVE


def _validate_safe_text(value: object, *, field_name: str, maximum_length: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DomainValidationError(f"{field_name} cannot be blank or have surrounding whitespace")
    if len(value) > maximum_length:
        raise DomainValidationError(f"{field_name} cannot exceed {maximum_length} characters")
    if not value.isprintable():
        raise DomainValidationError(f"{field_name} must be printable")


def _validate_bounded_integer(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DomainValidationError(f"{field_name} must be between {minimum} and {maximum}")


def _validate_relative_posix_path(value: str) -> None:
    if "\\" in value or value.startswith("/") or "//" in value:
        raise DomainValidationError("Security finding file path must be relative POSIX syntax")
    path = PurePosixPath(value)
    if value in {".", ".."} or any(part in {"", ".", ".."} for part in path.parts):
        raise DomainValidationError("Security finding file path cannot traverse directories")
    if path.as_posix() != value:
        raise DomainValidationError("Security finding file path must be normalized")


def _normalize_severities(
    values: tuple[SecuritySeverity, ...],
    *,
    field_name: str,
) -> tuple[SecuritySeverity, ...]:
    try:
        normalized = tuple(values)
    except TypeError:
        raise DomainValidationError(f"{field_name} are invalid") from None
    if any(not isinstance(value, SecuritySeverity) for value in normalized):
        raise DomainValidationError(f"{field_name} are invalid")
    if len(set(normalized)) != len(normalized):
        raise DomainValidationError(f"{field_name} must be unique")
    return normalized
