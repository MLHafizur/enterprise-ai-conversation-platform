"""Recoverable worker orchestration for durable repository security scans."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from enterprise_chat_platform.application.accept_user_turn import Clock
from enterprise_chat_platform.application.code_security import TenantCodeSecurityPolicy
from enterprise_chat_platform.application.code_security_ai import (
    SecurityExplanationProvider,
    SecurityFindingContext,
)
from enterprise_chat_platform.domain.code_security import (
    CodeScanPhase,
    CodeScanSource,
    GateDecision,
    SecurityFinding,
)
from enterprise_chat_platform.domain.identifiers import CodeScanId, TenantId
from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    RepositoryScanError,
    RepositoryScanResult,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    ValidatedRepositoryUrl,
    validate_repository_url,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    ClaimedCodeScan,
)

logger = logging.getLogger(__name__)


class CodeScanWorkResult(StrEnum):
    NO_WORK = "no_work"
    COMPLETED = "completed"
    RESCHEDULED = "rescheduled"
    FAILED = "failed"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeScanWorkerConfig:
    lease_duration: timedelta
    max_attempts: int
    initial_backoff: timedelta
    max_backoff: timedelta
    allowed_git_hosts: frozenset[str]

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0):
            raise ValueError("Code scan lease duration must be positive")
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 20:
            raise ValueError("Code scan max attempts must be between 1 and 20")
        if self.initial_backoff <= timedelta(0) or self.max_backoff < self.initial_backoff:
            raise ValueError("Code scan backoff is invalid")
        if not self.allowed_git_hosts:
            raise ValueError("Code scan Git host allowlist cannot be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeScanTerminalEvent:
    """Content-free realtime/integration notification."""

    tenant_id: TenantId
    scan_id: CodeScanId
    status: str
    gate_decision: GateDecision | None
    total_findings: int
    occurred_at: datetime


class CodeScanStore(Protocol):
    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedCodeScan | None: ...

    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy: ...

    async def advance_claimed_phase(
        self,
        *,
        job: ClaimedCodeScan,
        phase: CodeScanPhase,
        updated_at: datetime,
    ) -> bool: ...

    async def complete_claimed(
        self,
        *,
        job: ClaimedCodeScan,
        findings: tuple[SecurityFinding, ...],
        gate_decision: GateDecision,
        commit_sha: str,
        files_scanned: int,
        bytes_scanned: int,
        safe_ai_summary: str | None,
        completed_at: datetime,
    ) -> bool: ...

    async def reschedule_claimed(
        self,
        *,
        job: ClaimedCodeScan,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool: ...

    async def fail_claimed(
        self,
        *,
        job: ClaimedCodeScan,
        safe_error: str,
        failed_at: datetime,
    ) -> bool: ...


class RepositoryScanner(Protocol):
    async def scan_repository(
        self,
        *,
        repository: ValidatedRepositoryUrl,
        ref: str,
    ) -> RepositoryScanResult: ...

    async def scan_diff(
        self,
        *,
        repository: ValidatedRepositoryUrl,
        base_ref: str,
        head_ref: str,
    ) -> RepositoryScanResult: ...


class RepositoryScannerFactory(Protocol):
    def __call__(self, policy: TenantCodeSecurityPolicy) -> RepositoryScanner: ...


class CodeScanEventPublisher(Protocol):
    async def publish(self, event: CodeScanTerminalEvent) -> None: ...


class NullCodeScanEventPublisher:
    async def publish(self, event: CodeScanTerminalEvent) -> None:
        del event


class CodeScanResultNotifier(Protocol):
    async def notify(
        self,
        *,
        job: ClaimedCodeScan,
        result: RepositoryScanResult,
        gate_decision: GateDecision,
    ) -> None: ...


class NullCodeScanResultNotifier:
    async def notify(
        self,
        *,
        job: ClaimedCodeScan,
        result: RepositoryScanResult,
        gate_decision: GateDecision,
    ) -> None:
        del job, result, gate_decision


class ProcessCodeScanWorker:
    """Claim, scan, gate, commit, and notify without holding DB transactions."""

    def __init__(
        self,
        *,
        store: CodeScanStore,
        scanner_factory: RepositoryScannerFactory,
        explanation_provider: SecurityExplanationProvider,
        clock: Clock,
        config: CodeScanWorkerConfig,
        publisher: CodeScanEventPublisher | None = None,
        result_notifier: CodeScanResultNotifier | None = None,
    ) -> None:
        self._store = store
        self._scanner_factory = scanner_factory
        self._explanation_provider = explanation_provider
        self._clock = clock
        self._config = config
        self._publisher = publisher or NullCodeScanEventPublisher()
        self._result_notifier = result_notifier or NullCodeScanResultNotifier()

    async def process_next(self) -> CodeScanWorkResult:
        now = self._clock.now()
        job = await self._store.claim_next(
            now=now,
            lease_duration=self._config.lease_duration,
        )
        if job is None:
            return CodeScanWorkResult.NO_WORK

        try:
            policy = await self._store.get_policy(tenant_id=job.tenant_id)
            repository = validate_repository_url(
                job.clone_url,
                allowed_hosts=self._config.allowed_git_hosts,
            )
            if not await self._store.advance_claimed_phase(
                job=job,
                phase=CodeScanPhase.DETECTING,
                updated_at=self._clock.now(),
            ):
                return CodeScanWorkResult.LEASE_LOST
            scanner = self._scanner_factory(policy)
            scan_result = await self._scan(job=job, repository=repository, scanner=scanner)
            if not await self._store.advance_claimed_phase(
                job=job,
                phase=CodeScanPhase.GATING,
                updated_at=self._clock.now(),
            ):
                return CodeScanWorkResult.LEASE_LOST
            gate_decision = policy.gate_policy().decide(scan_result.findings)
            safe_summary = await self._safe_summary(
                job=job,
                policy=policy,
                findings=scan_result.findings,
                gate_decision=gate_decision,
            )
            completed_at = self._clock.now()
            committed = await self._store.complete_claimed(
                job=job,
                findings=scan_result.findings,
                gate_decision=gate_decision,
                commit_sha=scan_result.commit_sha,
                files_scanned=scan_result.files_analyzed,
                bytes_scanned=scan_result.bytes_analyzed,
                safe_ai_summary=safe_summary,
                completed_at=completed_at,
            )
            if not committed:
                return CodeScanWorkResult.LEASE_LOST
            await self._publish_quietly(
                CodeScanTerminalEvent(
                    tenant_id=job.tenant_id,
                    scan_id=job.scan_id,
                    status="succeeded",
                    gate_decision=gate_decision,
                    total_findings=len(scan_result.findings),
                    occurred_at=completed_at,
                )
            )
            await self._notify_quietly(
                job=job,
                result=scan_result,
                gate_decision=gate_decision,
            )
            return CodeScanWorkResult.COMPLETED
        except RepositoryScanError:
            return await self._retry_or_fail(job=job, safe_error="Repository scan failed")
        except Exception:
            # Provider, malformed remote data, or adapter defects never leak source,
            # URLs, tokens, or command stderr into durable/public errors.
            return await self._retry_or_fail(job=job, safe_error="Security scan failed")

    async def _scan(
        self,
        *,
        job: ClaimedCodeScan,
        repository: ValidatedRepositoryUrl,
        scanner: RepositoryScanner,
    ) -> RepositoryScanResult:
        if job.source is CodeScanSource.PULL_REQUEST or (
            job.base_branch is not None and job.head_branch is not None
        ):
            if job.base_branch is None or job.head_branch is None:
                raise RepositoryScanError("Pull-request scan references are missing")
            return await scanner.scan_diff(
                repository=repository,
                base_ref=job.base_branch,
                head_ref=job.head_branch,
            )
        if job.branch is None:
            raise RepositoryScanError("Repository scan branch is missing")
        return await scanner.scan_repository(repository=repository, ref=job.branch)

    async def _safe_summary(
        self,
        *,
        job: ClaimedCodeScan,
        policy: TenantCodeSecurityPolicy,
        findings: tuple[SecurityFinding, ...],
        gate_decision: GateDecision,
    ) -> str | None:
        if not policy.ai_summary_enabled:
            return None
        contexts = tuple(_finding_context(finding) for finding in findings[:100])
        try:
            report = await self._explanation_provider.generate_report(
                repository_name=str(job.repository_id),
                scan_status=gate_decision.value,
                findings=contexts,
            )
        except Exception:
            # The deterministic scanner and policy gate are authoritative. An
            # unavailable advisory provider must never discard or delay their
            # durable result.
            return None
        # Database summaries are single-line safe metadata; downloadable reports
        # remain request-time responses and may preserve Markdown formatting.
        normalized = " ".join(report.split())
        return normalized[:2_000] or None

    async def _retry_or_fail(
        self,
        *,
        job: ClaimedCodeScan,
        safe_error: str,
    ) -> CodeScanWorkResult:
        attempted_at = self._clock.now()
        if job.attempt_count < self._config.max_attempts:
            exponent = max(job.attempt_count - 1, 0)
            delay = min(
                self._config.initial_backoff * (2**exponent),
                self._config.max_backoff,
            )
            rescheduled = await self._store.reschedule_claimed(
                job=job,
                available_at=attempted_at + delay,
                attempted_at=attempted_at,
            )
            return CodeScanWorkResult.RESCHEDULED if rescheduled else CodeScanWorkResult.LEASE_LOST
        failed = await self._store.fail_claimed(
            job=job,
            safe_error=safe_error,
            failed_at=attempted_at,
        )
        if not failed:
            return CodeScanWorkResult.LEASE_LOST
        await self._publish_quietly(
            CodeScanTerminalEvent(
                tenant_id=job.tenant_id,
                scan_id=job.scan_id,
                status="failed",
                gate_decision=None,
                total_findings=0,
                occurred_at=attempted_at,
            )
        )
        return CodeScanWorkResult.FAILED

    async def _publish_quietly(self, event: CodeScanTerminalEvent) -> None:
        try:
            await self._publisher.publish(event)
        except Exception:
            # Durable scan state is authoritative; notification retries are an
            # operational concern and never roll back completed security work.
            return

    async def _notify_quietly(
        self,
        *,
        job: ClaimedCodeScan,
        result: RepositoryScanResult,
        gate_decision: GateDecision,
    ) -> None:
        try:
            await self._result_notifier.notify(
                job=job,
                result=result,
                gate_decision=gate_decision,
            )
        except Exception as error:
            candidate_channels = getattr(error, "failed_channels", ())
            safe_channels = tuple(
                channel for channel in candidate_channels if channel in {"github", "slack", "email"}
            )
            logger.warning(
                "code scan result notification failed",
                extra={
                    "tenant_id": str(job.tenant_id),
                    "scan_id": str(job.scan_id),
                    "notification_channels": ",".join(safe_channels) or "unknown",
                },
            )
            return


def _finding_context(finding: SecurityFinding) -> SecurityFindingContext:
    owasp_by_cwe = {
        "CWE-22": "A01:2021",
        "CWE-78": "A03:2021",
        "CWE-79": "A03:2021",
        "CWE-89": "A03:2021",
        "CWE-95": "A03:2021",
        "CWE-798": "A07:2021",
    }
    return SecurityFindingContext(
        rule_id=finding.rule_id,
        severity=finding.severity.value,
        message=finding.safe_message,
        file_path=finding.file_path,
        line_number=finding.line_number,
        code_snippet=finding.code_snippet,
        recommendation=finding.recommendation,
        cwe_id=finding.cwe_id,
        owasp_category=owasp_by_cwe.get(finding.cwe_id or ""),
    )
