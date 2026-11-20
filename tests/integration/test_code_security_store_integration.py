from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.application.code_security import TenantCodeSecurityPolicy
from enterprise_chat_platform.domain.code_security import (
    CodeRepositoryProvider,
    CodeScanPhase,
    CodeScanSource,
    GateDecision,
    RuleSetVersion,
    SecurityDetector,
    SecurityFinding,
    SecurityReviewDecision,
    SecuritySeverity,
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
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    CodeRepositoryRecord,
    CodeScanRecord,
    CodeScanReviewRecord,
    CodeSecurityFindingRecord,
    CodeSecuritySettingsRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    SqlAlchemyCodeSecurityStore,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
ACTOR_ID = UserId.parse("77777777-7777-4777-8777-777777777777")
CONTENT_HASH = "a" * 64
COMMIT_SHA = "b" * 40


@asynccontextmanager
async def _database_case(
    *tenant_ids: TenantId,
) -> AsyncIterator[SqlAlchemyCodeSecurityStore]:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())
    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
    assert (runtime_url.host, runtime_url.port, runtime_url.database) == (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    )

    runtime_engine = create_database_engine(runtime_settings)
    migration_engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    session_factory = create_session_factory(runtime_engine)
    await _cleanup(migration_engine, tenant_ids)
    try:
        yield SqlAlchemyCodeSecurityStore(session_factory)
    finally:
        await _cleanup(migration_engine, tenant_ids)
        await runtime_engine.dispose()
        await migration_engine.dispose()


async def _cleanup(engine: AsyncEngine, tenant_ids: tuple[TenantId, ...]) -> None:
    values = tuple(tenant_id.value for tenant_id in tenant_ids)
    if not values:
        return
    async with engine.begin() as connection:
        await connection.execute(
            delete(CodeScanReviewRecord).where(CodeScanReviewRecord.tenant_id.in_(values))
        )
        await connection.execute(
            delete(CodeSecurityFindingRecord).where(CodeSecurityFindingRecord.tenant_id.in_(values))
        )
        await connection.execute(delete(CodeScanRecord).where(CodeScanRecord.tenant_id.in_(values)))
        await connection.execute(
            delete(CodeSecuritySettingsRecord).where(
                CodeSecuritySettingsRecord.tenant_id.in_(values)
            )
        )
        await connection.execute(
            delete(CodeRepositoryRecord).where(CodeRepositoryRecord.tenant_id.in_(values))
        )


def _tenant_id() -> TenantId:
    return TenantId(uuid4())


def _finding(
    *,
    finding_id: SecurityFindingId | None = None,
    severity: SecuritySeverity = SecuritySeverity.CRITICAL,
    file_path: str = "src/settings.py",
    line_number: int = 7,
    fingerprint_seed: str = "hardcoded-secret",
) -> SecurityFinding:
    return SecurityFinding(
        finding_id=finding_id or SecurityFindingId.new(),
        fingerprint=hashlib.sha256(fingerprint_seed.encode()).hexdigest(),
        rule_id="hardcoded_secret",
        rule_set_version=RuleSetVersion("day10.v1"),
        severity=severity,
        detector=SecurityDetector.AST,
        safe_message="A credential-like value is hardcoded in source",
        recommendation="Load secrets from an approved secret manager",
        file_path=file_path,
        line_number=line_number,
        column_number=0,
        code_snippet="api_token = '<redacted>'",
        cwe_id="CWE-798",
    )


async def _add_repository(
    store: SqlAlchemyCodeSecurityStore,
    *,
    tenant_id: TenantId,
    repository_id: CodeRepositoryId | None = None,
    name: str = "Secure Service",
    path: str | None = None,
) -> CodeRepositoryId:
    resolved_id = repository_id or CodeRepositoryId.new()
    resolved_path = path or str(resolved_id)
    await store.add_repository(
        tenant_id=tenant_id,
        repository_id=resolved_id,
        provider=CodeRepositoryProvider.GITHUB,
        display_name=name,
        clone_url=f"https://github.com/example/{resolved_path}.git",
        normalized_url=f"https://github.com/example/{resolved_path}",
        default_branch="main",
        actor_id=ACTOR_ID,
        created_at=BASE_TIME,
    )
    return resolved_id


async def _enqueue_repository_scan(
    store: SqlAlchemyCodeSecurityStore,
    *,
    tenant_id: TenantId,
    repository_id: CodeRepositoryId,
    scan_id: CodeScanId | None = None,
    queued_at: datetime = BASE_TIME,
    webhook_delivery_id: str | None = None,
    source: CodeScanSource = CodeScanSource.REPOSITORY,
) -> CodeScanId:
    resolved_scan_id = scan_id or CodeScanId.new()
    await store.enqueue_scan(
        tenant_id=tenant_id,
        scan_id=resolved_scan_id,
        repository_id=repository_id,
        source=source,
        source_label="Secure Service:main",
        branch="main",
        base_branch=None,
        head_branch=None,
        content_hash=CONTENT_HASH,
        webhook_delivery_id=webhook_delivery_id,
        queued_at=queued_at,
    )
    return resolved_scan_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_direct_scan_persists_redacted_findings_with_strict_tenant_isolation() -> None:
    tenant_a = _tenant_id()
    tenant_b = _tenant_id()
    tenant_without_data = _tenant_id()
    shared_scan_id = CodeScanId.new()
    repeated_scan_id = CodeScanId.new()
    shared_finding_id = SecurityFindingId.new()
    finding_a = _finding(
        finding_id=shared_finding_id,
        fingerprint_seed="tenant-a",
    )
    finding_b = _finding(
        finding_id=shared_finding_id,
        file_path="services/worker.py",
        fingerprint_seed="tenant-b",
    )

    async with (
        _database_case(tenant_a, tenant_b, tenant_without_data) as store,
        asyncio.timeout(10),
    ):
        for tenant_id, finding in ((tenant_a, finding_a), (tenant_b, finding_b)):
            await store.save_completed_scan(
                tenant_id=tenant_id,
                actor_id=ACTOR_ID,
                scan_id=shared_scan_id,
                source=CodeScanSource.SNIPPET,
                source_label=finding.file_path,
                content_hash=CONTENT_HASH,
                findings=(finding,),
                gate_decision=GateDecision.BLOCK,
                files_scanned=1,
                bytes_scanned=42,
                completed_at=BASE_TIME,
            )
        await store.save_completed_scan(
            tenant_id=tenant_a,
            actor_id=ACTOR_ID,
            scan_id=repeated_scan_id,
            source=CodeScanSource.FILE_UPLOAD,
            source_label="settings-copy.py",
            content_hash=CONTENT_HASH,
            findings=(finding_a,),
            gate_decision=GateDecision.BLOCK,
            files_scanned=1,
            bytes_scanned=42,
            completed_at=BASE_TIME + timedelta(seconds=1),
        )

        detail_a = await store.get_scan_detail(
            tenant_id=tenant_a,
            scan_id=shared_scan_id,
        )
        detail_b = await store.get_scan_detail(
            tenant_id=tenant_b,
            scan_id=shared_scan_id,
        )
        missing = await store.get_scan_detail(
            tenant_id=tenant_without_data,
            scan_id=shared_scan_id,
        )
        repeated_detail = await store.get_scan_detail(
            tenant_id=tenant_a,
            scan_id=repeated_scan_id,
        )
        assert detail_a is not None
        assert detail_b is not None
        assert repeated_detail is not None
        finding_for_a = await store.get_finding(
            tenant_id=tenant_a,
            finding_id=SecurityFindingId(detail_a.findings[0].id),
        )
        finding_for_b = await store.get_finding(
            tenant_id=tenant_b,
            finding_id=SecurityFindingId(detail_b.findings[0].id),
        )

        assert missing is None
        assert detail_a.scan.total_findings == 1
        assert detail_a.scan.critical_findings == 1
        assert detail_a.scan.gate_decision == GateDecision.BLOCK.value
        assert detail_a.findings[0].file_path == "src/settings.py"
        assert detail_b.findings[0].file_path == "services/worker.py"
        assert finding_for_a is not None
        assert finding_for_b is not None
        assert finding_for_a.redacted_code_snippet == "api_token = '<redacted>'"
        assert finding_for_b.redacted_code_snippet == "api_token = '<redacted>'"
        assert detail_a.findings[0].fingerprint == repeated_detail.findings[0].fingerprint
        assert detail_a.findings[0].id != repeated_detail.findings[0].id
        assert (
            detail_a.findings[0].id
            == scan_scoped_finding_id(
                scan_id=shared_scan_id,
                fingerprint=finding_a.fingerprint,
            ).value
        )
        assert (
            repeated_detail.findings[0].id
            == scan_scoped_finding_id(
                scan_id=repeated_scan_id,
                fingerprint=finding_a.fingerprint,
            ).value
        )
        assert "super-secret" not in repr(detail_a)
        assert "super-secret" not in repr(detail_b)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_job_claim_is_fenced_and_completion_is_atomic() -> None:
    tenant_id = _tenant_id()
    other_tenant = _tenant_id()

    async with _database_case(tenant_id, other_tenant) as store, asyncio.timeout(10):
        repository_id = await _add_repository(store, tenant_id=tenant_id)
        scan_id = await _enqueue_repository_scan(
            store,
            tenant_id=tenant_id,
            repository_id=repository_id,
        )

        assert (
            await store.get_repository(
                tenant_id=other_tenant,
                repository_id=repository_id,
            )
            is None
        )
        job = await store.claim_next(now=BASE_TIME, lease_duration=timedelta(minutes=2))
        assert job is not None
        assert job.scan_id == scan_id
        assert job.attempt_count == 1

        forged_job = replace(job, lease_token=uuid4())
        assert not await store.advance_claimed_phase(
            job=forged_job,
            phase=CodeScanPhase.DETECTING,
            updated_at=BASE_TIME + timedelta(seconds=1),
        )
        assert await store.advance_claimed_phase(
            job=job,
            phase=CodeScanPhase.DETECTING,
            updated_at=BASE_TIME + timedelta(seconds=1),
        )
        running = await store.get_scan_detail(tenant_id=tenant_id, scan_id=scan_id)
        assert running is not None
        assert running.scan.phase == CodeScanPhase.DETECTING.value
        assert (
            await store.complete_claimed(
                job=forged_job,
                findings=(_finding(),),
                gate_decision=GateDecision.BLOCK,
                commit_sha=COMMIT_SHA,
                files_scanned=3,
                bytes_scanned=1_024,
                safe_ai_summary="One critical finding requires remediation.",
                completed_at=BASE_TIME + timedelta(seconds=30),
            )
            is False
        )

        finding = _finding()
        assert (
            await store.complete_claimed(
                job=job,
                findings=(finding,),
                gate_decision=GateDecision.BLOCK,
                commit_sha=COMMIT_SHA,
                files_scanned=3,
                bytes_scanned=1_024,
                safe_ai_summary="One critical finding requires remediation.",
                completed_at=BASE_TIME + timedelta(seconds=30),
            )
            is True
        )
        assert (
            await store.complete_claimed(
                job=job,
                findings=(),
                gate_decision=GateDecision.APPROVE,
                commit_sha=COMMIT_SHA,
                files_scanned=0,
                bytes_scanned=0,
                safe_ai_summary=None,
                completed_at=BASE_TIME + timedelta(seconds=31),
            )
            is False
        )

        detail = await store.get_scan_detail(tenant_id=tenant_id, scan_id=scan_id)
        assert detail is not None
        assert detail.scan.status == "succeeded"
        assert detail.scan.commit_sha == COMMIT_SHA
        assert detail.scan.attempt_count == 1
        assert detail.scan.lease_token is None
        assert detail.scan.total_findings == 1
        assert tuple(record.id for record in detail.findings) == (
            scan_scoped_finding_id(
                scan_id=scan_id,
                fingerprint=finding.fingerprint,
            ).value,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_disabled_repository_job_does_not_starve_enabled_queue() -> None:
    tenant_id = _tenant_id()

    async with _database_case(tenant_id) as store, asyncio.timeout(10):
        disabled_repository = await _add_repository(
            store,
            tenant_id=tenant_id,
            name="Disabled first",
        )
        enabled_repository = await _add_repository(
            store,
            tenant_id=tenant_id,
            name="Enabled second",
        )
        await _enqueue_repository_scan(
            store,
            tenant_id=tenant_id,
            repository_id=disabled_repository,
            queued_at=BASE_TIME,
        )
        enabled_scan = await _enqueue_repository_scan(
            store,
            tenant_id=tenant_id,
            repository_id=enabled_repository,
            queued_at=BASE_TIME + timedelta(seconds=1),
        )

        runtime_engine = create_database_engine(Settings())
        session_factory = create_session_factory(runtime_engine)
        try:
            async with session_factory() as session:
                async with session.begin():
                    record = await session.get(
                        CodeRepositoryRecord,
                        (tenant_id.value, disabled_repository.value),
                    )
                    assert record is not None
                    record.enabled = False
        finally:
            await runtime_engine.dispose()

        claimed = await store.claim_next(
            now=BASE_TIME + timedelta(seconds=2),
            lease_duration=timedelta(minutes=2),
        )

        assert claimed is not None
        assert claimed.scan_id == enabled_scan
        assert claimed.repository_id == enabled_repository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_retry_releases_lease_then_terminal_failure_is_fenced() -> None:
    tenant_id = _tenant_id()

    async with _database_case(tenant_id) as store, asyncio.timeout(10):
        repository_id = await _add_repository(store, tenant_id=tenant_id)
        scan_id = await _enqueue_repository_scan(
            store,
            tenant_id=tenant_id,
            repository_id=repository_id,
        )
        first_job = await store.claim_next(
            now=BASE_TIME,
            lease_duration=timedelta(minutes=2),
        )
        assert first_job is not None

        retry_at = BASE_TIME + timedelta(minutes=3)
        assert await store.reschedule_claimed(
            job=first_job,
            available_at=retry_at,
            attempted_at=BASE_TIME + timedelta(seconds=30),
        )
        assert (
            await store.claim_next(
                now=retry_at - timedelta(microseconds=1),
                lease_duration=timedelta(minutes=2),
            )
            is None
        )
        assert (
            await store.fail_claimed(
                job=first_job,
                safe_error="A stale worker must not win",
                failed_at=BASE_TIME + timedelta(seconds=31),
            )
            is False
        )

        second_job = await store.claim_next(
            now=retry_at,
            lease_duration=timedelta(minutes=2),
        )
        assert second_job is not None
        assert second_job.scan_id == scan_id
        assert second_job.attempt_count == 2
        assert second_job.lease_token != first_job.lease_token
        assert await store.fail_claimed(
            job=second_job,
            safe_error="x" * 600,
            failed_at=retry_at + timedelta(seconds=30),
        )

        detail = await store.get_scan_detail(tenant_id=tenant_id, scan_id=scan_id)
        assert detail is not None
        assert detail.scan.status == "failed"
        assert detail.scan.phase == "completed"
        assert detail.scan.attempt_count == 2
        assert detail.scan.safe_error == "x" * 512
        assert detail.scan.lease_token is None
        assert detail.scan.finished_at == retry_at + timedelta(seconds=30)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_policy_updates_use_optimistic_revision_and_remain_tenant_scoped() -> None:
    tenant_a = _tenant_id()
    tenant_b = _tenant_id()
    desired = TenantCodeSecurityPolicy(
        block_severity=SecuritySeverity.HIGH,
        review_severity=SecuritySeverity.MEDIUM,
        max_file_bytes=2_000_000,
        max_repository_files=3_000,
        max_repository_bytes=60_000_000,
        auto_scan_push=False,
        auto_scan_pull_request=True,
        ai_summary_enabled=True,
    )

    async with _database_case(tenant_a, tenant_b) as store, asyncio.timeout(10):
        assert (await store.get_policy(tenant_id=tenant_a)).revision == 0
        updated = await store.update_policy(
            tenant_id=tenant_a,
            actor_id=ACTOR_ID,
            expected_revision=0,
            policy=desired,
            updated_at=BASE_TIME,
        )
        stale = await store.update_policy(
            tenant_id=tenant_a,
            actor_id=ACTOR_ID,
            expected_revision=0,
            policy=replace(desired, auto_scan_push=True),
            updated_at=BASE_TIME + timedelta(seconds=1),
        )

        assert updated == replace(desired, revision=1)
        assert stale is None
        assert await store.get_policy(tenant_id=tenant_a) == updated
        assert await store.get_policy(tenant_id=tenant_b) == TenantCodeSecurityPolicy()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_review_requires_review_gate_and_allows_exactly_one_decision() -> None:
    tenant_id = _tenant_id()
    other_tenant = _tenant_id()
    review_scan_id = CodeScanId.new()
    blocked_scan_id = CodeScanId.new()

    async with _database_case(tenant_id, other_tenant) as store, asyncio.timeout(10):
        for scan_id, decision in (
            (review_scan_id, GateDecision.REVIEW_REQUIRED),
            (blocked_scan_id, GateDecision.BLOCK),
        ):
            await store.save_completed_scan(
                tenant_id=tenant_id,
                actor_id=ACTOR_ID,
                scan_id=scan_id,
                source=CodeScanSource.FILE_UPLOAD,
                source_label=f"{scan_id}.py",
                content_hash=CONTENT_HASH,
                findings=(
                    _finding(
                        severity=SecuritySeverity.HIGH,
                        fingerprint_seed=str(scan_id),
                    ),
                ),
                gate_decision=decision,
                files_scanned=1,
                bytes_scanned=64,
                completed_at=BASE_TIME,
            )

        review = await store.add_review(
            tenant_id=tenant_id,
            review_id=SecurityReviewId.new(),
            scan_id=review_scan_id,
            decision=SecurityReviewDecision.APPROVED,
            safe_note="Approved after compensating controls were verified.",
            actor_id=ACTOR_ID,
            reviewed_at=BASE_TIME + timedelta(minutes=1),
        )
        detail = await store.get_scan_detail(tenant_id=tenant_id, scan_id=review_scan_id)
        assert detail is not None
        assert detail.review is not None
        assert detail.review.id == review.id

        with pytest.raises(IntegrityError):
            await store.add_review(
                tenant_id=tenant_id,
                review_id=SecurityReviewId.new(),
                scan_id=review_scan_id,
                decision=SecurityReviewDecision.BLOCKED,
                safe_note=None,
                actor_id=ACTOR_ID,
                reviewed_at=BASE_TIME + timedelta(minutes=2),
            )
        with pytest.raises(IntegrityError):
            await store.add_review(
                tenant_id=tenant_id,
                review_id=SecurityReviewId.new(),
                scan_id=blocked_scan_id,
                decision=SecurityReviewDecision.BLOCKED,
                safe_note=None,
                actor_id=ACTOR_ID,
                reviewed_at=BASE_TIME + timedelta(minutes=2),
            )
        with pytest.raises(IntegrityError):
            await store.add_review(
                tenant_id=other_tenant,
                review_id=SecurityReviewId.new(),
                scan_id=review_scan_id,
                decision=SecurityReviewDecision.BLOCKED,
                safe_note=None,
                actor_id=ACTOR_ID,
                reviewed_at=BASE_TIME + timedelta(minutes=2),
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webhook_delivery_is_idempotent_per_tenant_repository() -> None:
    tenant_a = _tenant_id()
    tenant_b = _tenant_id()
    shared_repository_id = CodeRepositoryId.new()

    async with _database_case(tenant_a, tenant_b) as store, asyncio.timeout(10):
        for tenant_id in (tenant_a, tenant_b):
            await _add_repository(
                store,
                tenant_id=tenant_id,
                repository_id=shared_repository_id,
                path=f"shared-{tenant_id}",
            )

        first_scan_id = await _enqueue_repository_scan(
            store,
            tenant_id=tenant_a,
            repository_id=shared_repository_id,
            webhook_delivery_id="delivery-123",
            source=CodeScanSource.WEBHOOK,
        )
        with pytest.raises(IntegrityError):
            await _enqueue_repository_scan(
                store,
                tenant_id=tenant_a,
                repository_id=shared_repository_id,
                webhook_delivery_id="delivery-123",
                source=CodeScanSource.WEBHOOK,
            )

        second_tenant_scan_id = await _enqueue_repository_scan(
            store,
            tenant_id=tenant_b,
            repository_id=shared_repository_id,
            webhook_delivery_id="delivery-123",
            source=CodeScanSource.WEBHOOK,
        )
        scans_a = await store.list_scans(tenant_id=tenant_a)
        scans_b = await store.list_scans(tenant_id=tenant_b)

        assert tuple(record.id for record in scans_a) == (first_scan_id.value,)
        assert tuple(record.id for record in scans_b) == (second_tenant_scan_id.value,)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scan_queries_supply_bounded_dashboard_inputs_in_newest_first_order() -> None:
    tenant_id = _tenant_id()
    scan_ids = (CodeScanId.new(), CodeScanId.new(), CodeScanId.new())
    severities = (
        SecuritySeverity.LOW,
        SecuritySeverity.MEDIUM,
        SecuritySeverity.HIGH,
    )

    async with _database_case(tenant_id) as store, asyncio.timeout(10):
        for offset, (scan_id, severity) in enumerate(zip(scan_ids, severities, strict=True)):
            await store.save_completed_scan(
                tenant_id=tenant_id,
                actor_id=ACTOR_ID,
                scan_id=scan_id,
                source=CodeScanSource.CI,
                source_label=f"ci-{offset}.py",
                content_hash=hashlib.sha256(str(offset).encode()).hexdigest(),
                findings=(
                    _finding(
                        severity=severity,
                        file_path=f"src/module_{offset}.py",
                        line_number=offset + 1,
                        fingerprint_seed=f"dashboard-{offset}",
                    ),
                ),
                gate_decision=(
                    GateDecision.REVIEW_REQUIRED
                    if severity is SecuritySeverity.HIGH
                    else GateDecision.APPROVE
                ),
                files_scanned=1,
                bytes_scanned=100 + offset,
                completed_at=BASE_TIME + timedelta(minutes=offset),
            )

        newest_two = await store.list_scans(tenant_id=tenant_id, limit=2)
        dashboard_started = perf_counter()
        dashboard = await store.get_dashboard(
            tenant_id=tenant_id,
            recent_limit=2,
            trend_days=7,
        )
        assert perf_counter() - dashboard_started < 1
        detail = await store.get_scan_detail(
            tenant_id=tenant_id,
            scan_id=CodeScanId(newest_two[0].id),
        )

        assert tuple(record.id for record in newest_two) == (
            scan_ids[2].value,
            scan_ids[1].value,
        )
        assert sum(record.total_findings for record in newest_two) == 2
        assert sum(record.high_findings for record in newest_two) == 1
        assert sum(record.medium_findings for record in newest_two) == 1
        assert detail is not None
        assert detail.findings[0].file_path == "src/module_2.py"
        assert dashboard.counts == {
            "total_scans": 3,
            "approved": 2,
            "review_required": 1,
            "blocked": 0,
            "failed": 0,
            "critical": 0,
            "high": 1,
            "medium": 1,
            "low": 1,
            "info": 0,
            "vulnerabilities_found": 3,
            "files_scanned": 3,
            "repositories": 0,
        }
        assert tuple(record.id for record in dashboard.recent_scans) == (
            scan_ids[2].value,
            scan_ids[1].value,
        )
        assert set(dashboard.top_vulnerabilities) == {
            ("hardcoded_secret", "low", "CWE-798", 1),
            ("hardcoded_secret", "medium", "CWE-798", 1),
            ("hardcoded_secret", "high", "CWE-798", 1),
        }
        assert dashboard.trend == (("2026-01-10", 3, 0, 1, 1, 1, 0),)
