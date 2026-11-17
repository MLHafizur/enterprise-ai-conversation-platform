"""create code security persistence

Revision ID: f7b2c8d4e1a0
Revises: e6a1c4d9b2f0
Create Date: 2026-11-16 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "f7b2c8d4e1a0"
down_revision: str | Sequence[str] | None = "e6a1c4d9b2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant-scoped code security configuration and evidence."""
    _create_code_repositories()
    _create_code_scans()
    _create_code_security_findings()
    _create_code_security_settings()
    _create_code_scan_reviews()
    _harden_table_privileges()


def _create_code_repositories() -> None:
    op.create_table(
        "code_repositories",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("clone_url", sa.String(length=2048, collation="C"), nullable=False),
        sa.Column(
            "normalized_url",
            sa.String(length=2048, collation="C"),
            nullable=False,
        ),
        sa.Column(
            "default_branch",
            sa.String(length=255, collation="C"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "clone_url = btrim(clone_url) "
            "AND clone_url ~ '^https://[^/?#[:space:]]+(/[^?#[:space:]]*)?$' "
            "AND clone_url !~ '^https://[^/@]+@' "
            "AND clone_url !~ '[[:cntrl:]]'",
            name=op.f("ck_code_repositories_clone_url_safe"),
        ),
        sa.CheckConstraint(
            "default_branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND default_branch !~ '(^|/)\\.\\.(/|$)' "
            "AND default_branch !~ '//|@\\{'",
            name=op.f("ck_code_repositories_default_branch_valid"),
        ),
        sa.CheckConstraint(
            "display_name = btrim(display_name) AND display_name <> '' "
            "AND display_name !~ '[[:cntrl:]]'",
            name=op.f("ck_code_repositories_display_name_valid"),
        ),
        sa.CheckConstraint(
            "normalized_url = btrim(normalized_url) "
            "AND normalized_url ~ '^https://[^/?#[:space:]]+(/[^?#[:space:]]*)?$' "
            "AND normalized_url !~ '^https://[^/@]+@' "
            "AND normalized_url !~ '[[:cntrl:]]'",
            name=op.f("ck_code_repositories_normalized_url_safe"),
        ),
        sa.CheckConstraint(
            "provider IN ('github', 'gitlab', 'generic_git')",
            name=op.f("ck_code_repositories_provider_valid"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_code_repositories_timestamps_ordered"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name=op.f("pk_code_repositories"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "normalized_url",
            name="uq_code_repositories_tenant_normalized_url",
        ),
        schema="chat",
    )
    op.create_index(
        "ix_code_repositories_tenant_enabled_name",
        "code_repositories",
        ["tenant_id", "enabled", "display_name", "id"],
        unique=False,
        schema="chat",
    )


def _create_code_scans() -> None:
    op.create_table(
        "code_scans",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=24, collation="C"), nullable=False),
        sa.Column("status", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("phase", sa.String(length=24, collation="C"), nullable=False),
        sa.Column(
            "gate_decision",
            sa.String(length=24, collation="C"),
            nullable=True,
        ),
        sa.Column("branch", sa.String(length=255, collation="C"), nullable=True),
        sa.Column("base_branch", sa.String(length=255, collation="C"), nullable=True),
        sa.Column("head_branch", sa.String(length=255, collation="C"), nullable=True),
        sa.Column("commit_sha", sa.String(length=64, collation="C"), nullable=True),
        sa.Column("source_label", sa.String(length=256), nullable=False),
        sa.Column(
            "content_hash",
            sa.String(length=64, collation="C"),
            nullable=False,
        ),
        sa.Column("total_findings", sa.BigInteger(), nullable=False),
        sa.Column("critical_findings", sa.BigInteger(), nullable=False),
        sa.Column("high_findings", sa.BigInteger(), nullable=False),
        sa.Column("medium_findings", sa.BigInteger(), nullable=False),
        sa.Column("low_findings", sa.BigInteger(), nullable=False),
        sa.Column("info_findings", sa.BigInteger(), nullable=False),
        sa.Column("files_scanned", sa.BigInteger(), nullable=False),
        sa.Column("bytes_scanned", sa.BigInteger(), nullable=False),
        sa.Column(
            "ruleset_version",
            sa.String(length=64, collation="C"),
            nullable=False,
        ),
        sa.Column("safe_ai_summary", sa.String(length=2000), nullable=True),
        sa.Column("safe_error", sa.String(length=512), nullable=True),
        sa.Column(
            "webhook_delivery_id",
            sa.String(length=128, collation="C"),
            nullable=True,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_code_scans_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "(attempt_count = 0 AND last_attempt_at IS NULL) OR "
            "(attempt_count > 0 AND last_attempt_at IS NOT NULL "
            "AND last_attempt_at >= created_at)",
            name=op.f("ck_code_scans_attempt_state_valid"),
        ),
        sa.CheckConstraint(
            "commit_sha IS NULL OR commit_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name=op.f("ck_code_scans_commit_sha_valid"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_code_scans_content_hash_valid"),
        ),
        sa.CheckConstraint(
            "total_findings BETWEEN 0 AND 100000 "
            "AND critical_findings >= 0 AND high_findings >= 0 "
            "AND medium_findings >= 0 AND low_findings >= 0 AND info_findings >= 0 "
            "AND total_findings = critical_findings + high_findings + "
            "medium_findings + low_findings + info_findings",
            name=op.f("ck_code_scans_finding_counts_valid"),
        ),
        sa.CheckConstraint(
            "gate_decision IS NULL OR gate_decision IN ('approve', 'review_required', 'block')",
            name=op.f("ck_code_scans_gate_decision_valid"),
        ),
        sa.CheckConstraint(
            "(branch IS NULL OR (branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND branch !~ '(^|/)\\.\\.(/|$)' AND branch !~ '//|@\\{')) "
            "AND (base_branch IS NULL OR (base_branch ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND base_branch !~ '(^|/)\\.\\.(/|$)' AND base_branch !~ '//|@\\{')) "
            "AND (head_branch IS NULL OR (head_branch ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
            "AND head_branch !~ '(^|/)\\.\\.(/|$)' AND head_branch !~ '//|@\\{'))",
            name=op.f("ck_code_scans_git_refs_valid"),
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND last_attempt_at IS NOT NULL AND lease_expires_at > last_attempt_at)",
            name=op.f("ck_code_scans_lease_state_valid"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_code_scans_lifecycle_state_valid"),
        ),
        sa.CheckConstraint(
            "phase IN ('received', 'validating', 'parsing', 'detecting', "
            "'deduplicating', 'redacting', 'gating', 'completed')",
            name=op.f("ck_code_scans_phase_valid"),
        ),
        sa.CheckConstraint(
            "ruleset_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'",
            name=op.f("ck_code_scans_ruleset_version_valid"),
        ),
        sa.CheckConstraint(
            "safe_ai_summary IS NULL OR "
            "(safe_ai_summary = btrim(safe_ai_summary) AND safe_ai_summary <> '' "
            "AND safe_ai_summary !~ '[[:cntrl:]]')",
            name=op.f("ck_code_scans_safe_ai_summary_valid"),
        ),
        sa.CheckConstraint(
            "safe_error IS NULL OR "
            "(safe_error = btrim(safe_error) AND safe_error <> '' "
            "AND safe_error !~ '[[:cntrl:]]')",
            name=op.f("ck_code_scans_safe_error_valid"),
        ),
        sa.CheckConstraint(
            "files_scanned BETWEEN 0 AND 100000 AND bytes_scanned BETWEEN 0 AND 1073741824",
            name=op.f("ck_code_scans_scan_bounds_valid"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_code_scans_source_shape_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('snippet', 'file_upload', 'repository', 'pull_request', 'webhook', 'ci')",
            name=op.f("ck_code_scans_source_valid"),
        ),
        sa.CheckConstraint(
            "source_label = btrim(source_label) AND source_label <> '' "
            "AND source_label !~ '[[:cntrl:]]'",
            name=op.f("ck_code_scans_source_label_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_code_scans_status_valid"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND available_at >= created_at "
            "AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at))",
            name=op.f("ck_code_scans_timestamps_ordered"),
        ),
        sa.CheckConstraint(
            "webhook_delivery_id IS NULL OR "
            "(webhook_delivery_id = btrim(webhook_delivery_id) "
            "AND webhook_delivery_id <> '' "
            "AND webhook_delivery_id !~ '[[:cntrl:]]')",
            name=op.f("ck_code_scans_webhook_delivery_id_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "repository_id"],
            ["chat.code_repositories.tenant_id", "chat.code_repositories.id"],
            name="fk_code_scans_repository",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_code_scans")),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "gate_decision",
            name="uq_code_scans_tenant_id_gate_decision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "repository_id",
            "webhook_delivery_id",
            name="uq_code_scans_tenant_repository_delivery",
        ),
        schema="chat",
    )
    op.create_index(
        "ix_code_scans_dispatch_pending",
        "code_scans",
        ["available_at", "created_at", "tenant_id", "id"],
        unique=False,
        schema="chat",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_code_scans_tenant_repository_created",
        "code_scans",
        ["tenant_id", "repository_id", "created_at", "id"],
        unique=False,
        schema="chat",
    )
    op.create_index(
        "ix_code_scans_tenant_status_created",
        "code_scans",
        ["tenant_id", "status", "created_at", "id"],
        unique=False,
        schema="chat",
    )


def _create_code_security_findings() -> None:
    op.create_table(
        "code_security_findings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64, collation="C"), nullable=False),
        sa.Column("rule_id", sa.String(length=64, collation="C"), nullable=False),
        sa.Column("severity", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("detector", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("cwe_id", sa.String(length=32, collation="C"), nullable=True),
        sa.Column(
            "owasp_category",
            sa.String(length=32, collation="C"),
            nullable=True,
        ),
        sa.Column("safe_message", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("line_number", sa.BigInteger(), nullable=False),
        sa.Column("column_number", sa.BigInteger(), nullable=False),
        sa.Column("redacted_code_snippet", sa.String(length=512), nullable=False),
        sa.Column("recommendation", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "column_number >= 0",
            name=op.f("ck_code_security_findings_column_number_nonnegative"),
        ),
        sa.CheckConstraint(
            "cwe_id IS NULL OR cwe_id ~ '^CWE-[1-9][0-9]{0,4}$'",
            name=op.f("ck_code_security_findings_cwe_id_valid"),
        ),
        sa.CheckConstraint(
            "detector IN ('ast', 'pattern')",
            name=op.f("ck_code_security_findings_detector_valid"),
        ),
        sa.CheckConstraint(
            "file_path = btrim(file_path) AND file_path <> '' "
            "AND file_path !~ '[[:cntrl:]]' AND file_path !~ '^/' "
            "AND file_path !~ '(^|/)\\.\\.(/|$)'",
            name=op.f("ck_code_security_findings_file_path_valid"),
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_code_security_findings_fingerprint_valid"),
        ),
        sa.CheckConstraint(
            "line_number > 0",
            name=op.f("ck_code_security_findings_line_number_positive"),
        ),
        sa.CheckConstraint(
            "owasp_category IS NULL OR owasp_category ~ '^A[0-9]{2}(:[0-9]{4})?$'",
            name=op.f("ck_code_security_findings_owasp_category_valid"),
        ),
        sa.CheckConstraint(
            "recommendation = btrim(recommendation) AND recommendation <> '' "
            "AND recommendation !~ '[[:cntrl:]]'",
            name=op.f("ck_code_security_findings_recommendation_valid"),
        ),
        sa.CheckConstraint(
            "redacted_code_snippet = btrim(redacted_code_snippet) "
            "AND redacted_code_snippet <> '' "
            "AND redacted_code_snippet !~ '[[:cntrl:]]'",
            name=op.f("ck_code_security_findings_redacted_code_snippet_valid"),
        ),
        sa.CheckConstraint(
            "rule_id ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name=op.f("ck_code_security_findings_rule_id_valid"),
        ),
        sa.CheckConstraint(
            "safe_message = btrim(safe_message) AND safe_message <> '' "
            "AND safe_message !~ '[[:cntrl:]]'",
            name=op.f("ck_code_security_findings_safe_message_valid"),
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'low', 'medium', 'high', 'critical')",
            name=op.f("ck_code_security_findings_severity_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "scan_id"],
            ["chat.code_scans.tenant_id", "chat.code_scans.id"],
            name="fk_code_security_findings_scan",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name=op.f("pk_code_security_findings"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "scan_id",
            "fingerprint",
            name="uq_code_security_findings_scan_fingerprint",
        ),
        schema="chat",
    )
    op.create_index(
        "ix_code_security_findings_scan_severity",
        "code_security_findings",
        ["tenant_id", "scan_id", "severity", "created_at", "id"],
        unique=False,
        schema="chat",
    )
    op.create_index(
        "ix_code_security_findings_tenant_severity_created",
        "code_security_findings",
        ["tenant_id", "severity", "created_at", "id"],
        unique=False,
        schema="chat",
    )


def _create_code_security_settings() -> None:
    op.create_table(
        "code_security_settings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "block_severity",
            sa.String(length=16, collation="C"),
            nullable=False,
        ),
        sa.Column(
            "review_severity",
            sa.String(length=16, collation="C"),
            nullable=False,
        ),
        sa.Column("max_file_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_repository_files", sa.BigInteger(), nullable=False),
        sa.Column("max_repository_bytes", sa.BigInteger(), nullable=False),
        sa.Column("auto_scan_push", sa.Boolean(), nullable=False),
        sa.Column("auto_scan_pull_request", sa.Boolean(), nullable=False),
        sa.Column("ai_summary_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_file_bytes BETWEEN 1024 AND 10485760",
            name=op.f("ck_code_security_settings_max_file_bytes_valid"),
        ),
        sa.CheckConstraint(
            "max_repository_bytes BETWEEN 1 AND 1073741824 "
            "AND max_repository_bytes >= max_file_bytes",
            name=op.f("ck_code_security_settings_max_repository_bytes_valid"),
        ),
        sa.CheckConstraint(
            "max_repository_files BETWEEN 1 AND 100000",
            name=op.f("ck_code_security_settings_max_repository_files_valid"),
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name=op.f("ck_code_security_settings_revision_nonnegative"),
        ),
        sa.CheckConstraint(
            "CASE block_severity "
            "WHEN 'info' THEN 0 WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 END > "
            "CASE review_severity "
            "WHEN 'info' THEN 0 WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 END",
            name=op.f("ck_code_security_settings_severity_thresholds_ordered"),
        ),
        sa.CheckConstraint(
            "block_severity IN ('info', 'low', 'medium', 'high', 'critical') "
            "AND review_severity IN ('info', 'low', 'medium', 'high', 'critical')",
            name=op.f("ck_code_security_settings_severity_thresholds_valid"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            name=op.f("pk_code_security_settings"),
        ),
        schema="chat",
    )


def _create_code_scan_reviews() -> None:
    op.create_table(
        "code_scan_reviews",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column(
            "required_gate_decision",
            sa.String(length=24, collation="C"),
            server_default=sa.text("'review_required'"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("safe_note", sa.String(length=512), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'blocked')",
            name=op.f("ck_code_scan_reviews_decision_valid"),
        ),
        sa.CheckConstraint(
            "required_gate_decision = 'review_required'",
            name=op.f("ck_code_scan_reviews_required_gate_decision_valid"),
        ),
        sa.CheckConstraint(
            "safe_note IS NULL OR "
            "(safe_note = btrim(safe_note) AND safe_note <> '' "
            "AND safe_note !~ '[[:cntrl:]]')",
            name=op.f("ck_code_scan_reviews_safe_note_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "scan_id", "required_gate_decision"],
            [
                "chat.code_scans.tenant_id",
                "chat.code_scans.id",
                "chat.code_scans.gate_decision",
            ],
            name="fk_code_scan_reviews_review_required_scan",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name=op.f("pk_code_scan_reviews"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "scan_id",
            name="uq_code_scan_reviews_tenant_scan",
        ),
        schema="chat",
    )
    op.create_index(
        "ix_code_scan_reviews_tenant_reviewed",
        "code_scan_reviews",
        ["tenant_id", "reviewed_at", "id"],
        unique=False,
        schema="chat",
    )


def _harden_table_privileges() -> None:
    for table_name in (
        "code_repositories",
        "code_scans",
        "code_security_findings",
        "code_security_settings",
        "code_scan_reviews",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE chat.{table_name} FROM PUBLIC")

    op.execute(
        """
        DO $$
        DECLARE
            grantee_name name;
            immutable_table text;
        BEGIN
            FOREACH immutable_table IN ARRAY ARRAY[
                'code_security_findings',
                'code_scan_reviews'
            ]
            LOOP
                FOR grantee_name IN
                    SELECT DISTINCT grantee
                    FROM information_schema.role_table_grants
                    WHERE table_schema = 'chat'
                    AND table_name = immutable_table
                    AND grantee <> current_user
                    AND privilege_type IN ('UPDATE', 'DELETE')
                LOOP
                    EXECUTE format(
                        'REVOKE UPDATE, DELETE ON TABLE chat.%I FROM %I',
                        immutable_table,
                        grantee_name
                    );
                END LOOP;
            END LOOP;
        END
        $$
        """
    )


def downgrade() -> None:
    """Remove tenant-scoped code security configuration and evidence."""
    op.drop_index(
        "ix_code_scan_reviews_tenant_reviewed",
        table_name="code_scan_reviews",
        schema="chat",
    )
    op.drop_table("code_scan_reviews", schema="chat")
    op.drop_table("code_security_settings", schema="chat")
    op.drop_index(
        "ix_code_security_findings_tenant_severity_created",
        table_name="code_security_findings",
        schema="chat",
    )
    op.drop_index(
        "ix_code_security_findings_scan_severity",
        table_name="code_security_findings",
        schema="chat",
    )
    op.drop_table("code_security_findings", schema="chat")
    op.drop_index(
        "ix_code_scans_tenant_status_created",
        table_name="code_scans",
        schema="chat",
    )
    op.drop_index(
        "ix_code_scans_tenant_repository_created",
        table_name="code_scans",
        schema="chat",
    )
    op.drop_index(
        "ix_code_scans_dispatch_pending",
        table_name="code_scans",
        schema="chat",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_table("code_scans", schema="chat")
    op.drop_index(
        "ix_code_repositories_tenant_enabled_name",
        table_name="code_repositories",
        schema="chat",
    )
    op.drop_table("code_repositories", schema="chat")
