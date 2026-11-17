"""SQLAlchemy models for tenant-scoped source-code security analysis."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.domain.code_security import (
    MAX_CODE_SNIPPET_LENGTH,
    MAX_CONTENT_HASH_LENGTH,
    MAX_CWE_ID_LENGTH,
    MAX_FILE_PATH_LENGTH,
    MAX_GIT_REF_LENGTH,
    MAX_OWASP_CATEGORY_LENGTH,
    MAX_RECOMMENDATION_LENGTH,
    MAX_REPOSITORY_DISPLAY_NAME_LENGTH,
    MAX_REPOSITORY_URL_LENGTH,
    MAX_RULE_ID_LENGTH,
    MAX_RULE_SET_VERSION_LENGTH,
    MAX_SAFE_AI_SUMMARY_LENGTH,
    MAX_SAFE_MESSAGE_LENGTH,
    MAX_SAFE_SCAN_ERROR_LENGTH,
    MAX_SECURITY_FILE_BYTES,
    MAX_SECURITY_FINDINGS,
    MAX_SECURITY_REPOSITORY_BYTES,
    MAX_SECURITY_REPOSITORY_FILES,
    MAX_SOURCE_LABEL_LENGTH,
    MAX_WEBHOOK_DELIVERY_ID_LENGTH,
    CodeRepositoryProvider,
    CodeScanPhase,
    CodeScanSource,
    CodeScanStatus,
    GateDecision,
    SecurityDetector,
    SecurityReviewDecision,
    SecuritySeverity,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)


class CodeRepositoryRecord(Base):
    """One tenant-owned repository that may be scanned."""

    __tablename__ = "code_repositories"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16, collation="C"), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(MAX_REPOSITORY_DISPLAY_NAME_LENGTH),
        nullable=False,
    )
    clone_url: Mapped[str] = mapped_column(
        String(MAX_REPOSITORY_URL_LENGTH, collation="C"),
        nullable=False,
    )
    normalized_url: Mapped[str] = mapped_column(
        String(MAX_REPOSITORY_URL_LENGTH, collation="C"),
        nullable=False,
    )
    default_branch: Mapped[str] = mapped_column(
        String(MAX_GIT_REF_LENGTH, collation="C"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "normalized_url",
            name="uq_code_repositories_tenant_normalized_url",
        ),
        CheckConstraint(
            "provider IN ("
            f"'{CodeRepositoryProvider.GITHUB.value}', "
            f"'{CodeRepositoryProvider.GITLAB.value}', "
            f"'{CodeRepositoryProvider.GENERIC_GIT.value}'"
            ")",
            name="provider_valid",
        ),
        CheckConstraint(
            "display_name = btrim(display_name) AND display_name <> '' "
            "AND display_name !~ '[[:cntrl:]]'",
            name="display_name_valid",
        ),
        CheckConstraint(
            "clone_url = btrim(clone_url) "
            "AND clone_url ~ '^https://[^/?#[:space:]]+(/[^?#[:space:]]*)?$' "
            "AND clone_url !~ '^https://[^/@]+@' "
            "AND clone_url !~ '[[:cntrl:]]'",
            name="clone_url_safe",
        ),
        CheckConstraint(
            "normalized_url = btrim(normalized_url) "
            "AND normalized_url ~ '^https://[^/?#[:space:]]+(/[^?#[:space:]]*)?$' "
            "AND normalized_url !~ '^https://[^/@]+@' "
            "AND normalized_url !~ '[[:cntrl:]]'",
            name="normalized_url_safe",
        ),
        CheckConstraint(
            "default_branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND default_branch !~ '(^|/)\\.\\.(/|$)' "
            "AND default_branch !~ '//|@\\{'",
            name="default_branch_valid",
        ),
        CheckConstraint("updated_at >= created_at", name="timestamps_ordered"),
        Index(
            "ix_code_repositories_tenant_enabled_name",
            "tenant_id",
            "enabled",
            "display_name",
            "id",
        ),
    )


class CodeScanRecord(Base):
    """Durable queue and terminal summary for one security scan."""

    __tablename__ = "code_scans"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    source: Mapped[str] = mapped_column(String(24, collation="C"), nullable=False)
    status: Mapped[str] = mapped_column(String(16, collation="C"), nullable=False)
    phase: Mapped[str] = mapped_column(String(24, collation="C"), nullable=False)
    gate_decision: Mapped[str | None] = mapped_column(
        String(24, collation="C"),
        nullable=True,
    )
    branch: Mapped[str | None] = mapped_column(
        String(MAX_GIT_REF_LENGTH, collation="C"),
        nullable=True,
    )
    base_branch: Mapped[str | None] = mapped_column(
        String(MAX_GIT_REF_LENGTH, collation="C"),
        nullable=True,
    )
    head_branch: Mapped[str | None] = mapped_column(
        String(MAX_GIT_REF_LENGTH, collation="C"),
        nullable=True,
    )
    commit_sha: Mapped[str | None] = mapped_column(
        String(MAX_CONTENT_HASH_LENGTH, collation="C"),
        nullable=True,
    )
    source_label: Mapped[str] = mapped_column(
        String(MAX_SOURCE_LABEL_LENGTH),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(
        String(MAX_CONTENT_HASH_LENGTH, collation="C"),
        nullable=False,
    )
    total_findings: Mapped[int] = mapped_column(BigInteger, nullable=False)
    critical_findings: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_findings: Mapped[int] = mapped_column(BigInteger, nullable=False)
    medium_findings: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low_findings: Mapped[int] = mapped_column(BigInteger, nullable=False)
    info_findings: Mapped[int] = mapped_column(BigInteger, nullable=False)
    files_scanned: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bytes_scanned: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(
        String(MAX_RULE_SET_VERSION_LENGTH, collation="C"),
        nullable=False,
    )
    safe_ai_summary: Mapped[str | None] = mapped_column(
        String(MAX_SAFE_AI_SUMMARY_LENGTH),
        nullable=True,
    )
    safe_error: Mapped[str | None] = mapped_column(
        String(MAX_SAFE_SCAN_ERROR_LENGTH),
        nullable=True,
    )
    webhook_delivery_id: Mapped[str | None] = mapped_column(
        String(MAX_WEBHOOK_DELIVERY_ID_LENGTH, collation="C"),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "repository_id"],
            [
                f"{APP_SCHEMA}.code_repositories.tenant_id",
                f"{APP_SCHEMA}.code_repositories.id",
            ],
            name="fk_code_scans_repository",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "repository_id",
            "webhook_delivery_id",
            name="uq_code_scans_tenant_repository_delivery",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "gate_decision",
            name="uq_code_scans_tenant_id_gate_decision",
        ),
        CheckConstraint(
            "source IN ("
            f"'{CodeScanSource.SNIPPET.value}', "
            f"'{CodeScanSource.FILE_UPLOAD.value}', "
            f"'{CodeScanSource.REPOSITORY.value}', "
            f"'{CodeScanSource.PULL_REQUEST.value}', "
            f"'{CodeScanSource.WEBHOOK.value}', "
            f"'{CodeScanSource.CI.value}'"
            ")",
            name="source_valid",
        ),
        CheckConstraint(
            "status IN ("
            f"'{CodeScanStatus.QUEUED.value}', "
            f"'{CodeScanStatus.RUNNING.value}', "
            f"'{CodeScanStatus.SUCCEEDED.value}', "
            f"'{CodeScanStatus.FAILED.value}', "
            f"'{CodeScanStatus.CANCELLED.value}'"
            ")",
            name="status_valid",
        ),
        CheckConstraint(
            "phase IN ("
            f"'{CodeScanPhase.RECEIVED.value}', "
            f"'{CodeScanPhase.VALIDATING.value}', "
            f"'{CodeScanPhase.PARSING.value}', "
            f"'{CodeScanPhase.DETECTING.value}', "
            f"'{CodeScanPhase.DEDUPLICATING.value}', "
            f"'{CodeScanPhase.REDACTING.value}', "
            f"'{CodeScanPhase.GATING.value}', "
            f"'{CodeScanPhase.COMPLETED.value}'"
            ")",
            name="phase_valid",
        ),
        CheckConstraint(
            "gate_decision IS NULL OR gate_decision IN ("
            f"'{GateDecision.APPROVE.value}', "
            f"'{GateDecision.REVIEW_REQUIRED.value}', "
            f"'{GateDecision.BLOCK.value}'"
            ")",
            name="gate_decision_valid",
        ),
        CheckConstraint(
            "source_label = btrim(source_label) AND source_label <> '' "
            "AND source_label !~ '[[:cntrl:]]'",
            name="source_label_valid",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="content_hash_valid",
        ),
        CheckConstraint(
            "commit_sha IS NULL OR commit_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name="commit_sha_valid",
        ),
        CheckConstraint(
            "(branch IS NULL OR (branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND branch !~ '(^|/)\\.\\.(/|$)' AND branch !~ '//|@\\{')) "
            "AND (base_branch IS NULL OR (base_branch ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND base_branch !~ '(^|/)\\.\\.(/|$)' AND base_branch !~ '//|@\\{')) "
            "AND (head_branch IS NULL OR (head_branch ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND head_branch !~ '(^|/)\\.\\.(/|$)' AND head_branch !~ '//|@\\{'))",
            name="git_refs_valid",
        ),
        CheckConstraint(
            "(source IN ('snippet', 'file_upload') "
            "AND repository_id IS NULL AND branch IS NULL AND base_branch IS NULL "
            "AND head_branch IS NULL AND commit_sha IS NULL "
            "AND webhook_delivery_id IS NULL) OR "
            "(source = 'repository' AND repository_id IS NOT NULL "
            "AND branch IS NOT NULL AND base_branch IS NULL AND head_branch IS NULL "
            "AND webhook_delivery_id IS NULL) OR "
            "(source = 'pull_request' AND repository_id IS NOT NULL "
            "AND branch IS NULL AND base_branch IS NOT NULL AND head_branch IS NOT NULL "
            "AND webhook_delivery_id IS NULL) OR "
            "(source = 'webhook' AND repository_id IS NOT NULL "
            "AND webhook_delivery_id IS NOT NULL "
            "AND ((branch IS NOT NULL AND base_branch IS NULL AND head_branch IS NULL) "
            "OR (branch IS NULL AND base_branch IS NOT NULL AND head_branch IS NOT NULL))) OR "
            "(source = 'ci' AND webhook_delivery_id IS NULL AND "
            "((repository_id IS NULL AND branch IS NULL AND base_branch IS NULL "
            "AND head_branch IS NULL AND commit_sha IS NULL) OR "
            "(repository_id IS NOT NULL AND "
            "((branch IS NOT NULL AND base_branch IS NULL AND head_branch IS NULL) "
            "OR (branch IS NULL AND base_branch IS NOT NULL AND head_branch IS NOT NULL)))))",
            name="source_shape_valid",
        ),
        CheckConstraint(
            "webhook_delivery_id IS NULL OR "
            "(webhook_delivery_id = btrim(webhook_delivery_id) "
            "AND webhook_delivery_id <> '' AND webhook_delivery_id !~ '[[:cntrl:]]')",
            name="webhook_delivery_id_valid",
        ),
        CheckConstraint(
            f"total_findings BETWEEN 0 AND {MAX_SECURITY_FINDINGS} "
            "AND critical_findings >= 0 AND high_findings >= 0 "
            "AND medium_findings >= 0 AND low_findings >= 0 AND info_findings >= 0 "
            "AND total_findings = critical_findings + high_findings + "
            "medium_findings + low_findings + info_findings",
            name="finding_counts_valid",
        ),
        CheckConstraint(
            f"files_scanned BETWEEN 0 AND {MAX_SECURITY_REPOSITORY_FILES} "
            f"AND bytes_scanned BETWEEN 0 AND {MAX_SECURITY_REPOSITORY_BYTES}",
            name="scan_bounds_valid",
        ),
        CheckConstraint(
            "ruleset_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'",
            name="ruleset_version_valid",
        ),
        CheckConstraint(
            "safe_ai_summary IS NULL OR "
            "(safe_ai_summary = btrim(safe_ai_summary) AND safe_ai_summary <> '' "
            "AND safe_ai_summary !~ '[[:cntrl:]]')",
            name="safe_ai_summary_valid",
        ),
        CheckConstraint(
            "safe_error IS NULL OR "
            "(safe_error = btrim(safe_error) AND safe_error <> '' "
            "AND safe_error !~ '[[:cntrl:]]')",
            name="safe_error_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(attempt_count = 0 AND last_attempt_at IS NULL) OR "
            "(attempt_count > 0 AND last_attempt_at IS NOT NULL "
            "AND last_attempt_at >= created_at)",
            name="attempt_state_valid",
        ),
        CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND last_attempt_at IS NOT NULL AND lease_expires_at > last_attempt_at)",
            name="lease_state_valid",
        ),
        CheckConstraint(
            "updated_at >= created_at AND available_at >= created_at "
            "AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at))",
            name="timestamps_ordered",
        ),
        CheckConstraint(
            "(status = 'queued' AND phase = 'received' AND finished_at IS NULL "
            "AND gate_decision IS NULL AND safe_error IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(status = 'running' AND phase NOT IN ('received', 'completed') "
            "AND started_at IS NOT NULL AND finished_at IS NULL "
            "AND gate_decision IS NULL AND safe_error IS NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status = 'succeeded' AND phase = 'completed' "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND gate_decision IS NOT NULL AND safe_error IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(status = 'failed' AND phase = 'completed' "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND gate_decision IS NULL AND safe_error IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(status = 'cancelled' AND phase = 'completed' "
            "AND finished_at IS NOT NULL AND gate_decision IS NULL "
            "AND safe_error IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="lifecycle_state_valid",
        ),
        Index(
            "ix_code_scans_dispatch_pending",
            "available_at",
            "created_at",
            "tenant_id",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_code_scans_tenant_repository_created",
            "tenant_id",
            "repository_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_code_scans_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
            "id",
        ),
    )


class CodeSecurityFindingRecord(Base):
    """Immutable, redacted evidence produced by one code scan."""

    __tablename__ = "code_security_findings"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    rule_id: Mapped[str] = mapped_column(
        String(MAX_RULE_ID_LENGTH, collation="C"),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(String(16, collation="C"), nullable=False)
    detector: Mapped[str] = mapped_column(String(16, collation="C"), nullable=False)
    cwe_id: Mapped[str | None] = mapped_column(
        String(MAX_CWE_ID_LENGTH, collation="C"),
        nullable=True,
    )
    owasp_category: Mapped[str | None] = mapped_column(
        String(MAX_OWASP_CATEGORY_LENGTH, collation="C"),
        nullable=True,
    )
    safe_message: Mapped[str] = mapped_column(
        String(MAX_SAFE_MESSAGE_LENGTH),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(MAX_FILE_PATH_LENGTH), nullable=False)
    line_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    column_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redacted_code_snippet: Mapped[str] = mapped_column(
        String(MAX_CODE_SNIPPET_LENGTH),
        nullable=False,
    )
    recommendation: Mapped[str] = mapped_column(
        String(MAX_RECOMMENDATION_LENGTH),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "scan_id"],
            [f"{APP_SCHEMA}.code_scans.tenant_id", f"{APP_SCHEMA}.code_scans.id"],
            name="fk_code_security_findings_scan",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "scan_id",
            "fingerprint",
            name="uq_code_security_findings_scan_fingerprint",
        ),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="fingerprint_valid",
        ),
        CheckConstraint(
            "rule_id ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="rule_id_valid",
        ),
        CheckConstraint(
            "severity IN ("
            f"'{SecuritySeverity.INFO.value}', "
            f"'{SecuritySeverity.LOW.value}', "
            f"'{SecuritySeverity.MEDIUM.value}', "
            f"'{SecuritySeverity.HIGH.value}', "
            f"'{SecuritySeverity.CRITICAL.value}'"
            ")",
            name="severity_valid",
        ),
        CheckConstraint(
            f"detector IN ('{SecurityDetector.AST.value}', '{SecurityDetector.PATTERN.value}')",
            name="detector_valid",
        ),
        CheckConstraint(
            "cwe_id IS NULL OR cwe_id ~ '^CWE-[1-9][0-9]{0,4}$'",
            name="cwe_id_valid",
        ),
        CheckConstraint(
            "owasp_category IS NULL OR owasp_category ~ '^A[0-9]{2}(:[0-9]{4})?$'",
            name="owasp_category_valid",
        ),
        CheckConstraint(
            "safe_message = btrim(safe_message) AND safe_message <> '' "
            "AND safe_message !~ '[[:cntrl:]]'",
            name="safe_message_valid",
        ),
        CheckConstraint(
            "file_path = btrim(file_path) AND file_path <> '' "
            "AND file_path !~ '[[:cntrl:]]' AND file_path !~ '^/' "
            "AND file_path !~ '(^|/)\\.\\.(/|$)'",
            name="file_path_valid",
        ),
        CheckConstraint("line_number > 0", name="line_number_positive"),
        CheckConstraint("column_number >= 0", name="column_number_nonnegative"),
        CheckConstraint(
            "redacted_code_snippet = btrim(redacted_code_snippet) "
            "AND redacted_code_snippet <> '' "
            "AND redacted_code_snippet !~ '[[:cntrl:]]'",
            name="redacted_code_snippet_valid",
        ),
        CheckConstraint(
            "recommendation = btrim(recommendation) AND recommendation <> '' "
            "AND recommendation !~ '[[:cntrl:]]'",
            name="recommendation_valid",
        ),
        Index(
            "ix_code_security_findings_scan_severity",
            "tenant_id",
            "scan_id",
            "severity",
            "created_at",
            "id",
        ),
        Index(
            "ix_code_security_findings_tenant_severity_created",
            "tenant_id",
            "severity",
            "created_at",
            "id",
        ),
    )


class CodeSecuritySettingsRecord(Base):
    """Mutable tenant policy controlling bounded code analysis."""

    __tablename__ = "code_security_settings"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_severity: Mapped[str] = mapped_column(
        String(16, collation="C"),
        nullable=False,
    )
    review_severity: Mapped[str] = mapped_column(
        String(16, collation="C"),
        nullable=False,
    )
    max_file_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_repository_files: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_repository_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    auto_scan_push: Mapped[bool] = mapped_column(Boolean, nullable=False)
    auto_scan_pull_request: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ai_summary_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "block_severity IN ('info', 'low', 'medium', 'high', 'critical') "
            "AND review_severity IN ('info', 'low', 'medium', 'high', 'critical')",
            name="severity_thresholds_valid",
        ),
        CheckConstraint(
            "CASE block_severity "
            "WHEN 'info' THEN 0 WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 END > "
            "CASE review_severity "
            "WHEN 'info' THEN 0 WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 END",
            name="severity_thresholds_ordered",
        ),
        CheckConstraint(
            f"max_file_bytes BETWEEN 1024 AND {MAX_SECURITY_FILE_BYTES}",
            name="max_file_bytes_valid",
        ),
        CheckConstraint(
            f"max_repository_files BETWEEN 1 AND {MAX_SECURITY_REPOSITORY_FILES}",
            name="max_repository_files_valid",
        ),
        CheckConstraint(
            f"max_repository_bytes BETWEEN 1 AND {MAX_SECURITY_REPOSITORY_BYTES} "
            "AND max_repository_bytes >= max_file_bytes",
            name="max_repository_bytes_valid",
        ),
    )


class CodeScanReviewRecord(Base):
    """Immutable authorized decision for one review-required scan."""

    __tablename__ = "code_scan_reviews"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    required_gate_decision: Mapped[str] = mapped_column(
        String(24, collation="C"),
        nullable=False,
        server_default=text(f"'{GateDecision.REVIEW_REQUIRED.value}'"),
    )
    decision: Mapped[str] = mapped_column(String(16, collation="C"), nullable=False)
    safe_note: Mapped[str | None] = mapped_column(
        String(MAX_SAFE_MESSAGE_LENGTH),
        nullable=True,
    )
    reviewed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "scan_id", "required_gate_decision"],
            [
                f"{APP_SCHEMA}.code_scans.tenant_id",
                f"{APP_SCHEMA}.code_scans.id",
                f"{APP_SCHEMA}.code_scans.gate_decision",
            ],
            name="fk_code_scan_reviews_review_required_scan",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "scan_id",
            name="uq_code_scan_reviews_tenant_scan",
        ),
        CheckConstraint(
            f"required_gate_decision = '{GateDecision.REVIEW_REQUIRED.value}'",
            name="required_gate_decision_valid",
        ),
        CheckConstraint(
            "decision IN ("
            f"'{SecurityReviewDecision.APPROVED.value}', "
            f"'{SecurityReviewDecision.BLOCKED.value}'"
            ")",
            name="decision_valid",
        ),
        CheckConstraint(
            "safe_note IS NULL OR "
            "(safe_note = btrim(safe_note) AND safe_note <> '' "
            "AND safe_note !~ '[[:cntrl:]]')",
            name="safe_note_valid",
        ),
        Index(
            "ix_code_scan_reviews_tenant_reviewed",
            "tenant_id",
            "reviewed_at",
            "id",
        ),
    )
