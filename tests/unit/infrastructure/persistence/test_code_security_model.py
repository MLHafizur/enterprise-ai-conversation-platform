from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.schema import DefaultClause

from enterprise_chat_platform.infrastructure.persistence.metadata import APP_SCHEMA
from enterprise_chat_platform.infrastructure.persistence.models import (
    CodeRepositoryRecord,
    CodeScanRecord,
    CodeScanReviewRecord,
    CodeSecurityFindingRecord,
    CodeSecuritySettingsRecord,
    registered_model_metadata,
)


def _table(record: type[object]) -> Table:
    return cast(Table, record.__table__)  # type: ignore[attr-defined]


def _checks(table: Table) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _unique_constraints(table: Table) -> dict[str, tuple[str, ...]]:
    return {
        str(constraint.name): tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_keys(
    table: Table,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], str | None]]:
    return {
        str(constraint.name): (
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _indexes(table: Table) -> dict[str, tuple[str, ...]]:
    return {
        str(index.name): tuple(column.name for column in index.columns) for index in table.indexes
    }


def test_code_repository_has_tenant_safe_shape_and_identity() -> None:
    table = _table(CodeRepositoryRecord)

    assert table.schema == APP_SCHEMA
    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "provider",
        "display_name",
        "clone_url",
        "normalized_url",
        "default_branch",
        "enabled",
        "created_by",
        "created_at",
        "updated_at",
    )
    assert not any(column.nullable for column in table.columns)
    assert isinstance(table.c.tenant_id.type, Uuid)
    assert isinstance(table.c.provider.type, String)
    assert table.c.provider.type.collation == "C"
    assert isinstance(table.c.enabled.type, Boolean)
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True
    assert _unique_constraints(table) == {
        "uq_code_repositories_tenant_normalized_url": (
            "tenant_id",
            "normalized_url",
        )
    }
    assert _indexes(table) == {
        "ix_code_repositories_tenant_enabled_name": (
            "tenant_id",
            "enabled",
            "display_name",
            "id",
        )
    }


def test_code_repository_rejects_unsafe_urls_refs_and_metadata() -> None:
    checks = _checks(_table(CodeRepositoryRecord))

    assert set(checks) == {
        "ck_code_repositories_provider_valid",
        "ck_code_repositories_display_name_valid",
        "ck_code_repositories_clone_url_safe",
        "ck_code_repositories_normalized_url_safe",
        "ck_code_repositories_default_branch_valid",
        "ck_code_repositories_timestamps_ordered",
    }
    assert "generic_git" in checks["ck_code_repositories_provider_valid"]
    assert "^https://" in checks["ck_code_repositories_clone_url_safe"]
    assert "[^/@]+@" in checks["ck_code_repositories_clone_url_safe"]
    assert "\\.\\." in checks["ck_code_repositories_default_branch_valid"]


def test_code_scan_is_a_tenant_scoped_bounded_queue_record() -> None:
    table = _table(CodeScanRecord)

    assert table.schema == APP_SCHEMA
    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "repository_id",
        "source",
        "status",
        "phase",
        "gate_decision",
        "branch",
        "base_branch",
        "head_branch",
        "commit_sha",
        "source_label",
        "content_hash",
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
        "webhook_delivery_id",
        "attempt_count",
        "available_at",
        "last_attempt_at",
        "lease_token",
        "lease_expires_at",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    )
    assert isinstance(table.c.repository_id.type, Uuid)
    assert table.c.repository_id.nullable is True
    assert isinstance(table.c.total_findings.type, BigInteger)
    assert isinstance(table.c.attempt_count.type, Integer)
    assert _foreign_keys(table) == {
        "fk_code_scans_repository": (
            ("tenant_id", "repository_id"),
            ("chat.code_repositories.tenant_id", "chat.code_repositories.id"),
            "RESTRICT",
        )
    }
    assert _unique_constraints(table) == {
        "uq_code_scans_tenant_id_gate_decision": (
            "tenant_id",
            "id",
            "gate_decision",
        ),
        "uq_code_scans_tenant_repository_delivery": (
            "tenant_id",
            "repository_id",
            "webhook_delivery_id",
        ),
    }
    assert _indexes(table) == {
        "ix_code_scans_dispatch_pending": (
            "available_at",
            "created_at",
            "tenant_id",
            "id",
        ),
        "ix_code_scans_tenant_repository_created": (
            "tenant_id",
            "repository_id",
            "created_at",
            "id",
        ),
        "ix_code_scans_tenant_status_created": (
            "tenant_id",
            "status",
            "created_at",
            "id",
        ),
    }
    pending_index = next(
        index for index in table.indexes if index.name == "ix_code_scans_dispatch_pending"
    )
    assert str(pending_index.dialect_options["postgresql"]["where"]) == "status = 'queued'"


def test_code_scan_checks_bind_source_lifecycle_summary_and_lease() -> None:
    checks = _checks(_table(CodeScanRecord))

    assert set(checks) == {
        "ck_code_scans_source_valid",
        "ck_code_scans_status_valid",
        "ck_code_scans_phase_valid",
        "ck_code_scans_gate_decision_valid",
        "ck_code_scans_source_label_valid",
        "ck_code_scans_content_hash_valid",
        "ck_code_scans_commit_sha_valid",
        "ck_code_scans_git_refs_valid",
        "ck_code_scans_source_shape_valid",
        "ck_code_scans_webhook_delivery_id_valid",
        "ck_code_scans_finding_counts_valid",
        "ck_code_scans_scan_bounds_valid",
        "ck_code_scans_ruleset_version_valid",
        "ck_code_scans_safe_ai_summary_valid",
        "ck_code_scans_safe_error_valid",
        "ck_code_scans_attempt_count_nonnegative",
        "ck_code_scans_attempt_state_valid",
        "ck_code_scans_lease_state_valid",
        "ck_code_scans_timestamps_ordered",
        "ck_code_scans_lifecycle_state_valid",
    }
    assert "repository_id IS NULL" in checks["ck_code_scans_source_shape_valid"]
    assert "webhook_delivery_id IS NOT NULL" in checks["ck_code_scans_source_shape_valid"]
    assert "total_findings = critical_findings" in checks["ck_code_scans_finding_counts_valid"]
    assert "lease_token IS NOT NULL" in checks["ck_code_scans_lease_state_valid"]
    lifecycle = checks["ck_code_scans_lifecycle_state_valid"]
    assert "status = 'running'" in lifecycle
    assert "status = 'succeeded'" in lifecycle
    assert "gate_decision IS NOT NULL" in lifecycle
    assert "status = 'failed'" in lifecycle
    assert "safe_error IS NOT NULL" in lifecycle


def test_security_finding_is_immutable_redacted_tenant_evidence() -> None:
    table = _table(CodeSecurityFindingRecord)

    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "scan_id",
        "fingerprint",
        "rule_id",
        "severity",
        "detector",
        "cwe_id",
        "owasp_category",
        "safe_message",
        "file_path",
        "line_number",
        "column_number",
        "redacted_code_snippet",
        "recommendation",
        "created_at",
    )
    assert _foreign_keys(table) == {
        "fk_code_security_findings_scan": (
            ("tenant_id", "scan_id"),
            ("chat.code_scans.tenant_id", "chat.code_scans.id"),
            "RESTRICT",
        )
    }
    assert _unique_constraints(table) == {
        "uq_code_security_findings_scan_fingerprint": (
            "tenant_id",
            "scan_id",
            "fingerprint",
        )
    }
    assert _indexes(table) == {
        "ix_code_security_findings_scan_severity": (
            "tenant_id",
            "scan_id",
            "severity",
            "created_at",
            "id",
        ),
        "ix_code_security_findings_tenant_severity_created": (
            "tenant_id",
            "severity",
            "created_at",
            "id",
        ),
    }
    checks = _checks(table)
    assert "^[0-9a-f]{64}$" in checks["ck_code_security_findings_fingerprint_valid"]
    assert "file_path !~ '^/'" in checks["ck_code_security_findings_file_path_valid"]
    assert (
        "redacted_code_snippet <> ''"
        in checks["ck_code_security_findings_redacted_code_snippet_valid"]
    )
    assert "critical" in checks["ck_code_security_findings_severity_valid"]


def test_security_settings_have_versioned_bounded_tenant_policy() -> None:
    table = _table(CodeSecuritySettingsRecord)

    assert tuple(column.name for column in table.primary_key.columns) == ("tenant_id",)
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "revision",
        "block_severity",
        "review_severity",
        "max_file_bytes",
        "max_repository_files",
        "max_repository_bytes",
        "auto_scan_push",
        "auto_scan_pull_request",
        "ai_summary_enabled",
        "updated_by",
        "updated_at",
    )
    assert isinstance(table.c.revision.type, BigInteger)
    assert isinstance(table.c.auto_scan_push.type, Boolean)
    checks = _checks(table)
    assert set(checks) == {
        "ck_code_security_settings_revision_nonnegative",
        "ck_code_security_settings_severity_thresholds_valid",
        "ck_code_security_settings_severity_thresholds_ordered",
        "ck_code_security_settings_max_file_bytes_valid",
        "ck_code_security_settings_max_repository_files_valid",
        "ck_code_security_settings_max_repository_bytes_valid",
    }
    assert "1073741824" in checks["ck_code_security_settings_max_repository_bytes_valid"]
    assert (
        "max_repository_bytes >= max_file_bytes"
        in checks["ck_code_security_settings_max_repository_bytes_valid"]
    )


def test_scan_review_can_only_target_a_review_required_scan_once() -> None:
    table = _table(CodeScanReviewRecord)

    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "scan_id",
        "required_gate_decision",
        "decision",
        "safe_note",
        "reviewed_by",
        "reviewed_at",
    )
    server_default = table.c.required_gate_decision.server_default
    assert isinstance(server_default, DefaultClause)
    assert str(server_default.arg) == "'review_required'"
    assert _foreign_keys(table) == {
        "fk_code_scan_reviews_review_required_scan": (
            ("tenant_id", "scan_id", "required_gate_decision"),
            (
                "chat.code_scans.tenant_id",
                "chat.code_scans.id",
                "chat.code_scans.gate_decision",
            ),
            "RESTRICT",
        )
    }
    assert _unique_constraints(table) == {
        "uq_code_scan_reviews_tenant_scan": ("tenant_id", "scan_id")
    }
    assert _indexes(table) == {
        "ix_code_scan_reviews_tenant_reviewed": (
            "tenant_id",
            "reviewed_at",
            "id",
        )
    }
    checks = _checks(table)
    assert "review_required" in checks["ck_code_scan_reviews_required_gate_decision_valid"]
    assert "approved" in checks["ck_code_scan_reviews_decision_valid"]
    assert "blocked" in checks["ck_code_scan_reviews_decision_valid"]


def test_registry_exposes_every_code_security_table_to_alembic() -> None:
    metadata = registered_model_metadata()

    assert metadata.tables["chat.code_repositories"] is CodeRepositoryRecord.__table__
    assert metadata.tables["chat.code_scans"] is CodeScanRecord.__table__
    assert metadata.tables["chat.code_security_findings"] is (CodeSecurityFindingRecord.__table__)
    assert metadata.tables["chat.code_security_settings"] is (CodeSecuritySettingsRecord.__table__)
    assert metadata.tables["chat.code_scan_reviews"] is CodeScanReviewRecord.__table__
