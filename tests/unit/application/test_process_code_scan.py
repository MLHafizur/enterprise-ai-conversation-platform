from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

import pytest

from enterprise_chat_platform.application.code_security import TenantCodeSecurityPolicy
from enterprise_chat_platform.application.code_security_ai import (
    LocalSecurityExplanationProvider,
    SecurityExplanationProvider,
)
from enterprise_chat_platform.application.process_code_scan import (
    CodeScanResultNotifier,
    CodeScanWorkerConfig,
    CodeScanWorkResult,
    ProcessCodeScanWorker,
)
from enterprise_chat_platform.domain.code_security import (
    CodeScanPhase,
    CodeScanSource,
    GateDecision,
)
from enterprise_chat_platform.domain.identifiers import (
    CodeRepositoryId,
    CodeScanId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    RepositoryScanError,
    RepositoryScanResult,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    ClaimedCodeScan,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def _job(attempt_count: int = 1) -> ClaimedCodeScan:
    return ClaimedCodeScan(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        scan_id=CodeScanId.parse("22222222-2222-4222-8222-222222222222"),
        repository_id=CodeRepositoryId.parse("33333333-3333-4333-8333-333333333333"),
        source=CodeScanSource.REPOSITORY,
        clone_url="https://github.com/acme/app.git",
        branch="main",
        base_branch=None,
        head_branch=None,
        lease_token=UUID("44444444-4444-4444-8444-444444444444"),
        attempt_count=attempt_count,
    )


class _Store:
    def __init__(self, job: ClaimedCodeScan | None) -> None:
        self.job = job
        self.completed = False
        self.rescheduled = False
        self.failed = False
        self.phases: list[CodeScanPhase] = []

    async def claim_next(self, **_: object) -> ClaimedCodeScan | None:
        return self.job

    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy:
        return TenantCodeSecurityPolicy()

    async def advance_claimed_phase(
        self,
        *,
        phase: CodeScanPhase,
        **_: object,
    ) -> bool:
        self.phases.append(phase)
        return True

    async def complete_claimed(self, **_: object) -> bool:
        self.completed = True
        return True

    async def reschedule_claimed(self, **_: object) -> bool:
        self.rescheduled = True
        return True

    async def fail_claimed(self, **_: object) -> bool:
        self.failed = True
        return True


class _Scanner:
    def __init__(self, error: bool = False) -> None:
        self.error = error

    async def scan_repository(self, **_: object) -> RepositoryScanResult:
        if self.error:
            raise RepositoryScanError("secret remote stderr")
        findings = SecurityEngine().analyze_code(
            'password = "do-not-return-this-secret"\n',
            "app.py",
        )
        return RepositoryScanResult(
            commit_sha="a" * 40,
            files_analyzed=1,
            bytes_analyzed=40,
            findings=findings,
        )

    async def scan_diff(self, **_: object) -> RepositoryScanResult:
        return await self.scan_repository()


def _worker(
    store: _Store,
    scanner: _Scanner,
    clock: _Clock,
    *,
    explanation_provider: SecurityExplanationProvider | None = None,
    result_notifier: CodeScanResultNotifier | None = None,
) -> ProcessCodeScanWorker:
    return ProcessCodeScanWorker(
        store=store,
        scanner_factory=lambda policy: scanner,
        explanation_provider=explanation_provider or LocalSecurityExplanationProvider(),
        clock=clock,
        config=CodeScanWorkerConfig(
            lease_duration=timedelta(seconds=180),
            max_attempts=3,
            initial_backoff=timedelta(seconds=2),
            max_backoff=timedelta(seconds=60),
            allowed_git_hosts=frozenset({"github.com"}),
        ),
        result_notifier=result_notifier,
    )


@pytest.mark.asyncio
async def test_worker_completes_and_blocks_critical_finding() -> None:
    store = _Store(_job())

    result = await _worker(store, _Scanner(), _Clock()).process_next()

    assert result is CodeScanWorkResult.COMPLETED
    assert store.completed
    assert store.phases == [CodeScanPhase.DETECTING, CodeScanPhase.GATING]


class _UnavailableExplanationProvider(LocalSecurityExplanationProvider):
    async def generate_report(self, **_: object) -> str:
        raise RuntimeError("provider unavailable")


@pytest.mark.asyncio
async def test_advisory_provider_failure_does_not_block_deterministic_result() -> None:
    store = _Store(_job())

    result = await _worker(
        store,
        _Scanner(),
        _Clock(),
        explanation_provider=_UnavailableExplanationProvider(),
    ).process_next()

    assert result is CodeScanWorkResult.COMPLETED
    assert store.completed
    assert not store.rescheduled
    assert not store.failed


class _NotificationError(RuntimeError):
    failed_channels = ("github", "untrusted-value")


class _NotificationLogRecord(Protocol):
    notification_channels: str


class _FailingNotifier:
    async def notify(
        self,
        *,
        job: ClaimedCodeScan,
        result: RepositoryScanResult,
        gate_decision: GateDecision,
    ) -> None:
        del job, result, gate_decision
        raise _NotificationError("provider detail must not be logged")


@pytest.mark.asyncio
async def test_optional_notification_failure_logs_only_safe_channel_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _Store(_job())

    result = await _worker(
        store,
        _Scanner(),
        _Clock(),
        result_notifier=_FailingNotifier(),
    ).process_next()

    assert result is CodeScanWorkResult.COMPLETED
    record = cast(
        _NotificationLogRecord,
        next(
            item
            for item in caplog.records
            if item.message == "code scan result notification failed"
        ),
    )
    assert record.notification_channels == "github"
    assert "provider detail" not in caplog.text


@pytest.mark.asyncio
async def test_worker_reschedules_safe_remote_failure_before_last_attempt() -> None:
    store = _Store(_job(attempt_count=1))

    result = await _worker(store, _Scanner(error=True), _Clock()).process_next()

    assert result is CodeScanWorkResult.RESCHEDULED
    assert store.rescheduled
    assert not store.failed


@pytest.mark.asyncio
async def test_worker_fails_safely_after_attempt_budget() -> None:
    store = _Store(_job(attempt_count=3))

    result = await _worker(store, _Scanner(error=True), _Clock()).process_next()

    assert result is CodeScanWorkResult.FAILED
    assert store.failed
