"""Authenticated HTTP boundary for deterministic secure-code analysis."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from enterprise_chat_platform.api.dependencies import (
    get_runtime_session_factory,
    get_runtime_settings,
)
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.code_security import (
    AnalyzeCodeService,
    CompletedCodeScan,
    TenantCodeSecurityPolicy,
)
from enterprise_chat_platform.application.code_security_ai import (
    LocalSecurityExplanationProvider,
    SecurityExplanationProvider,
    SecurityFindingContext,
)
from enterprise_chat_platform.domain.code_security import (
    MAX_REPOSITORY_DISPLAY_NAME_LENGTH,
    MAX_SAFE_MESSAGE_LENGTH,
    MAX_WEBHOOK_DELIVERY_ID_LENGTH,
    CodeRepositoryProvider,
    CodeScanSource,
    GateDecision,
    SecurityAnalysisSettings,
    SecurityReviewDecision,
    SecuritySeverity,
)
from enterprise_chat_platform.domain.errors import DomainError
from enterprise_chat_platform.domain.identifiers import (
    CodeRepositoryId,
    CodeScanId,
    SecurityFindingId,
    SecurityReviewId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.code_analysis.gemini_explanations import (
    GeminiSecurityExplanationProvider,
    SecurityAIUnavailableError,
)
from enterprise_chat_platform.infrastructure.code_analysis.github_webhook import (
    MAX_GITHUB_WEBHOOK_BYTES,
    derive_repository_webhook_secret,
    parse_github_scan_event,
    verify_github_signature,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    validate_git_ref,
    validate_repository_url,
)
from enterprise_chat_platform.infrastructure.code_analysis.result_notifications import (
    GitHubSecurityStatusPublisher,
    SecurityReviewStatusPublisher,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import (
    DEFAULT_RULE_SET_VERSION,
    DEFAULT_SECURITY_RULES,
    SecurityEngine,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    CodeRepositoryRecord,
    CodeScanRecord,
    CodeScanReviewRecord,
    CodeSecurityFindingRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    SqlAlchemyCodeSecurityStore,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock
from enterprise_chat_platform.settings import Settings

router = APIRouter(
    prefix="/v1/security",
    tags=["code-security"],
)

logger = logging.getLogger(__name__)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SnippetScanRequest(_StrictModel):
    source_label: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=10_485_760)


class RepositoryCreateRequest(_StrictModel):
    display_name: str = Field(min_length=1, max_length=MAX_REPOSITORY_DISPLAY_NAME_LENGTH)
    clone_url: str = Field(min_length=1, max_length=2_048)
    default_branch: str = Field(default="main", min_length=1, max_length=255)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("display name must be trimmed printable text")
        return value


class RepositoryScanRequest(_StrictModel):
    branch: str | None = Field(default=None, min_length=1, max_length=255)


class PullRequestScanRequest(_StrictModel):
    base_branch: str = Field(min_length=1, max_length=255)
    head_branch: str = Field(min_length=1, max_length=255)


class SecuritySettingsUpdateRequest(_StrictModel):
    expected_revision: int = Field(ge=0)
    block_severity: Literal["critical", "high", "medium", "low"]
    review_severity: Literal["high", "medium", "low", "info"]
    max_file_bytes: int = Field(ge=1_024, le=10_485_760)
    max_repository_files: int = Field(ge=1, le=100_000)
    max_repository_bytes: int = Field(ge=1_024, le=1_073_741_824)
    auto_scan_push: bool
    auto_scan_pull_request: bool
    ai_summary_enabled: bool


class ScanReviewRequest(_StrictModel):
    decision: Literal["approved", "blocked"]
    safe_note: str | None = Field(default=None, min_length=1, max_length=MAX_SAFE_MESSAGE_LENGTH)

    @field_validator("safe_note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or not value.isprintable()):
            raise ValueError("review note must be trimmed printable text")
        return value


class UuidCodeSecurityIds:
    """Generate opaque server-owned identifiers at the application boundary."""

    @staticmethod
    def new_scan_id() -> CodeScanId:
        return CodeScanId.new()

    @staticmethod
    def new_review_id() -> SecurityReviewId:
        return SecurityReviewId.new()


def get_code_security_store(
    session_factory: Annotated[AsyncSessionFactory, Depends(get_runtime_session_factory)],
) -> SqlAlchemyCodeSecurityStore:
    return SqlAlchemyCodeSecurityStore(session_factory)


def get_analyze_code_service(
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> AnalyzeCodeService:
    return AnalyzeCodeService(
        persistence=store,
        engine=SecurityEngine(
            settings=SecurityAnalysisSettings(
                max_code_bytes=settings.code_security_max_file_size_bytes,
                max_findings=settings.code_security_max_findings_per_scan,
            )
        ),
        clock=UtcSystemClock(),
        ids=UuidCodeSecurityIds(),
    )


async def get_security_explanation_provider(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> AsyncIterator[SecurityExplanationProvider]:
    """Own a request-scoped HTTP client only when Gemini is explicitly enabled."""
    if settings.code_security_ai_mode == "disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security explanation service is unavailable",
        )
    if settings.code_security_ai_mode == "echo":
        yield LocalSecurityExplanationProvider()
        return

    api_key = settings.gemini_api_key
    if api_key is None:  # Settings validates this; keep the boundary fail-closed.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security explanation service is unavailable",
        )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield GeminiSecurityExplanationProvider(
            client=client,
            api_key=api_key.get_secret_value(),
            model=settings.gemini_model,
            timeout_seconds=float(settings.provider_timeout_seconds),
            api_base_url=settings.gemini_api_base_url,
        )


async def get_security_review_status_publisher(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> AsyncIterator[SecurityReviewStatusPublisher | None]:
    """Own the request-scoped client used for an optional final GitHub status."""
    token = settings.code_security_github_token
    if token is None:
        yield None
        return
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield GitHubSecurityStatusPublisher(
            client=client,
            github_token=token.get_secret_value(),
            github_api_base_url=settings.code_security_github_api_base_url,
            allowed_git_hosts=settings.allowed_code_repository_hosts,
            timeout_seconds=float(settings.provider_timeout_seconds),
        )


def require_code_security_user(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> AuthenticatedPrincipal:
    if not settings.code_security_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not principal.roles.intersection({"agent", "admin"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return principal


def require_code_security_admin(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> AuthenticatedPrincipal:
    if not settings.code_security_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if "admin" not in principal.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return principal


def _invalid_request() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Code security request is invalid",
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Code security resource changed before the request completed",
    )


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _finding_dict(
    finding: CodeSecurityFindingRecord,
) -> dict[str, object]:
    return {
        "id": str(finding.id),
        "fingerprint": finding.fingerprint,
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "detector": finding.detector,
        "cwe_id": finding.cwe_id,
        "owasp_category": finding.owasp_category,
        "safe_message": finding.safe_message,
        "file_path": finding.file_path,
        "line_number": finding.line_number,
        "column_number": finding.column_number,
        "redacted_code_snippet": finding.redacted_code_snippet,
        "recommendation": finding.recommendation,
        "created_at": finding.created_at.isoformat(),
    }


def _scan_dict(
    scan: CodeScanRecord,
    *,
    findings: tuple[CodeSecurityFindingRecord, ...] = (),
) -> dict[str, object]:
    return {
        "id": str(scan.id),
        "repository_id": None if scan.repository_id is None else str(scan.repository_id),
        "source": scan.source,
        "status": scan.status,
        "phase": scan.phase,
        "gate_decision": scan.gate_decision,
        "source_label": scan.source_label,
        "branch": scan.branch,
        "base_branch": scan.base_branch,
        "head_branch": scan.head_branch,
        "total_findings": scan.total_findings,
        "critical_findings": scan.critical_findings,
        "high_findings": scan.high_findings,
        "medium_findings": scan.medium_findings,
        "low_findings": scan.low_findings,
        "info_findings": scan.info_findings,
        "files_scanned": scan.files_scanned,
        "bytes_scanned": scan.bytes_scanned,
        "ruleset_version": scan.ruleset_version,
        "safe_ai_summary": scan.safe_ai_summary,
        "safe_error": scan.safe_error,
        "created_at": scan.created_at.isoformat(),
        "updated_at": scan.updated_at.isoformat(),
        "started_at": None if scan.started_at is None else scan.started_at.isoformat(),
        "finished_at": None if scan.finished_at is None else scan.finished_at.isoformat(),
        "findings": [_finding_dict(finding) for finding in findings],
    }


def _completed_scan_dict(scan: CompletedCodeScan) -> dict[str, object]:
    return {
        "id": str(scan.scan_id),
        "repository_id": None,
        "source": scan.source.value,
        "status": "succeeded",
        "phase": "completed",
        "gate_decision": scan.gate_decision.value,
        "source_label": scan.source_label,
        "branch": None,
        "base_branch": None,
        "head_branch": None,
        "total_findings": scan.summary.total_findings,
        "critical_findings": scan.summary.critical,
        "high_findings": scan.summary.high,
        "medium_findings": scan.summary.medium,
        "low_findings": scan.summary.low,
        "info_findings": scan.summary.info,
        "files_scanned": scan.files_scanned,
        "bytes_scanned": scan.bytes_scanned,
        "ruleset_version": scan.ruleset_version,
        "safe_ai_summary": None,
        "safe_error": None,
        "created_at": scan.completed_at.isoformat(),
        "updated_at": scan.completed_at.isoformat(),
        "started_at": scan.completed_at.isoformat(),
        "finished_at": scan.completed_at.isoformat(),
        "findings": [
            {
                "id": str(finding.finding_id),
                "fingerprint": finding.fingerprint,
                "rule_id": finding.rule_id,
                "severity": finding.severity.value,
                "detector": finding.detector.value,
                "cwe_id": finding.cwe_id,
                "owasp_category": _owasp_category(finding.cwe_id),
                "safe_message": finding.safe_message,
                "file_path": finding.file_path,
                "line_number": finding.line_number,
                "column_number": finding.column_number,
                "redacted_code_snippet": finding.code_snippet,
                "recommendation": finding.recommendation,
                "created_at": scan.completed_at.isoformat(),
            }
            for finding in scan.findings
        ],
    }


def _repository_dict(repository: CodeRepositoryRecord) -> dict[str, object]:
    return {
        "id": str(repository.id),
        "provider": repository.provider,
        "display_name": repository.display_name,
        "clone_url": repository.clone_url,
        "default_branch": repository.default_branch,
        "enabled": repository.enabled,
        "created_at": repository.created_at.isoformat(),
        "updated_at": repository.updated_at.isoformat(),
    }


def _settings_dict(policy: TenantCodeSecurityPolicy) -> dict[str, object]:
    return {
        "revision": policy.revision,
        "block_severity": policy.block_severity.value,
        "review_severity": policy.review_severity.value,
        "max_file_bytes": policy.max_file_bytes,
        "max_repository_files": policy.max_repository_files,
        "max_repository_bytes": policy.max_repository_bytes,
        "auto_scan_push": policy.auto_scan_push,
        "auto_scan_pull_request": policy.auto_scan_pull_request,
        "ai_summary_enabled": policy.ai_summary_enabled,
    }


def _finding_context(finding: CodeSecurityFindingRecord) -> SecurityFindingContext:
    return SecurityFindingContext(
        rule_id=finding.rule_id,
        severity=finding.severity,
        message=finding.safe_message,
        file_path=finding.file_path,
        line_number=finding.line_number,
        code_snippet=finding.redacted_code_snippet,
        recommendation=finding.recommendation,
        cwe_id=finding.cwe_id,
        owasp_category=finding.owasp_category,
    )


def _owasp_category(cwe_id: str | None) -> str | None:
    return {
        "CWE-22": "A01:2021",
        "CWE-78": "A03:2021",
        "CWE-79": "A03:2021",
        "CWE-89": "A03:2021",
        "CWE-95": "A03:2021",
        "CWE-798": "A07:2021",
    }.get(cwe_id or "")


def _queue_hash(*parts: str) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@router.get("/rules")
async def list_security_rules(
    response: Response,
    _principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
) -> dict[str, object]:
    _set_no_store(response)
    return {
        "ruleset_version": DEFAULT_RULE_SET_VERSION.value,
        "rules": [
            {
                "rule_id": rule.rule_id,
                "title": rule.title,
                "severity": rule.severity.value,
                "detectors": [detector.value for detector in rule.detectors],
                "safe_message": rule.safe_message,
                "recommendation": rule.recommendation,
                "cwe_id": rule.cwe_id,
                "owasp_category": _owasp_category(rule.cwe_id),
                "enabled": rule.enabled,
            }
            for rule in DEFAULT_SECURITY_RULES
        ],
    }


@router.post("/scans/snippet", status_code=status.HTTP_201_CREATED)
async def scan_snippet(
    body: SnippetScanRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    service: Annotated[AnalyzeCodeService, Depends(get_analyze_code_service)],
) -> dict[str, object]:
    try:
        scan = await service.analyze(
            actor=principal.actor,
            source=CodeScanSource.SNIPPET,
            source_label=body.source_label,
            code=body.code,
        )
    except DomainError:
        raise _invalid_request() from None
    _set_no_store(response)
    return _completed_scan_dict(scan)


@router.post("/scans/file", status_code=status.HTTP_201_CREATED)
async def scan_file(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    service: Annotated[AnalyzeCodeService, Depends(get_analyze_code_service)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
    file: Annotated[UploadFile, File(...)],
) -> dict[str, object]:
    filename = file.filename
    if (
        filename is None
        or not filename
        or filename != filename.strip()
        or filename != PurePosixPath(filename).name
        or "\\" in filename
        or len(filename) > 256
    ):
        raise _invalid_request()
    raw = await file.read(settings.code_security_max_file_size_bytes + 1)
    if len(raw) > settings.code_security_max_file_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds the configured limit",
        )
    try:
        code = raw.decode("utf-8")
        scan = await service.analyze(
            actor=principal.actor,
            source=CodeScanSource.FILE_UPLOAD,
            source_label=filename,
            code=code,
        )
    except (UnicodeDecodeError, DomainError):
        raise _invalid_request() from None
    finally:
        await file.close()
    _set_no_store(response)
    return _completed_scan_dict(scan)


@router.get("/scans")
async def list_scans(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    scans = await store.list_scans(tenant_id=principal.actor.tenant_id, limit=limit)
    _set_no_store(response)
    return {"scans": [_scan_dict(scan) for scan in scans]}


@router.get("/scans/{scan_id}")
async def get_scan(
    scan_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
) -> dict[str, object]:
    detail = await store.get_scan_detail(
        tenant_id=principal.actor.tenant_id,
        scan_id=CodeScanId(scan_id),
    )
    if detail is None:
        raise _not_found()
    _set_no_store(response)
    payload = _scan_dict(detail.scan, findings=detail.findings)
    payload["review"] = None if detail.review is None else _review_dict(detail.review)
    return payload


@router.post("/findings/{finding_id}/explanation")
async def explain_finding(
    finding_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    provider: Annotated[
        SecurityExplanationProvider,
        Depends(get_security_explanation_provider),
    ],
) -> dict[str, object]:
    finding = await store.get_finding(
        tenant_id=principal.actor.tenant_id,
        finding_id=SecurityFindingId(finding_id),
    )
    if finding is None:
        raise _not_found()
    try:
        explanation = await provider.explain(finding=_finding_context(finding))
    except SecurityAIUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security explanation service is unavailable",
        ) from None
    _set_no_store(response)
    return {
        "finding_id": str(finding.id),
        "advisory": True,
        "meaning": explanation.meaning,
        "exploitation": explanation.exploitation,
        "remediation_steps": list(explanation.remediation_steps),
        "secure_example": explanation.secure_example,
    }


@router.post("/findings/{finding_id}/fix")
async def suggest_finding_fix(
    finding_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    provider: Annotated[
        SecurityExplanationProvider,
        Depends(get_security_explanation_provider),
    ],
) -> dict[str, object]:
    finding = await store.get_finding(
        tenant_id=principal.actor.tenant_id,
        finding_id=SecurityFindingId(finding_id),
    )
    if finding is None:
        raise _not_found()
    try:
        suggestion = await provider.suggest_fix(finding=_finding_context(finding))
    except SecurityAIUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security explanation service is unavailable",
        ) from None
    _set_no_store(response)
    return {"finding_id": str(finding.id), "advisory": True, "suggestion": suggestion}


@router.post("/scans/{scan_id}/report")
async def generate_scan_report(
    scan_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    provider: Annotated[
        SecurityExplanationProvider,
        Depends(get_security_explanation_provider),
    ],
) -> dict[str, object]:
    detail = await store.get_scan_detail(
        tenant_id=principal.actor.tenant_id,
        scan_id=CodeScanId(scan_id),
    )
    if detail is None:
        raise _not_found()
    repository_name: str | None = None
    if detail.scan.repository_id is not None:
        repository = await store.get_repository(
            tenant_id=principal.actor.tenant_id,
            repository_id=CodeRepositoryId(detail.scan.repository_id),
        )
        repository_name = None if repository is None else repository.display_name
    try:
        report = await provider.generate_report(
            repository_name=repository_name,
            scan_status=detail.scan.gate_decision or detail.scan.status,
            findings=tuple(_finding_context(finding) for finding in detail.findings),
        )
    except SecurityAIUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security explanation service is unavailable",
        ) from None
    _set_no_store(response)
    return {"scan_id": str(detail.scan.id), "advisory": True, "report": report}


@router.get("/repositories")
async def list_repositories(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
) -> dict[str, object]:
    repositories = await store.list_repositories(tenant_id=principal.actor.tenant_id)
    _set_no_store(response)
    return {"repositories": [_repository_dict(repository) for repository in repositories]}


@router.post("/repositories", status_code=status.HTTP_201_CREATED)
async def add_repository(
    body: RepositoryCreateRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_admin)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> dict[str, object]:
    try:
        location = validate_repository_url(
            body.clone_url,
            allowed_hosts=settings.allowed_code_repository_hosts,
        )
        branch = validate_git_ref(body.default_branch, field_name="Default branch")
        provider = (
            CodeRepositoryProvider.GITHUB
            if location.provider == "github"
            else CodeRepositoryProvider.GENERIC_GIT
        )
        repository = await store.add_repository(
            tenant_id=principal.actor.tenant_id,
            repository_id=CodeRepositoryId.new(),
            provider=provider,
            display_name=body.display_name,
            clone_url=location.clone_url,
            normalized_url=location.normalized_url,
            default_branch=branch,
            actor_id=principal.actor.actor_id,
            created_at=UtcSystemClock.now(),
        )
    except ValueError:
        raise _invalid_request() from None
    except IntegrityError:
        raise _conflict() from None
    _set_no_store(response)
    return _repository_dict(repository)


async def _repository_or_404(
    *,
    store: SqlAlchemyCodeSecurityStore,
    principal: AuthenticatedPrincipal,
    repository_id: UUID,
) -> CodeRepositoryRecord:
    repository = await store.get_repository(
        tenant_id=principal.actor.tenant_id,
        repository_id=CodeRepositoryId(repository_id),
    )
    if repository is None or not repository.enabled:
        raise _not_found()
    return repository


@router.post("/repositories/{repository_id}/scans", status_code=status.HTTP_202_ACCEPTED)
async def queue_repository_scan(
    repository_id: UUID,
    body: RepositoryScanRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
) -> dict[str, object]:
    repository = await _repository_or_404(
        store=store,
        principal=principal,
        repository_id=repository_id,
    )
    try:
        branch = validate_git_ref(body.branch or repository.default_branch)
        record = await store.enqueue_scan(
            tenant_id=principal.actor.tenant_id,
            scan_id=CodeScanId.new(),
            repository_id=CodeRepositoryId(repository_id),
            source=CodeScanSource.REPOSITORY,
            source_label=f"repository:{repository.id}",
            branch=branch,
            base_branch=None,
            head_branch=None,
            content_hash=_queue_hash(str(repository.id), "repository", branch),
            webhook_delivery_id=None,
            queued_at=UtcSystemClock.now(),
        )
    except ValueError:
        raise _invalid_request() from None
    _set_no_store(response)
    return _scan_dict(record)


@router.post(
    "/repositories/{repository_id}/pull-request-scans",
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_pull_request_scan(
    repository_id: UUID,
    body: PullRequestScanRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
) -> dict[str, object]:
    repository = await _repository_or_404(
        store=store,
        principal=principal,
        repository_id=repository_id,
    )
    try:
        base_branch = validate_git_ref(body.base_branch, field_name="Base branch")
        head_branch = validate_git_ref(body.head_branch, field_name="Head branch")
        record = await store.enqueue_scan(
            tenant_id=principal.actor.tenant_id,
            scan_id=CodeScanId.new(),
            repository_id=CodeRepositoryId(repository_id),
            source=CodeScanSource.PULL_REQUEST,
            source_label=f"pull_request:{repository.id}",
            branch=None,
            base_branch=base_branch,
            head_branch=head_branch,
            content_hash=_queue_hash(
                str(repository.id),
                "pull_request",
                base_branch,
                head_branch,
            ),
            webhook_delivery_id=None,
            queued_at=UtcSystemClock.now(),
        )
    except ValueError:
        raise _invalid_request() from None
    _set_no_store(response)
    return _scan_dict(record)


@router.get("/settings")
async def get_security_settings(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
) -> dict[str, object]:
    policy = await store.get_policy(tenant_id=principal.actor.tenant_id)
    _set_no_store(response)
    return _settings_dict(policy)


@router.put("/settings")
async def update_security_settings(
    body: SecuritySettingsUpdateRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_admin)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> dict[str, object]:
    try:
        if (
            body.max_file_bytes > settings.code_security_max_file_size_bytes
            or body.max_repository_files > settings.code_security_max_repository_files
            or body.max_repository_bytes > settings.code_security_max_repository_bytes
        ):
            raise DomainError("Tenant code security limits exceed deployment limits")
        policy = TenantCodeSecurityPolicy(
            block_severity=SecuritySeverity(body.block_severity),
            review_severity=SecuritySeverity(body.review_severity),
            max_file_bytes=body.max_file_bytes,
            max_repository_files=body.max_repository_files,
            max_repository_bytes=body.max_repository_bytes,
            auto_scan_push=body.auto_scan_push,
            auto_scan_pull_request=body.auto_scan_pull_request,
            ai_summary_enabled=body.ai_summary_enabled,
        )
        updated = await store.update_policy(
            tenant_id=principal.actor.tenant_id,
            actor_id=principal.actor.actor_id,
            expected_revision=body.expected_revision,
            policy=policy,
            updated_at=UtcSystemClock.now(),
        )
    except DomainError:
        raise _invalid_request() from None
    except IntegrityError:
        # Two first-time writers can both observe revision zero. The database
        # primary key is the final arbiter; expose the same safe CAS conflict as
        # a stale revision instead of leaking a transaction error.
        raise _conflict() from None
    if updated is None:
        raise _conflict()
    _set_no_store(response)
    return _settings_dict(updated)


@router.post("/scans/{scan_id}/reviews", status_code=status.HTTP_201_CREATED)
async def review_scan(
    scan_id: UUID,
    body: ScanReviewRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_admin)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    status_publisher: Annotated[
        SecurityReviewStatusPublisher | None,
        Depends(get_security_review_status_publisher),
    ],
) -> dict[str, object]:
    detail = await store.get_scan_detail(
        tenant_id=principal.actor.tenant_id,
        scan_id=CodeScanId(scan_id),
    )
    if detail is None:
        raise _not_found()
    if detail.scan.gate_decision != GateDecision.REVIEW_REQUIRED.value:
        raise _conflict()
    repository = None
    if (
        detail.scan.source in {CodeScanSource.REPOSITORY.value, CodeScanSource.PULL_REQUEST.value}
        and detail.scan.repository_id is not None
        and detail.scan.commit_sha is not None
    ):
        repository = await store.get_repository(
            tenant_id=principal.actor.tenant_id,
            repository_id=CodeRepositoryId(detail.scan.repository_id),
        )
    decision = SecurityReviewDecision(body.decision)
    try:
        record = await store.add_review(
            tenant_id=principal.actor.tenant_id,
            review_id=SecurityReviewId.new(),
            scan_id=CodeScanId(scan_id),
            decision=decision,
            safe_note=body.safe_note,
            actor_id=principal.actor.actor_id,
            reviewed_at=UtcSystemClock.now(),
        )
    except IntegrityError:
        raise _conflict() from None
    if (
        status_publisher is not None
        and repository is not None
        and repository.provider == CodeRepositoryProvider.GITHUB.value
        and detail.scan.commit_sha is not None
    ):
        try:
            await status_publisher.publish_review_decision(
                clone_url=repository.clone_url,
                commit_sha=detail.scan.commit_sha,
                decision=decision,
            )
        except Exception:
            # The append-only review already committed. External status delivery
            # is best effort and logs only a fixed channel category.
            logger.warning(
                "code security review status publish failed",
                extra={"notification_channel": "github"},
            )
    _set_no_store(response)
    return _review_dict(record)


def _review_dict(review: CodeScanReviewRecord) -> dict[str, object]:
    return {
        "id": str(review.id),
        "scan_id": str(review.scan_id),
        "decision": review.decision,
        "safe_note": review.safe_note,
        "reviewed_by": str(review.reviewed_by),
        "reviewed_at": review.reviewed_at.isoformat(),
    }


@router.get("/dashboard")
async def get_security_dashboard(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_code_security_user)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
) -> dict[str, object]:
    dashboard = await store.get_dashboard(
        tenant_id=principal.actor.tenant_id,
        recent_limit=10,
        trend_days=7,
    )
    titles = {rule.rule_id: rule.title for rule in DEFAULT_SECURITY_RULES}
    top = [
        {
            "rule_id": rule_id,
            "title": titles.get(rule_id, "Security finding"),
            "count": count,
            "severity": severity,
            "cwe_id": cwe_id,
        }
        for rule_id, severity, cwe_id, count in dashboard.top_vulnerabilities
    ]
    trend = [
        {
            "date": day,
            "total": total,
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "info": info,
        }
        for day, total, critical, high, medium, low, info in dashboard.trend
    ]
    _set_no_store(response)
    return {
        "counts": dashboard.counts,
        "recent_scans": [_scan_dict(scan) for scan in dashboard.recent_scans],
        "top_vulnerabilities": top,
        "trend": trend,
    }


@router.post(
    "/webhooks/github/{tenant_id}/{repository_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_github_webhook(
    tenant_id: UUID,
    repository_id: UUID,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_runtime_settings)],
    store: Annotated[SqlAlchemyCodeSecurityStore, Depends(get_code_security_store)],
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    event_type: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    delivery_id: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
) -> dict[str, object]:
    secret = settings.code_security_webhook_secret
    if not settings.code_security_enabled or secret is None:
        raise _not_found()
    body = await _read_bounded_webhook_body(request)
    repository_secret = derive_repository_webhook_secret(
        master_secret=secret.get_secret_value(),
        tenant_id=tenant_id,
        repository_id=repository_id,
    )
    if signature is None or not verify_github_signature(
        body=body,
        signature=signature,
        secret=repository_secret,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    if (
        event_type is None
        or delivery_id is None
        or not delivery_id
        or delivery_id != delivery_id.strip()
        or not delivery_id.isprintable()
        or len(delivery_id) > MAX_WEBHOOK_DELIVERY_ID_LENGTH
    ):
        raise _invalid_request()
    try:
        event = parse_github_scan_event(event_type=event_type, body=body)
    except ValueError:
        raise _invalid_request() from None
    if event is None:
        _set_no_store(response)
        return {"accepted": False, "reason": "event_ignored", "scan_id": None}

    repository = await store.get_repository(
        tenant_id=TenantId(tenant_id),
        repository_id=CodeRepositoryId(repository_id),
    )
    if repository is None or not repository.enabled:
        raise _not_found()
    try:
        incoming = validate_repository_url(
            event.clone_url,
            allowed_hosts=settings.allowed_code_repository_hosts,
        )
    except ValueError:
        raise _invalid_request() from None
    if incoming.normalized_url != repository.normalized_url:
        raise _not_found()

    policy = await store.get_policy(tenant_id=TenantId(tenant_id))
    if (event.event_type == "push" and not policy.auto_scan_push) or (
        event.event_type == "pull_request" and not policy.auto_scan_pull_request
    ):
        _set_no_store(response)
        return {"accepted": False, "reason": "automatic_scan_disabled", "scan_id": None}

    is_pull_request = event.event_type == "pull_request"
    scan_id = CodeScanId.new()
    try:
        record = await store.enqueue_scan(
            tenant_id=TenantId(tenant_id),
            scan_id=scan_id,
            repository_id=CodeRepositoryId(repository_id),
            source=CodeScanSource.WEBHOOK,
            source_label=f"github:{event.event_type}:{delivery_id}",
            branch=None if is_pull_request else event.branch,
            base_branch=event.base_branch if is_pull_request else None,
            head_branch=event.head_branch if is_pull_request else None,
            content_hash=hashlib.sha256(body).hexdigest(),
            webhook_delivery_id=delivery_id,
            queued_at=UtcSystemClock.now(),
        )
    except IntegrityError:
        _set_no_store(response)
        return {"accepted": True, "reason": "duplicate_delivery", "scan_id": None}
    _set_no_store(response)
    return {"accepted": True, "reason": "queued", "scan_id": str(record.id)}


async def _read_bounded_webhook_body(request: Request) -> bytes:
    """Read a webhook incrementally without ever buffering more than 1 MiB."""
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            raise _invalid_request() from None
        if content_length < 0:
            raise _invalid_request()
        if content_length > MAX_GITHUB_WEBHOOK_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Payload too large",
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_GITHUB_WEBHOOK_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Payload too large",
            )
        body.extend(chunk)
    return bytes(body)
