import threading
from datetime import UTC, datetime

import pytest

from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.code_security import (
    AnalyzeCodeService,
    TenantCodeSecurityPolicy,
)
from enterprise_chat_platform.domain.code_security import (
    CodeScanSource,
    GateDecision,
    SecuritySeverity,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    CodeScanId,
    SecurityReviewId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine


class _ThreadRecordingEngine(SecurityEngine):
    def __init__(self) -> None:
        super().__init__()
        self.thread_id: int | None = None

    def analyze_code(self, code: str, file_path: str = "snippet.py"):  # type: ignore[no-untyped-def]
        self.thread_id = threading.get_ident()
        return super().analyze_code(code, file_path)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


class _Ids:
    def new_scan_id(self) -> CodeScanId:
        return CodeScanId.parse("33333333-3333-4333-8333-333333333333")

    def new_review_id(self) -> SecurityReviewId:  # pragma: no cover - unused port method
        raise NotImplementedError


class _Persistence:
    def __init__(self, policy: TenantCodeSecurityPolicy | None = None) -> None:
        self.policy = policy or TenantCodeSecurityPolicy()
        self.saved: dict[str, object] | None = None

    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy:
        return self.policy

    async def save_completed_scan(self, **values: object) -> None:
        self.saved = values


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        actor_id=UserId.parse("22222222-2222-4222-8222-222222222222"),
    )


@pytest.mark.asyncio
async def test_direct_scan_blocks_critical_and_persists_only_safe_result() -> None:
    persistence = _Persistence()
    service = AnalyzeCodeService(
        persistence=persistence,
        engine=SecurityEngine(),
        clock=_Clock(),
        ids=_Ids(),
    )

    result = await service.analyze(
        actor=_actor(),
        source=CodeScanSource.SNIPPET,
        source_label="demo.py",
        code='api_key = "sk-a-real-secret-that-must-not-persist"\n',
    )

    assert result.gate_decision is GateDecision.BLOCK
    assert result.summary.critical == 1
    assert persistence.saved is not None
    findings = persistence.saved["findings"]
    assert isinstance(findings, tuple)
    assert findings[0].finding_id == result.findings[0].finding_id
    assert "sk-a-real-secret" not in findings[0].code_snippet


def test_policy_expands_thresholds_into_fail_closed_gate() -> None:
    policy = TenantCodeSecurityPolicy(
        block_severity=SecuritySeverity.HIGH,
        review_severity=SecuritySeverity.MEDIUM,
    )

    assert policy.gate_policy().block_severities == (
        SecuritySeverity.HIGH,
        SecuritySeverity.CRITICAL,
    )
    assert policy.gate_policy().review_severities == (SecuritySeverity.MEDIUM,)


@pytest.mark.asyncio
async def test_direct_scan_rejects_tenant_file_limit_before_persistence() -> None:
    persistence = _Persistence(TenantCodeSecurityPolicy(max_file_bytes=1_024))
    service = AnalyzeCodeService(
        persistence=persistence,
        engine=SecurityEngine(),
        clock=_Clock(),
        ids=_Ids(),
    )

    with pytest.raises(DomainValidationError, match="tenant file limit"):
        await service.analyze(
            actor=_actor(),
            source=CodeScanSource.FILE_UPLOAD,
            source_label="large.py",
            code="x" * 1_025,
        )

    assert persistence.saved is None


@pytest.mark.asyncio
async def test_direct_scan_runs_cpu_analysis_off_the_event_loop_thread() -> None:
    persistence = _Persistence()
    engine = _ThreadRecordingEngine()
    service = AnalyzeCodeService(
        persistence=persistence,
        engine=engine,
        clock=_Clock(),
        ids=_Ids(),
    )
    event_loop_thread = threading.get_ident()

    await service.analyze(
        actor=_actor(),
        source=CodeScanSource.SNIPPET,
        source_label="safe.py",
        code="print('safe')\n",
    )

    assert engine.thread_id is not None
    assert engine.thread_id != event_loop_thread
