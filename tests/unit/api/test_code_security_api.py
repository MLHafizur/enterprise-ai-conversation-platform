from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from enterprise_chat_platform.api.code_security import (
    get_analyze_code_service,
    get_code_security_store,
    get_security_explanation_provider,
    get_security_review_status_publisher,
    router,
)
from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.code_security import (
    AnalyzeCodeService,
    CompletedCodeScan,
    TenantCodeSecurityPolicy,
)
from enterprise_chat_platform.application.code_security_ai import (
    LocalSecurityExplanationProvider,
    SecurityExplanationProvider,
)
from enterprise_chat_platform.domain.code_security import (
    CodeRepositoryProvider,
    CodeScanSource,
    GateDecision,
    SecurityReviewDecision,
    SecuritySummary,
)
from enterprise_chat_platform.domain.identifiers import (
    CodeRepositoryId,
    CodeScanId,
    SecurityFindingId,
    SecurityReviewId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.code_analysis.github_webhook import (
    MAX_GITHUB_WEBHOOK_BYTES,
    derive_repository_webhook_secret,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine
from enterprise_chat_platform.infrastructure.persistence.models import (
    CodeRepositoryRecord,
    CodeScanRecord,
    CodeScanReviewRecord,
    CodeSecurityFindingRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    CodeSecurityDashboardData,
    ScanDetail,
    SqlAlchemyCodeSecurityStore,
)
from enterprise_chat_platform.settings import Settings

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
REPOSITORY_ID = CodeRepositoryId.parse("33333333-3333-4333-8333-333333333333")
SCAN_ID = CodeScanId.parse("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 11, 16, 23, 0, tzinfo=UTC)
DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:test-password@127.0.0.1:55432/enterprise_chat"
)
WEBHOOK_SECRET = "day10-test-webhook-secret-32-characters"
PUSH_SHA = "a" * 40


def _principal(*roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor=ActorContext(tenant_id=TENANT_ID, actor_id=ACTOR_ID),
        roles=frozenset(roles),
    )


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": SecretStr(DATABASE_URL),
        "auth_mode": "jwt",
        "jwt_key": SecretStr("unit-test-jwt-key"),
        "realtime_enabled": False,
        "provider_mode": "disabled",
        "code_security_ai_mode": "echo",
        "code_security_webhook_secret": SecretStr(WEBHOOK_SECRET),
        "_env_file": None,
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def _repository() -> CodeRepositoryRecord:
    return CodeRepositoryRecord(
        tenant_id=TENANT_ID.value,
        id=REPOSITORY_ID.value,
        provider=CodeRepositoryProvider.GITHUB.value,
        display_name="Secure service",
        clone_url="https://github.com/example/secure-service.git",
        normalized_url="https://github.com/example/secure-service",
        default_branch="main",
        enabled=True,
        created_by=ACTOR_ID.value,
        created_at=NOW,
        updated_at=NOW,
    )


def _scan(
    *,
    scan_id: CodeScanId = SCAN_ID,
    gate: str | None = GateDecision.BLOCK.value,
    total: int = 1,
) -> CodeScanRecord:
    return CodeScanRecord(
        tenant_id=TENANT_ID.value,
        id=scan_id.value,
        repository_id=REPOSITORY_ID.value,
        source="repository",
        status="succeeded",
        phase="completed",
        gate_decision=gate,
        branch="main",
        base_branch=None,
        head_branch=None,
        commit_sha="a" * 40,
        source_label="Secure service:main",
        content_hash="b" * 64,
        total_findings=total,
        critical_findings=total,
        high_findings=0,
        medium_findings=0,
        low_findings=0,
        info_findings=0,
        files_scanned=12,
        bytes_scanned=2048,
        ruleset_version="day10.v1",
        safe_ai_summary=None,
        safe_error=None,
        webhook_delivery_id=None,
        attempt_count=1,
        available_at=NOW,
        last_attempt_at=NOW,
        lease_token=None,
        lease_expires_at=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        finished_at=NOW,
    )


def _finding() -> CodeSecurityFindingRecord:
    return CodeSecurityFindingRecord(
        tenant_id=TENANT_ID.value,
        id=SecurityFindingId.parse("55555555-5555-4555-8555-555555555555").value,
        scan_id=SCAN_ID.value,
        fingerprint="c" * 64,
        rule_id="hardcoded_secret",
        severity="critical",
        detector="ast",
        cwe_id="CWE-798",
        owasp_category="A07:2021",
        safe_message="A credential-like value is hardcoded in source",
        file_path="src/settings.py",
        line_number=8,
        column_number=0,
        redacted_code_snippet="api_key = '[REDACTED]'",
        recommendation="Load secrets from an approved secret manager",
        created_at=NOW,
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def analyze(self, **kwargs: object) -> CompletedCodeScan:
        self.calls.append(kwargs)
        code = cast(str, kwargs["code"])
        label = cast(str, kwargs["source_label"])
        source = cast(CodeScanSource, kwargs["source"])
        findings = SecurityEngine().analyze_code(code, label)
        return CompletedCodeScan(
            scan_id=SCAN_ID,
            source=source,
            source_label=label,
            gate_decision=(GateDecision.BLOCK if findings else GateDecision.APPROVE),
            summary=SecuritySummary.from_findings(findings),
            findings=findings,
            ruleset_version="day10.v1",
            files_scanned=1,
            bytes_scanned=len(code.encode()),
            completed_at=NOW,
        )


class _Store:
    def __init__(self) -> None:
        self.policy = TenantCodeSecurityPolicy()
        self.repository = _repository()
        self.scan = _scan()
        self.finding = _finding()
        self.review: CodeScanReviewRecord | None = None
        self.enqueued: list[dict[str, object]] = []
        self.updated_policy: TenantCodeSecurityPolicy | None = self.policy
        self.policy_update_error: IntegrityError | None = None

    async def get_policy(self, **_: object) -> TenantCodeSecurityPolicy:
        return self.policy

    async def update_policy(self, **kwargs: object) -> TenantCodeSecurityPolicy | None:
        if self.policy_update_error is not None:
            raise self.policy_update_error
        policy = cast(TenantCodeSecurityPolicy, kwargs["policy"])
        if self.updated_policy is None:
            return None
        self.updated_policy = replace(policy, revision=cast(int, kwargs["expected_revision"]) + 1)
        return self.updated_policy

    async def add_repository(self, **kwargs: object) -> CodeRepositoryRecord:
        return CodeRepositoryRecord(
            tenant_id=cast(TenantId, kwargs["tenant_id"]).value,
            id=cast(CodeRepositoryId, kwargs["repository_id"]).value,
            provider=cast(CodeRepositoryProvider, kwargs["provider"]).value,
            display_name=cast(str, kwargs["display_name"]),
            clone_url=cast(str, kwargs["clone_url"]),
            normalized_url=cast(str, kwargs["normalized_url"]),
            default_branch=cast(str, kwargs["default_branch"]),
            enabled=True,
            created_by=ACTOR_ID.value,
            created_at=NOW,
            updated_at=NOW,
        )

    async def list_repositories(self, **_: object) -> tuple[CodeRepositoryRecord, ...]:
        return (self.repository,)

    async def get_repository(self, **kwargs: object) -> CodeRepositoryRecord | None:
        repository_id = cast(CodeRepositoryId, kwargs["repository_id"])
        return self.repository if repository_id == REPOSITORY_ID else None

    async def enqueue_scan(self, **kwargs: object) -> CodeScanRecord:
        self.enqueued.append(kwargs)
        source = cast(CodeScanSource, kwargs["source"])
        return replace_scan(
            self.scan,
            id=cast(CodeScanId, kwargs["scan_id"]).value,
            source=source.value,
            status="queued",
            phase="received",
            gate_decision=None,
            branch=kwargs["branch"],
            base_branch=kwargs["base_branch"],
            head_branch=kwargs["head_branch"],
            total_findings=0,
            critical_findings=0,
            files_scanned=0,
            bytes_scanned=0,
            started_at=None,
            finished_at=None,
        )

    async def list_scans(self, **_: object) -> tuple[CodeScanRecord, ...]:
        return (self.scan,)

    async def get_dashboard(self, **_: object) -> CodeSecurityDashboardData:
        return CodeSecurityDashboardData(
            counts={
                "total_scans": 1,
                "approved": 0,
                "review_required": 0,
                "blocked": 1,
                "failed": 0,
                "critical": 1,
                "high": 0,
                "medium": 0,
                "low": 0,
                "info": 0,
                "vulnerabilities_found": 1,
                "files_scanned": 1,
                "repositories": 1,
            },
            recent_scans=(self.scan,),
            top_vulnerabilities=(("hardcoded_secret", "critical", "CWE-798", 1),),
            trend=(("2026-11-16", 1, 1, 0, 0, 0, 0),),
        )

    async def get_scan_detail(self, **kwargs: object) -> ScanDetail | None:
        scan_id = cast(CodeScanId, kwargs["scan_id"])
        if scan_id != SCAN_ID:
            return None
        return ScanDetail(scan=self.scan, findings=(self.finding,), review=self.review)

    async def get_finding(self, **kwargs: object) -> CodeSecurityFindingRecord | None:
        finding_id = cast(SecurityFindingId, kwargs["finding_id"])
        return self.finding if finding_id.value == self.finding.id else None

    async def add_review(self, **kwargs: object) -> CodeScanReviewRecord:
        self.review = CodeScanReviewRecord(
            tenant_id=TENANT_ID.value,
            id=cast(SecurityReviewId, kwargs["review_id"]).value,
            scan_id=SCAN_ID.value,
            required_gate_decision="review_required",
            decision=cast(Any, kwargs["decision"]).value,
            safe_note=kwargs["safe_note"],
            reviewed_by=ACTOR_ID.value,
            reviewed_at=NOW,
        )
        return self.review


class _ReviewStatusPublisher:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.error = error
        self.delay_seconds = delay_seconds
        self.cancelled = False
        self.calls: list[dict[str, object]] = []

    async def publish_review_decision(
        self,
        *,
        clone_url: str,
        commit_sha: str,
        decision: SecurityReviewDecision,
    ) -> None:
        self.calls.append(
            {
                "clone_url": clone_url,
                "commit_sha": commit_sha,
                "decision": decision,
            }
        )
        if self.delay_seconds:
            try:
                await asyncio.sleep(self.delay_seconds)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error is not None:
            raise self.error


def replace_scan(record: CodeScanRecord, **changes: object) -> CodeScanRecord:
    values = {
        column.name: getattr(record, column.name)
        for column in cast(Any, CodeScanRecord.__table__).columns
    }
    values.update(changes)
    return CodeScanRecord(**values)


def _application(
    *,
    roles: tuple[str, ...] = ("agent",),
    store: _Store | None = None,
    service: _Service | None = None,
    settings: Settings | None = None,
    review_status_publisher: _ReviewStatusPublisher | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    resolved_store = store or _Store()
    resolved_service = service or _Service()
    resolved_settings = settings or _settings()
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(*roles)
    app.dependency_overrides[get_runtime_settings] = lambda: resolved_settings
    app.dependency_overrides[get_code_security_store] = lambda: cast(
        SqlAlchemyCodeSecurityStore,
        resolved_store,
    )
    app.dependency_overrides[get_security_review_status_publisher] = lambda: review_status_publisher
    app.dependency_overrides[get_analyze_code_service] = lambda: cast(
        AnalyzeCodeService,
        resolved_service,
    )
    app.dependency_overrides[get_security_explanation_provider] = lambda: cast(
        SecurityExplanationProvider,
        LocalSecurityExplanationProvider(),
    )
    return app


def test_agent_lists_versioned_rules_without_patterns() -> None:
    with TestClient(_application()) as client:
        response = client.get("/v1/security/rules")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["ruleset_version"] == "day10.v1"
    assert {rule["rule_id"] for rule in response.json()["rules"]} == {
        "sql_injection",
        "xss",
        "path_traversal",
        "hardcoded_secret",
        "unsafe_eval_exec",
        "command_injection",
    }
    assert all("pattern" not in rule for rule in response.json()["rules"])
    assert {rule["rule_id"]: rule["owasp_category"] for rule in response.json()["rules"]}[
        "sql_injection"
    ] == "A03:2021"


@pytest.mark.parametrize("roles", [(), ("user",)])
def test_non_security_roles_are_forbidden(roles: tuple[str, ...]) -> None:
    with TestClient(_application(roles=roles)) as client:
        response = client.get("/v1/security/scans")
    assert response.status_code == 403


def test_snippet_scan_returns_redacted_findings_without_source() -> None:
    service = _Service()
    source = 'api_key = "super-secret-production-key"'
    with TestClient(_application(service=service)) as client:
        response = client.post(
            "/v1/security/scans/snippet",
            json={"source_label": "settings.py", "code": source},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["gate_decision"] == "block"
    assert payload["critical_findings"] == 1
    assert "[REDACTED]" in payload["findings"][0]["redacted_code_snippet"]
    assert "super-secret" not in response.text
    assert service.calls[0]["source"] is CodeScanSource.SNIPPET


def test_multipart_file_scan_uses_filename_and_never_echoes_source() -> None:
    service = _Service()
    with TestClient(_application(service=service)) as client:
        response = client.post(
            "/v1/security/scans/file",
            files={"file": ("service.py", b"print('safe')", "text/x-python")},
        )

    assert response.status_code == 201
    assert response.json()["source_label"] == "service.py"
    assert "print('safe')" not in response.text
    assert service.calls[0]["source"] is CodeScanSource.FILE_UPLOAD


def test_admin_adds_only_allowlisted_https_repository() -> None:
    with TestClient(_application(roles=("admin",))) as client:
        response = client.post(
            "/v1/security/repositories",
            json={
                "display_name": "Secure service",
                "clone_url": "https://github.com/example/secure-service",
                "default_branch": "main",
            },
        )
        rejected = client.post(
            "/v1/security/repositories",
            json={
                "display_name": "Internal target",
                "clone_url": "https://127.0.0.1/admin.git",
                "default_branch": "main",
            },
        )

    assert response.status_code == 201
    assert response.json()["clone_url"] == "https://github.com/example/secure-service.git"
    assert rejected.status_code == 422
    assert "127.0.0.1" not in rejected.text


def test_agent_cannot_manage_repository_or_settings() -> None:
    with TestClient(_application()) as client:
        repository = client.post(
            "/v1/security/repositories",
            json={
                "display_name": "Denied",
                "clone_url": "https://github.com/example/denied",
                "default_branch": "main",
            },
        )
        settings = client.put(
            "/v1/security/settings",
            json={
                "expected_revision": 0,
                "block_severity": "critical",
                "review_severity": "high",
                "max_file_bytes": 1_048_576,
                "max_repository_files": 2_000,
                "max_repository_bytes": 52_428_800,
                "auto_scan_push": True,
                "auto_scan_pull_request": True,
                "ai_summary_enabled": False,
            },
        )
    assert repository.status_code == settings.status_code == 403


def test_repository_and_pull_request_scans_are_durably_queued() -> None:
    store = _Store()
    with TestClient(_application(store=store)) as client:
        repository_response = client.post(
            f"/v1/security/repositories/{REPOSITORY_ID}/scans",
            json={"branch": "release/2026"},
        )
        pull_request_response = client.post(
            f"/v1/security/repositories/{REPOSITORY_ID}/pull-request-scans",
            json={"base_branch": "main", "head_branch": "feature/security"},
        )

    assert repository_response.status_code == pull_request_response.status_code == 202
    assert [call["source"] for call in store.enqueued] == [
        CodeScanSource.REPOSITORY,
        CodeScanSource.PULL_REQUEST,
    ]
    assert store.enqueued[1]["branch"] is None
    assert store.enqueued[1]["base_branch"] == "main"


def test_lists_are_tenant_scoped_safe_contracts() -> None:
    with TestClient(_application()) as client:
        scans = client.get("/v1/security/scans")
        repositories = client.get("/v1/security/repositories")
        settings = client.get("/v1/security/settings")

    assert scans.status_code == repositories.status_code == settings.status_code == 200
    assert scans.json()["scans"][0]["id"] == str(SCAN_ID)
    assert scans.json()["scans"][0]["findings"] == []
    assert repositories.json()["repositories"][0]["id"] == str(REPOSITORY_ID)
    assert "normalized_url" not in repositories.text
    assert settings.json()["revision"] == 0
    assert "tenant_id" not in scans.text + repositories.text + settings.text


def test_scan_detail_and_local_explanation_are_content_safe() -> None:
    finding_id = _finding().id
    with TestClient(_application()) as client:
        detail = client.get(f"/v1/security/scans/{SCAN_ID}")
        explanation = client.post(f"/v1/security/findings/{finding_id}/explanation")
        fix = client.post(f"/v1/security/findings/{finding_id}/fix")
        report = client.post(f"/v1/security/scans/{SCAN_ID}/report")

    assert detail.status_code == explanation.status_code == fix.status_code == 200
    assert report.status_code == 200
    assert set(detail.json()) == {
        "id",
        "repository_id",
        "source",
        "status",
        "phase",
        "gate_decision",
        "source_label",
        "branch",
        "base_branch",
        "head_branch",
        "total_findings",
        "critical_findings",
        "high_findings",
        "medium_findings",
        "low_findings",
        "info_findings",
        "files_scanned",
        "bytes_scanned",
        "ruleset_version",
        "safe_ai_summary",
        "safe_error",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "findings",
        "review",
    }
    assert detail.json()["review"] is None
    assert detail.json()["findings"][0]["cwe_id"] == "CWE-798"
    assert explanation.json()["advisory"] is True
    assert len(explanation.json()["remediation_steps"]) == 3
    assert fix.json()["advisory"] is True
    assert report.json()["report"].startswith("# Security analysis report")


def test_settings_use_optimistic_revision() -> None:
    store = _Store()
    body = {
        "expected_revision": 0,
        "block_severity": "critical",
        "review_severity": "medium",
        "max_file_bytes": 2_048,
        "max_repository_files": 100,
        "max_repository_bytes": 1_048_576,
        "auto_scan_push": False,
        "auto_scan_pull_request": True,
        "ai_summary_enabled": True,
    }
    with TestClient(_application(roles=("admin",), store=store)) as client:
        response = client.put("/v1/security/settings", json=body)
        store.updated_policy = None
        conflict = client.put("/v1/security/settings", json=body)

    assert response.status_code == 200
    assert response.json()["revision"] == 1
    assert response.json()["review_severity"] == "medium"
    assert conflict.status_code == 409


def test_concurrent_first_settings_write_maps_database_race_to_conflict() -> None:
    store = _Store()
    store.policy_update_error = IntegrityError("INSERT", {}, RuntimeError("duplicate"))
    body = {
        "expected_revision": 0,
        "block_severity": "critical",
        "review_severity": "high",
        "max_file_bytes": 1_048_576,
        "max_repository_files": 2_000,
        "max_repository_bytes": 52_428_800,
        "auto_scan_push": True,
        "auto_scan_pull_request": True,
        "ai_summary_enabled": False,
    }

    with TestClient(_application(roles=("admin",), store=store)) as client:
        response = client.put("/v1/security/settings", json=body)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Code security resource changed before the request completed"
    }


def test_admin_records_one_review_only_for_review_required_scan() -> None:
    store = _Store()
    publisher = _ReviewStatusPublisher()
    store.scan = replace_scan(
        store.scan,
        gate_decision=GateDecision.REVIEW_REQUIRED.value,
    )
    with TestClient(
        _application(
            roles=("admin",),
            store=store,
            review_status_publisher=publisher,
        )
    ) as client:
        response = client.post(
            f"/v1/security/scans/{SCAN_ID}/reviews",
            json={"decision": "approved", "safe_note": "Verified compensating control"},
        )
        detail = client.get(f"/v1/security/scans/{SCAN_ID}")

    assert response.status_code == 201
    assert response.json()["scan_id"] == str(SCAN_ID)
    assert response.json()["decision"] == "approved"
    assert response.json()["safe_note"] == "Verified compensating control"
    assert detail.status_code == 200
    assert set(detail.json()["review"]) == {
        "id",
        "scan_id",
        "decision",
        "safe_note",
        "reviewed_by",
        "reviewed_at",
    }
    assert detail.json()["review"] == response.json()
    assert publisher.calls == [
        {
            "clone_url": "https://github.com/example/secure-service.git",
            "commit_sha": "a" * 40,
            "decision": SecurityReviewDecision.APPROVED,
        }
    ]


def test_webhook_scan_review_publishes_final_github_status() -> None:
    store = _Store()
    publisher = _ReviewStatusPublisher()
    store.scan = replace_scan(
        store.scan,
        source=CodeScanSource.WEBHOOK.value,
        gate_decision=GateDecision.REVIEW_REQUIRED.value,
    )

    with TestClient(
        _application(
            roles=("admin",),
            store=store,
            review_status_publisher=publisher,
        )
    ) as client:
        response = client.post(
            f"/v1/security/scans/{SCAN_ID}/reviews",
            json={"decision": "approved", "safe_note": "Webhook finding reviewed"},
        )

    assert response.status_code == 201
    assert publisher.calls == [
        {
            "clone_url": "https://github.com/example/secure-service.git",
            "commit_sha": "a" * 40,
            "decision": SecurityReviewDecision.APPROVED,
        }
    ]


def test_review_remains_committed_when_github_status_delivery_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _Store()
    store.scan = replace_scan(
        store.scan,
        gate_decision=GateDecision.REVIEW_REQUIRED.value,
    )
    publisher = _ReviewStatusPublisher(error=RuntimeError("secret provider response"))

    with TestClient(
        _application(
            roles=("admin",),
            store=store,
            review_status_publisher=publisher,
        )
    ) as client:
        response = client.post(
            f"/v1/security/scans/{SCAN_ID}/reviews",
            json={"decision": "blocked", "safe_note": "Exploit path confirmed"},
        )
        detail = client.get(f"/v1/security/scans/{SCAN_ID}")

    assert response.status_code == 201
    assert response.json()["decision"] == "blocked"
    assert detail.json()["review"] == response.json()
    assert "secret provider response" not in caplog.text
    matching = [
        record
        for record in caplog.records
        if record.getMessage() == "code security review status publish failed"
    ]
    assert len(matching) == 1
    assert matching[0].notification_channel == "github"  # type: ignore[attr-defined]


def test_review_response_bounds_slow_github_status_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "enterprise_chat_platform.api.code_security._REVIEW_STATUS_PUBLISH_TIMEOUT_SECONDS",
        0.01,
    )
    store = _Store()
    store.scan = replace_scan(
        store.scan,
        gate_decision=GateDecision.REVIEW_REQUIRED.value,
    )
    publisher = _ReviewStatusPublisher(delay_seconds=60)

    with TestClient(
        _application(
            roles=("admin",),
            store=store,
            review_status_publisher=publisher,
        )
    ) as client:
        response = client.post(
            f"/v1/security/scans/{SCAN_ID}/reviews",
            json={"decision": "blocked", "safe_note": "Manual review completed"},
        )

    assert response.status_code == 201
    assert response.json()["decision"] == "blocked"
    assert store.review is not None
    assert publisher.cancelled is True


def test_dashboard_matches_frontend_contract() -> None:
    with TestClient(_application()) as client:
        response = client.get("/v1/security/dashboard")

    assert response.status_code == 200
    assert set(response.json()) == {
        "counts",
        "recent_scans",
        "top_vulnerabilities",
        "trend",
    }
    assert response.json()["counts"] == {
        "total_scans": 1,
        "approved": 0,
        "review_required": 0,
        "blocked": 1,
        "failed": 0,
        "critical": 1,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
        "vulnerabilities_found": 1,
        "files_scanned": 1,
        "repositories": 1,
    }
    assert response.json()["top_vulnerabilities"][0]["rule_id"] == "hardcoded_secret"


def _signature(body: bytes) -> str:
    repository_secret = derive_repository_webhook_secret(
        master_secret=WEBHOOK_SECRET,
        tenant_id=TENANT_ID.value,
        repository_id=REPOSITORY_ID.value,
    )
    return "sha256=" + hmac.new(repository_secret.encode(), body, hashlib.sha256).hexdigest()


def _push_payload(*, clone_url: str = "https://github.com/example/secure-service.git") -> bytes:
    return json.dumps(
        {
            "ref": "refs/heads/main",
            "after": PUSH_SHA,
            "repository": {"clone_url": clone_url},
        },
        separators=(",", ":"),
    ).encode()


def test_webhook_rejects_bad_signature_before_queue_access() -> None:
    store = _Store()
    body = _push_payload()
    with TestClient(_application(store=store)) as client:
        response = client.post(
            f"/v1/security/webhooks/github/{TENANT_ID}/{REPOSITORY_ID}",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=" + "0" * 64,
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-1",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 401
    assert store.enqueued == []


def test_webhook_signature_cannot_be_replayed_for_another_repository() -> None:
    store = _Store()
    body = _push_payload()
    other_repository_id = UUID("99999999-9999-4999-8999-999999999999")

    with TestClient(_application(store=store)) as client:
        response = client.post(
            f"/v1/security/webhooks/github/{TENANT_ID}/{other_repository_id}",
            content=body,
            headers={
                # This is valid only for REPOSITORY_ID, not the route above.
                "X-Hub-Signature-256": _signature(body),
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-cross-repository",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 401
    assert store.enqueued == []


def test_signed_webhook_matches_repository_and_honors_auto_scan_policy() -> None:
    store = _Store()
    body = _push_payload()
    headers = {
        "X-Hub-Signature-256": _signature(body),
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "delivery-2",
        "Content-Type": "application/json",
    }
    with TestClient(_application(store=store)) as client:
        queued = client.post(
            f"/v1/security/webhooks/github/{TENANT_ID}/{REPOSITORY_ID}",
            content=body,
            headers=headers,
        )
        store.policy = replace(store.policy, auto_scan_push=False)
        disabled = client.post(
            f"/v1/security/webhooks/github/{TENANT_ID}/{REPOSITORY_ID}",
            content=body,
            headers={**headers, "X-GitHub-Delivery": "delivery-3"},
        )

    assert queued.status_code == 202
    assert queued.json()["reason"] == "queued"
    assert store.enqueued[0]["source"] is CodeScanSource.WEBHOOK
    assert store.enqueued[0]["branch"] == PUSH_SHA
    assert disabled.json() == {
        "accepted": False,
        "reason": "automatic_scan_disabled",
        "scan_id": None,
    }


def test_signed_webhook_rejects_repository_substitution() -> None:
    body = _push_payload(clone_url="https://github.com/attacker/other.git")
    with TestClient(_application()) as client:
        response = client.post(
            f"/v1/security/webhooks/github/{TENANT_ID}/{REPOSITORY_ID}",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body),
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-4",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 404
    assert "attacker" not in response.text


def test_webhook_rejects_oversized_body_before_queue_access() -> None:
    store = _Store()
    body = b"x" * (MAX_GITHUB_WEBHOOK_BYTES + 1)
    with TestClient(_application(store=store)) as client:
        response = client.post(
            f"/v1/security/webhooks/github/{TENANT_ID}/{REPOSITORY_ID}",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body),
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-oversized",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Payload too large"}
    assert store.enqueued == []


def test_disabled_feature_is_hidden_from_authenticated_users() -> None:
    with TestClient(_application(settings=_settings(code_security_enabled=False))) as client:
        response = client.get("/v1/security/rules")
    assert response.status_code == 404
