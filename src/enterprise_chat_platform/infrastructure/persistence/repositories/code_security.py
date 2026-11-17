"""PostgreSQL transactions for tenant-scoped secure code analysis."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.code_security import TenantCodeSecurityPolicy
from enterprise_chat_platform.domain.code_security import (
    CodeRepositoryProvider,
    CodeScanPhase,
    CodeScanSource,
    CodeScanStatus,
    GateDecision,
    SecurityFinding,
    SecurityReviewDecision,
    SecuritySeverity,
    SecuritySummary,
    scan_scoped_finding_id,
)
from enterprise_chat_platform.domain.identifiers import (
    CodeRepositoryId,
    CodeScanId,
    SecurityFindingId,
    SecurityReviewId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import (
    DEFAULT_RULE_SET_VERSION,
)
from enterprise_chat_platform.infrastructure.persistence.database import AsyncSessionFactory
from enterprise_chat_platform.infrastructure.persistence.models import (
    CodeRepositoryRecord,
    CodeScanRecord,
    CodeScanReviewRecord,
    CodeSecurityFindingRecord,
    CodeSecuritySettingsRecord,
)

_OWASP_BY_CWE = {
    "CWE-22": "A01:2021",
    "CWE-78": "A03:2021",
    "CWE-79": "A03:2021",
    "CWE-89": "A03:2021",
    "CWE-95": "A03:2021",
    "CWE-798": "A07:2021",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedCodeScan:
    tenant_id: TenantId
    scan_id: CodeScanId
    repository_id: CodeRepositoryId
    source: CodeScanSource
    clone_url: str
    branch: str | None
    base_branch: str | None
    head_branch: str | None
    lease_token: UUID
    attempt_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ScanDetail:
    scan: CodeScanRecord
    findings: tuple[CodeSecurityFindingRecord, ...]
    review: CodeScanReviewRecord | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeSecurityDashboardData:
    counts: dict[str, int]
    recent_scans: tuple[CodeScanRecord, ...]
    top_vulnerabilities: tuple[tuple[str, str, str | None, int], ...]
    trend: tuple[tuple[str, int, int, int, int, int, int], ...]


class SqlAlchemyCodeSecurityStore:
    """Short, fenced transactions for scans, settings, repositories, and reviews."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy:
        async with self._session_factory() as session:
            record = await session.get(CodeSecuritySettingsRecord, tenant_id.value)
            return _policy_from_record(record)

    async def update_policy(
        self,
        *,
        tenant_id: TenantId,
        actor_id: UserId,
        expected_revision: int,
        policy: TenantCodeSecurityPolicy,
        updated_at: datetime,
    ) -> TenantCodeSecurityPolicy | None:
        timestamp = normalize_utc(updated_at, field_name="code_security_settings.updated_at")
        async with self._session_factory() as session:
            async with session.begin():
                record = await session.scalar(
                    select(CodeSecuritySettingsRecord)
                    .where(CodeSecuritySettingsRecord.tenant_id == tenant_id.value)
                    .with_for_update()
                )
                current_revision = 0 if record is None else record.revision
                if current_revision != expected_revision:
                    return None
                next_policy = replace(policy, revision=current_revision + 1)
                if record is None:
                    session.add(
                        CodeSecuritySettingsRecord(
                            tenant_id=tenant_id.value,
                            revision=next_policy.revision,
                            block_severity=next_policy.block_severity.value,
                            review_severity=next_policy.review_severity.value,
                            max_file_bytes=next_policy.max_file_bytes,
                            max_repository_files=next_policy.max_repository_files,
                            max_repository_bytes=next_policy.max_repository_bytes,
                            auto_scan_push=next_policy.auto_scan_push,
                            auto_scan_pull_request=next_policy.auto_scan_pull_request,
                            ai_summary_enabled=next_policy.ai_summary_enabled,
                            updated_by=actor_id.value,
                            updated_at=timestamp,
                        )
                    )
                else:
                    _apply_policy(
                        record=record,
                        policy=next_policy,
                        actor_id=actor_id,
                        updated_at=timestamp,
                    )
                return next_policy

    async def add_repository(
        self,
        *,
        tenant_id: TenantId,
        repository_id: CodeRepositoryId,
        provider: CodeRepositoryProvider,
        display_name: str,
        clone_url: str,
        normalized_url: str,
        default_branch: str,
        actor_id: UserId,
        created_at: datetime,
    ) -> CodeRepositoryRecord:
        timestamp = normalize_utc(created_at, field_name="code_repository.created_at")
        record = CodeRepositoryRecord(
            tenant_id=tenant_id.value,
            id=repository_id.value,
            provider=provider.value,
            display_name=display_name,
            clone_url=clone_url,
            normalized_url=normalized_url,
            default_branch=default_branch,
            enabled=True,
            created_by=actor_id.value,
            created_at=timestamp,
            updated_at=timestamp,
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(record)
            session.expunge(record)
        return record

    async def list_repositories(
        self,
        *,
        tenant_id: TenantId,
        limit: int = 100,
    ) -> tuple[CodeRepositoryRecord, ...]:
        async with self._session_factory() as session:
            records = tuple(
                (
                    await session.scalars(
                        select(CodeRepositoryRecord)
                        .where(CodeRepositoryRecord.tenant_id == tenant_id.value)
                        .order_by(CodeRepositoryRecord.display_name, CodeRepositoryRecord.id)
                        .limit(limit)
                    )
                ).all()
            )
            for record in records:
                session.expunge(record)
            return records

    async def get_repository(
        self,
        *,
        tenant_id: TenantId,
        repository_id: CodeRepositoryId,
    ) -> CodeRepositoryRecord | None:
        async with self._session_factory() as session:
            record = await session.get(
                CodeRepositoryRecord,
                (tenant_id.value, repository_id.value),
            )
            if record is not None:
                session.expunge(record)
            return record

    async def enqueue_scan(
        self,
        *,
        tenant_id: TenantId,
        scan_id: CodeScanId,
        repository_id: CodeRepositoryId,
        source: CodeScanSource,
        source_label: str,
        branch: str | None,
        base_branch: str | None,
        head_branch: str | None,
        content_hash: str,
        webhook_delivery_id: str | None,
        queued_at: datetime,
    ) -> CodeScanRecord:
        timestamp = normalize_utc(queued_at, field_name="code_scan.queued_at")
        record = CodeScanRecord(
            tenant_id=tenant_id.value,
            id=scan_id.value,
            repository_id=repository_id.value,
            source=source.value,
            status=CodeScanStatus.QUEUED.value,
            phase=CodeScanPhase.RECEIVED.value,
            gate_decision=None,
            branch=branch,
            base_branch=base_branch,
            head_branch=head_branch,
            commit_sha=None,
            source_label=source_label,
            content_hash=content_hash,
            total_findings=0,
            critical_findings=0,
            high_findings=0,
            medium_findings=0,
            low_findings=0,
            info_findings=0,
            files_scanned=0,
            bytes_scanned=0,
            ruleset_version=DEFAULT_RULE_SET_VERSION.value,
            safe_ai_summary=None,
            safe_error=None,
            webhook_delivery_id=webhook_delivery_id,
            attempt_count=0,
            available_at=timestamp,
            last_attempt_at=None,
            lease_token=None,
            lease_expires_at=None,
            created_at=timestamp,
            updated_at=timestamp,
            started_at=None,
            finished_at=None,
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(record)
            session.expunge(record)
        return record

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
    ) -> None:
        del actor_id  # Audit identity is already authenticated; direct scans are immutable.
        timestamp = normalize_utc(completed_at, field_name="code_scan.completed_at")
        summary = SecuritySummary.from_findings(findings)
        scan_record = _completed_scan_record(
            tenant_id=tenant_id,
            scan_id=scan_id,
            source=source,
            source_label=source_label,
            content_hash=content_hash,
            summary=summary,
            gate_decision=gate_decision,
            files_scanned=files_scanned,
            bytes_scanned=bytes_scanned,
            completed_at=timestamp,
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(scan_record)
                session.add_all(
                    _finding_records(
                        tenant_id=tenant_id,
                        scan_id=scan_id,
                        findings=findings,
                        created_at=timestamp,
                    )
                )

    async def list_scans(
        self,
        *,
        tenant_id: TenantId,
        limit: int = 100,
    ) -> tuple[CodeScanRecord, ...]:
        async with self._session_factory() as session:
            records = tuple(
                (
                    await session.scalars(
                        select(CodeScanRecord)
                        .where(CodeScanRecord.tenant_id == tenant_id.value)
                        .order_by(CodeScanRecord.created_at.desc(), CodeScanRecord.id.desc())
                        .limit(limit)
                    )
                ).all()
            )
            for record in records:
                session.expunge(record)
            return records

    async def get_dashboard(
        self,
        *,
        tenant_id: TenantId,
        recent_limit: int = 10,
        trend_days: int = 7,
    ) -> CodeSecurityDashboardData:
        """Aggregate the tenant dashboard in bounded SQL queries, never N+1 reads."""
        async with self._session_factory() as session:
            count_row = (
                await session.execute(
                    select(
                        func.count(CodeScanRecord.id),
                        func.sum(
                            case(
                                (CodeScanRecord.gate_decision == GateDecision.APPROVE.value, 1),
                                else_=0,
                            )
                        ),
                        func.sum(
                            case(
                                (
                                    CodeScanRecord.gate_decision
                                    == GateDecision.REVIEW_REQUIRED.value,
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        func.sum(
                            case(
                                (CodeScanRecord.gate_decision == GateDecision.BLOCK.value, 1),
                                else_=0,
                            )
                        ),
                        func.sum(
                            case(
                                (CodeScanRecord.status == CodeScanStatus.FAILED.value, 1),
                                else_=0,
                            )
                        ),
                        func.coalesce(func.sum(CodeScanRecord.critical_findings), 0),
                        func.coalesce(func.sum(CodeScanRecord.high_findings), 0),
                        func.coalesce(func.sum(CodeScanRecord.medium_findings), 0),
                        func.coalesce(func.sum(CodeScanRecord.low_findings), 0),
                        func.coalesce(func.sum(CodeScanRecord.info_findings), 0),
                        func.coalesce(func.sum(CodeScanRecord.total_findings), 0),
                        func.coalesce(func.sum(CodeScanRecord.files_scanned), 0),
                        select(func.count(CodeRepositoryRecord.id))
                        .where(CodeRepositoryRecord.tenant_id == tenant_id.value)
                        .scalar_subquery(),
                    ).where(CodeScanRecord.tenant_id == tenant_id.value)
                )
            ).one()
            count_names = (
                "total_scans",
                "approved",
                "review_required",
                "blocked",
                "failed",
                "critical",
                "high",
                "medium",
                "low",
                "info",
                "vulnerabilities_found",
                "files_scanned",
                "repositories",
            )
            counts = {
                name: int(value or 0) for name, value in zip(count_names, count_row, strict=True)
            }
            recent_scans = tuple(
                (
                    await session.scalars(
                        select(CodeScanRecord)
                        .where(CodeScanRecord.tenant_id == tenant_id.value)
                        .order_by(CodeScanRecord.created_at.desc(), CodeScanRecord.id.desc())
                        .limit(recent_limit)
                    )
                ).all()
            )
            top_rows = (
                await session.execute(
                    select(
                        CodeSecurityFindingRecord.rule_id,
                        CodeSecurityFindingRecord.severity,
                        CodeSecurityFindingRecord.cwe_id,
                        func.count(CodeSecurityFindingRecord.id).label("finding_count"),
                    )
                    .where(CodeSecurityFindingRecord.tenant_id == tenant_id.value)
                    .group_by(
                        CodeSecurityFindingRecord.rule_id,
                        CodeSecurityFindingRecord.severity,
                        CodeSecurityFindingRecord.cwe_id,
                    )
                    .order_by(func.count(CodeSecurityFindingRecord.id).desc())
                    .limit(10)
                )
            ).all()
            trend_rows = (
                await session.execute(
                    select(
                        func.date(CodeScanRecord.created_at).label("scan_date"),
                        func.count(CodeScanRecord.id),
                        func.sum(CodeScanRecord.critical_findings),
                        func.sum(CodeScanRecord.high_findings),
                        func.sum(CodeScanRecord.medium_findings),
                        func.sum(CodeScanRecord.low_findings),
                        func.sum(CodeScanRecord.info_findings),
                    )
                    .where(CodeScanRecord.tenant_id == tenant_id.value)
                    .group_by(func.date(CodeScanRecord.created_at))
                    .order_by(func.date(CodeScanRecord.created_at).desc())
                    .limit(trend_days)
                )
            ).all()
            top_vulnerabilities = tuple(
                (str(row[0]), str(row[1]), row[2], int(row[3])) for row in top_rows
            )
            trend = tuple(
                (
                    str(row[0]),
                    int(row[1] or 0),
                    int(row[2] or 0),
                    int(row[3] or 0),
                    int(row[4] or 0),
                    int(row[5] or 0),
                    int(row[6] or 0),
                )
                for row in reversed(trend_rows)
            )
            for record in recent_scans:
                session.expunge(record)
            return CodeSecurityDashboardData(
                counts=counts,
                recent_scans=recent_scans,
                top_vulnerabilities=top_vulnerabilities,
                trend=trend,
            )

    async def get_scan_detail(
        self,
        *,
        tenant_id: TenantId,
        scan_id: CodeScanId,
    ) -> ScanDetail | None:
        async with self._session_factory() as session:
            scan = await session.get(CodeScanRecord, (tenant_id.value, scan_id.value))
            if scan is None:
                return None
            findings = tuple(
                (
                    await session.scalars(
                        select(CodeSecurityFindingRecord)
                        .where(
                            CodeSecurityFindingRecord.tenant_id == tenant_id.value,
                            CodeSecurityFindingRecord.scan_id == scan_id.value,
                        )
                        .order_by(
                            CodeSecurityFindingRecord.file_path,
                            CodeSecurityFindingRecord.line_number,
                            CodeSecurityFindingRecord.id,
                        )
                    )
                ).all()
            )
            review = await session.scalar(
                select(CodeScanReviewRecord).where(
                    CodeScanReviewRecord.tenant_id == tenant_id.value,
                    CodeScanReviewRecord.scan_id == scan_id.value,
                )
            )
            session.expunge(scan)
            for finding in findings:
                session.expunge(finding)
            if review is not None:
                session.expunge(review)
            return ScanDetail(scan=scan, findings=findings, review=review)

    async def get_finding(
        self,
        *,
        tenant_id: TenantId,
        finding_id: SecurityFindingId,
    ) -> CodeSecurityFindingRecord | None:
        async with self._session_factory() as session:
            record = await session.get(
                CodeSecurityFindingRecord,
                (tenant_id.value, finding_id.value),
            )
            if record is not None:
                session.expunge(record)
            return record

    async def add_review(
        self,
        *,
        tenant_id: TenantId,
        review_id: SecurityReviewId,
        scan_id: CodeScanId,
        decision: SecurityReviewDecision,
        safe_note: str | None,
        actor_id: UserId,
        reviewed_at: datetime,
    ) -> CodeScanReviewRecord:
        timestamp = normalize_utc(reviewed_at, field_name="code_scan_review.reviewed_at")
        record = CodeScanReviewRecord(
            tenant_id=tenant_id.value,
            id=review_id.value,
            scan_id=scan_id.value,
            required_gate_decision=GateDecision.REVIEW_REQUIRED.value,
            decision=decision.value,
            safe_note=safe_note,
            reviewed_by=actor_id.value,
            reviewed_at=timestamp,
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(record)
            session.expunge(record)
        return record

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedCodeScan | None:
        claimed_at = normalize_utc(now, field_name="code_scan_claim.claimed_at")
        async with self._session_factory() as session:
            async with session.begin():
                scan = await session.scalar(
                    select(CodeScanRecord)
                    .join(
                        CodeRepositoryRecord,
                        and_(
                            CodeRepositoryRecord.tenant_id == CodeScanRecord.tenant_id,
                            CodeRepositoryRecord.id == CodeScanRecord.repository_id,
                        ),
                    )
                    .where(
                        or_(
                            and_(
                                CodeScanRecord.status == CodeScanStatus.QUEUED.value,
                                CodeScanRecord.available_at <= claimed_at,
                            ),
                            and_(
                                CodeScanRecord.status == CodeScanStatus.RUNNING.value,
                                CodeScanRecord.lease_expires_at <= claimed_at,
                            ),
                        ),
                        CodeRepositoryRecord.enabled.is_(True),
                    )
                    .order_by(CodeScanRecord.available_at, CodeScanRecord.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if scan is None or scan.repository_id is None:
                    return None
                repository = await session.get(
                    CodeRepositoryRecord,
                    (scan.tenant_id, scan.repository_id),
                )
                if repository is None:
                    return None
                lease_token = uuid4()
                scan.status = CodeScanStatus.RUNNING.value
                scan.phase = CodeScanPhase.VALIDATING.value
                scan.attempt_count += 1
                scan.last_attempt_at = claimed_at
                scan.lease_token = lease_token
                scan.lease_expires_at = claimed_at + lease_duration
                scan.started_at = scan.started_at or claimed_at
                scan.updated_at = claimed_at
                return ClaimedCodeScan(
                    tenant_id=TenantId(scan.tenant_id),
                    scan_id=CodeScanId(scan.id),
                    repository_id=CodeRepositoryId(scan.repository_id),
                    source=CodeScanSource(scan.source),
                    clone_url=repository.clone_url,
                    branch=scan.branch,
                    base_branch=scan.base_branch,
                    head_branch=scan.head_branch,
                    lease_token=lease_token,
                    attempt_count=scan.attempt_count,
                )

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
    ) -> bool:
        timestamp = normalize_utc(completed_at, field_name="code_scan.completed_at")
        summary = SecuritySummary.from_findings(findings)
        async with self._session_factory() as session:
            async with session.begin():
                scan = await _lock_owned_scan(session=session, job=job, at=timestamp)
                if scan is None:
                    return False
                _apply_summary(record=scan, summary=summary)
                scan.status = CodeScanStatus.SUCCEEDED.value
                scan.phase = CodeScanPhase.COMPLETED.value
                scan.gate_decision = gate_decision.value
                scan.commit_sha = commit_sha
                scan.files_scanned = files_scanned
                scan.bytes_scanned = bytes_scanned
                scan.safe_ai_summary = safe_ai_summary
                scan.safe_error = None
                scan.lease_token = None
                scan.lease_expires_at = None
                scan.finished_at = timestamp
                scan.updated_at = timestamp
                session.add_all(
                    _finding_records(
                        tenant_id=job.tenant_id,
                        scan_id=job.scan_id,
                        findings=findings,
                        created_at=timestamp,
                    )
                )
                return True

    async def advance_claimed_phase(
        self,
        *,
        job: ClaimedCodeScan,
        phase: CodeScanPhase,
        updated_at: datetime,
    ) -> bool:
        """Persist non-secret progress while preserving lease fencing."""
        if phase not in {
            CodeScanPhase.PARSING,
            CodeScanPhase.DETECTING,
            CodeScanPhase.DEDUPLICATING,
            CodeScanPhase.REDACTING,
            CodeScanPhase.GATING,
        }:
            raise ValueError("Code scan progress phase is invalid")
        timestamp = normalize_utc(updated_at, field_name="code_scan_phase.updated_at")
        async with self._session_factory() as session:
            async with session.begin():
                scan = await _lock_owned_scan(session=session, job=job, at=timestamp)
                if scan is None:
                    return False
                scan.phase = phase.value
                scan.updated_at = timestamp
                return True

    async def reschedule_claimed(
        self,
        *,
        job: ClaimedCodeScan,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool:
        attempt_time = normalize_utc(attempted_at, field_name="code_scan.attempted_at")
        next_time = normalize_utc(available_at, field_name="code_scan.available_at")
        async with self._session_factory() as session:
            async with session.begin():
                scan = await _lock_owned_scan(session=session, job=job, at=attempt_time)
                if scan is None:
                    return False
                scan.status = CodeScanStatus.QUEUED.value
                scan.phase = CodeScanPhase.RECEIVED.value
                scan.available_at = next_time
                scan.lease_token = None
                scan.lease_expires_at = None
                scan.updated_at = attempt_time
                return True

    async def fail_claimed(
        self,
        *,
        job: ClaimedCodeScan,
        safe_error: str,
        failed_at: datetime,
    ) -> bool:
        timestamp = normalize_utc(failed_at, field_name="code_scan.failed_at")
        async with self._session_factory() as session:
            async with session.begin():
                scan = await _lock_owned_scan(session=session, job=job, at=timestamp)
                if scan is None:
                    return False
                scan.status = CodeScanStatus.FAILED.value
                scan.phase = CodeScanPhase.COMPLETED.value
                scan.safe_error = safe_error[:512]
                scan.lease_token = None
                scan.lease_expires_at = None
                scan.finished_at = timestamp
                scan.updated_at = timestamp
                return True


def _policy_from_record(record: CodeSecuritySettingsRecord | None) -> TenantCodeSecurityPolicy:
    if record is None:
        return TenantCodeSecurityPolicy()
    return TenantCodeSecurityPolicy(
        revision=record.revision,
        block_severity=SecuritySeverity(record.block_severity),
        review_severity=SecuritySeverity(record.review_severity),
        max_file_bytes=record.max_file_bytes,
        max_repository_files=record.max_repository_files,
        max_repository_bytes=record.max_repository_bytes,
        auto_scan_push=record.auto_scan_push,
        auto_scan_pull_request=record.auto_scan_pull_request,
        ai_summary_enabled=record.ai_summary_enabled,
    )


def _apply_policy(
    *,
    record: CodeSecuritySettingsRecord,
    policy: TenantCodeSecurityPolicy,
    actor_id: UserId,
    updated_at: datetime,
) -> None:
    record.revision = policy.revision
    record.block_severity = policy.block_severity.value
    record.review_severity = policy.review_severity.value
    record.max_file_bytes = policy.max_file_bytes
    record.max_repository_files = policy.max_repository_files
    record.max_repository_bytes = policy.max_repository_bytes
    record.auto_scan_push = policy.auto_scan_push
    record.auto_scan_pull_request = policy.auto_scan_pull_request
    record.ai_summary_enabled = policy.ai_summary_enabled
    record.updated_by = actor_id.value
    record.updated_at = updated_at


def _completed_scan_record(
    *,
    tenant_id: TenantId,
    scan_id: CodeScanId,
    source: CodeScanSource,
    source_label: str,
    content_hash: str,
    summary: SecuritySummary,
    gate_decision: GateDecision,
    files_scanned: int,
    bytes_scanned: int,
    completed_at: datetime,
) -> CodeScanRecord:
    record = CodeScanRecord(
        tenant_id=tenant_id.value,
        id=scan_id.value,
        repository_id=None,
        source=source.value,
        status=CodeScanStatus.SUCCEEDED.value,
        phase=CodeScanPhase.COMPLETED.value,
        gate_decision=gate_decision.value,
        branch=None,
        base_branch=None,
        head_branch=None,
        commit_sha=None,
        source_label=source_label,
        content_hash=content_hash,
        total_findings=summary.total_findings,
        critical_findings=summary.critical,
        high_findings=summary.high,
        medium_findings=summary.medium,
        low_findings=summary.low,
        info_findings=summary.info,
        files_scanned=files_scanned,
        bytes_scanned=bytes_scanned,
        ruleset_version=DEFAULT_RULE_SET_VERSION.value,
        safe_ai_summary=None,
        safe_error=None,
        webhook_delivery_id=None,
        attempt_count=1,
        available_at=completed_at,
        last_attempt_at=completed_at,
        lease_token=None,
        lease_expires_at=None,
        created_at=completed_at,
        updated_at=completed_at,
        started_at=completed_at,
        finished_at=completed_at,
    )
    return record


def _apply_summary(*, record: CodeScanRecord, summary: SecuritySummary) -> None:
    record.total_findings = summary.total_findings
    record.critical_findings = summary.critical
    record.high_findings = summary.high
    record.medium_findings = summary.medium
    record.low_findings = summary.low
    record.info_findings = summary.info


def _finding_records(
    *,
    tenant_id: TenantId,
    scan_id: CodeScanId,
    findings: tuple[SecurityFinding, ...],
    created_at: datetime,
) -> list[CodeSecurityFindingRecord]:
    return [
        CodeSecurityFindingRecord(
            tenant_id=tenant_id.value,
            id=scan_scoped_finding_id(
                scan_id=scan_id,
                fingerprint=finding.fingerprint,
            ).value,
            scan_id=scan_id.value,
            fingerprint=finding.fingerprint,
            rule_id=finding.rule_id,
            severity=finding.severity.value,
            detector=finding.detector.value,
            cwe_id=finding.cwe_id,
            owasp_category=_OWASP_BY_CWE.get(finding.cwe_id or ""),
            safe_message=finding.safe_message,
            file_path=finding.file_path,
            line_number=finding.line_number,
            column_number=finding.column_number,
            redacted_code_snippet=finding.code_snippet,
            recommendation=finding.recommendation,
            created_at=created_at,
        )
        for finding in findings
    ]


async def _lock_owned_scan(
    *,
    session: AsyncSession,
    job: ClaimedCodeScan,
    at: datetime,
) -> CodeScanRecord | None:
    return await session.scalar(
        select(CodeScanRecord)
        .where(
            CodeScanRecord.tenant_id == job.tenant_id.value,
            CodeScanRecord.id == job.scan_id.value,
            CodeScanRecord.status == CodeScanStatus.RUNNING.value,
            CodeScanRecord.lease_token == job.lease_token,
            CodeScanRecord.lease_expires_at > at,
        )
        .with_for_update()
    )
