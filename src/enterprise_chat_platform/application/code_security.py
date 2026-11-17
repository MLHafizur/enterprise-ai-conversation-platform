"""Application orchestration for tenant-scoped secure code analysis."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from enterprise_chat_platform.application.accept_user_turn import ActorContext, Clock
from enterprise_chat_platform.domain.code_security import (
    CodeScanSource,
    GateDecision,
    SecurityFinding,
    SecurityGatePolicy,
    SecuritySeverity,
    SecuritySummary,
    scan_scoped_finding_id,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    CodeRepositoryId,
    CodeScanId,
    SecurityReviewId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import (
    DEFAULT_RULE_SET_VERSION,
    SecurityEngine,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TenantCodeSecurityPolicy:
    """Tenant-controlled gate and resource policy with safe defaults."""

    revision: int = 0
    block_severity: SecuritySeverity = SecuritySeverity.CRITICAL
    review_severity: SecuritySeverity = SecuritySeverity.HIGH
    max_file_bytes: int = 1_048_576
    max_repository_files: int = 2_000
    max_repository_bytes: int = 52_428_800
    auto_scan_push: bool = True
    auto_scan_pull_request: bool = True
    ai_summary_enabled: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or self.revision < 0:
            raise DomainValidationError("Code security policy revision cannot be negative")
        if self.block_severity.rank <= self.review_severity.rank:
            raise DomainValidationError("Block severity must be stronger than review severity")
        if not 1_024 <= self.max_file_bytes <= 10_485_760:
            raise DomainValidationError("Code security file limit is invalid")
        if not 1 <= self.max_repository_files <= 100_000:
            raise DomainValidationError("Code security repository file limit is invalid")
        if not self.max_file_bytes <= self.max_repository_bytes <= 1_073_741_824:
            raise DomainValidationError("Code security repository byte limit is invalid")
        if any(
            not isinstance(value, bool)
            for value in (
                self.auto_scan_push,
                self.auto_scan_pull_request,
                self.ai_summary_enabled,
            )
        ):
            raise DomainValidationError("Code security policy flags are invalid")

    def gate_policy(self) -> SecurityGatePolicy:
        """Block/review at the configured threshold and every stronger severity."""
        block = tuple(
            severity for severity in SecuritySeverity if severity.rank >= self.block_severity.rank
        )
        review = tuple(
            severity
            for severity in SecuritySeverity
            if self.review_severity.rank <= severity.rank < self.block_severity.rank
        )
        return SecurityGatePolicy(block_severities=block, review_severities=review)


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletedCodeScan:
    """Safe immutable result returned after a direct scan commits."""

    scan_id: CodeScanId
    source: CodeScanSource
    source_label: str
    gate_decision: GateDecision
    summary: SecuritySummary
    findings: tuple[SecurityFinding, ...]
    ruleset_version: str
    files_scanned: int
    bytes_scanned: int
    completed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class QueuedCodeScan:
    """Reference returned when repository work has been durably queued."""

    scan_id: CodeScanId
    source: CodeScanSource
    repository_id: CodeRepositoryId
    queued_at: datetime


class CodeSecurityIds(Protocol):
    def new_scan_id(self) -> CodeScanId: ...

    def new_review_id(self) -> SecurityReviewId: ...


class CodeSecurityPersistence(Protocol):
    """Transactional boundary; implementations never persist raw source."""

    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy: ...

    async def save_completed_scan(
        self,
        *,
        tenant_id: TenantId,
        actor_id: UserId,
        scan_id: CodeScanId,
        source: CodeScanSource,
        source_label: str,
        content_hash: str,
        findings: tuple[SecurityFinding, ...],
        gate_decision: GateDecision,
        files_scanned: int,
        bytes_scanned: int,
        completed_at: datetime,
    ) -> None: ...


class AnalyzeCodeService:
    """Run deterministic rules, apply the tenant gate, and persist only safe data."""

    def __init__(
        self,
        *,
        persistence: CodeSecurityPersistence,
        engine: SecurityEngine,
        clock: Clock,
        ids: CodeSecurityIds,
    ) -> None:
        self._persistence = persistence
        self._engine = engine
        self._clock = clock
        self._ids = ids

    async def analyze(
        self,
        *,
        actor: ActorContext,
        source: CodeScanSource,
        source_label: str,
        code: str,
    ) -> CompletedCodeScan:
        if source not in {CodeScanSource.SNIPPET, CodeScanSource.FILE_UPLOAD, CodeScanSource.CI}:
            raise DomainValidationError("Direct analysis source is invalid")
        if not source_label or source_label != source_label.strip() or len(source_label) > 256:
            raise DomainValidationError("Code scan source label is invalid")

        policy = await self._persistence.get_policy(tenant_id=actor.tenant_id)
        code_size = len(code.encode("utf-8"))
        if code_size > policy.max_file_bytes:
            raise DomainValidationError("Code analysis input exceeds the tenant file limit")

        scan_id = self._ids.new_scan_id()
        findings = tuple(
            replace(
                finding,
                finding_id=scan_scoped_finding_id(
                    scan_id=scan_id,
                    fingerprint=finding.fingerprint,
                ),
            )
            for finding in await asyncio.to_thread(
                self._engine.analyze_code,
                code,
                source_label,
            )
        )
        gate_decision = policy.gate_policy().decide(findings)
        summary = SecuritySummary.from_findings(findings)
        completed_at = normalize_utc(
            self._clock.now(),
            field_name="code_scan.completed_at",
        )
        content_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        await self._persistence.save_completed_scan(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            scan_id=scan_id,
            source=source,
            source_label=source_label,
            content_hash=content_hash,
            findings=findings,
            gate_decision=gate_decision,
            files_scanned=1,
            bytes_scanned=code_size,
            completed_at=completed_at,
        )
        return CompletedCodeScan(
            scan_id=scan_id,
            source=source,
            source_label=source_label,
            gate_decision=gate_decision,
            summary=summary,
            findings=findings,
            ruleset_version=DEFAULT_RULE_SET_VERSION.value,
            files_scanned=1,
            bytes_scanned=code_size,
            completed_at=completed_at,
        )
