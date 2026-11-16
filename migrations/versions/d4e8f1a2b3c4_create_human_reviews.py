"""create human reviews

Revision ID: d4e8f1a2b3c4
Revises: a72989b2dcec
Create Date: 2026-11-16 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "d4e8f1a2b3c4"
down_revision: str | Sequence[str] | None = "a72989b2dcec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tenant-scoped human-review workflow table."""
    op.create_unique_constraint(
        op.f("uq_input_policy_assessments_tenant_id_agent_run_id_action"),
        "input_policy_assessments",
        ["tenant_id", "agent_run_id", "action"],
        schema="chat",
    )
    op.create_table(
        "human_reviews",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("policy_action", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("status", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=16, collation="C"), nullable=True),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_by IS NULL AND resolved_at IS NULL "
            "AND resolution IS NULL AND version = 0) OR "
            "(status = 'resolved' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resolution IS NOT NULL AND version = 1)",
            name=op.f("ck_human_reviews_lifecycle_state_valid"),
        ),
        sa.CheckConstraint(
            "policy_action = 'escalate'",
            name=op.f("ck_human_reviews_policy_action_escalate"),
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('confirmed', 'dismissed')",
            name=op.f("ck_human_reviews_resolution_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved')",
            name=op.f("ck_human_reviews_status_valid"),
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at",
            name=op.f("ck_human_reviews_timestamps_ordered"),
        ),
        sa.CheckConstraint(
            "version >= 0",
            name=op.f("ck_human_reviews_version_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_run_id", "policy_action"],
            [
                "chat.input_policy_assessments.tenant_id",
                "chat.input_policy_assessments.agent_run_id",
                "chat.input_policy_assessments.action",
            ],
            name="fk_human_reviews_input_policy_assessment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "agent_run_id",
            name=op.f("pk_human_reviews"),
        ),
        schema="chat",
    )
    op.create_index(
        "ix_human_reviews_tenant_status_created",
        "human_reviews",
        ["tenant_id", "status", "created_at", "agent_run_id"],
        unique=False,
        schema="chat",
    )
    # Escalations that became terminal before this table existed will never be
    # claimed by the worker again. Seed their pending cases during the migration
    # so the review queue is complete immediately after deployment.
    op.execute(
        """
        INSERT INTO chat.human_reviews (
            tenant_id,
            agent_run_id,
            policy_action,
            status,
            version,
            created_at,
            resolved_by,
            resolved_at,
            resolution
        )
        SELECT
            tenant_id,
            agent_run_id,
            action,
            'pending',
            0,
            assessed_at,
            NULL,
            NULL,
            NULL
        FROM chat.input_policy_assessments
        WHERE action = 'escalate'
        ON CONFLICT (tenant_id, agent_run_id) DO NOTHING
        """
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE chat.human_reviews FROM PUBLIC")
    op.execute(
        """
        DO $$
        DECLARE
            grantee_name name;
        BEGIN
            FOR grantee_name IN
                SELECT DISTINCT grantee
                FROM information_schema.role_table_grants
                WHERE table_schema = 'chat'
                AND table_name = 'human_reviews'
                AND grantee <> current_user
                AND privilege_type IN ('UPDATE', 'DELETE')
            LOOP
                EXECUTE format(
                    'REVOKE UPDATE, DELETE ON TABLE chat.human_reviews FROM %I',
                    grantee_name
                );
                EXECUTE format(
                    'GRANT UPDATE ('
                    'status, version, resolved_by, resolved_at, resolution'
                    ') ON TABLE chat.human_reviews TO %I',
                    grantee_name
                );
            END LOOP;
        END
        $$
        """
    )


def downgrade() -> None:
    """Remove human-review workflow persistence."""
    op.drop_index(
        "ix_human_reviews_tenant_status_created",
        table_name="human_reviews",
        schema="chat",
    )
    op.drop_table("human_reviews", schema="chat")
    op.drop_constraint(
        op.f("uq_input_policy_assessments_tenant_id_agent_run_id_action"),
        "input_policy_assessments",
        schema="chat",
        type_="unique",
    )
