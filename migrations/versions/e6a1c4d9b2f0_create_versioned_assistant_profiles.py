"""create versioned assistant profiles

Revision ID: e6a1c4d9b2f0
Revises: d4e8f1a2b3c4
Create Date: 2026-11-16 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "e6a1c4d9b2f0"
down_revision: str | Sequence[str] | None = "d4e8f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PROFILE_ID = "00000000-0000-4000-8000-000000000101"
DEFAULT_PROFILE_CREATED_BY = "00000000-0000-4000-8000-000000000102"


def upgrade() -> None:
    """Create immutable profiles, active pointers, and pinned run references."""
    op.create_table(
        "assistant_profile_versions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "prompt_template_version",
            sa.String(length=64, collation="C"),
            nullable=False,
        ),
        sa.Column("assistant_name", sa.String(length=128), nullable=False),
        sa.Column("organization_name", sa.String(length=128), nullable=False),
        sa.Column("response_tone", sa.String(length=16, collation="C"), nullable=False),
        sa.Column("expertise", sa.String(length=32, collation="C"), nullable=False),
        sa.Column(
            "communication_style",
            sa.String(length=32, collation="C"),
            nullable=False,
        ),
        sa.Column("context_character_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "assistant_name ~ '^[A-Za-z0-9][A-Za-z0-9 .&()''_-]{0,127}$'",
            name=op.f("ck_assistant_profile_versions_assistant_name_valid"),
        ),
        sa.CheckConstraint(
            "communication_style IN ('direct_helpful', 'concise_supportive')",
            name=op.f("ck_assistant_profile_versions_communication_style_valid"),
        ),
        sa.CheckConstraint(
            "context_character_limit BETWEEN 1 AND 64000",
            name=op.f("ck_assistant_profile_versions_context_character_limit_valid"),
        ),
        sa.CheckConstraint(
            "expertise IN ('senior_engineer', 'product_specialist')",
            name=op.f("ck_assistant_profile_versions_expertise_valid"),
        ),
        sa.CheckConstraint(
            "organization_name ~ '^[A-Za-z0-9][A-Za-z0-9 .&()''_-]{0,127}$'",
            name=op.f("ck_assistant_profile_versions_organization_name_valid"),
        ),
        sa.CheckConstraint(
            "prompt_template_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'",
            name=op.f("ck_assistant_profile_versions_prompt_template_version_valid"),
        ),
        sa.CheckConstraint(
            "response_tone IN ('professional', 'friendly')",
            name=op.f("ck_assistant_profile_versions_response_tone_valid"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_assistant_profile_versions_version_positive"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "profile_id",
            "version",
            name=op.f("pk_assistant_profile_versions"),
        ),
        schema="chat",
    )
    op.create_table(
        "tenant_assistant_profile_assignments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "profile_version > 0",
            name=op.f("ck_tenant_assistant_profile_assignments_profile_version_positive"),
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name=op.f("ck_tenant_assistant_profile_assignments_revision_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "profile_version"],
            [
                "chat.assistant_profile_versions.tenant_id",
                "chat.assistant_profile_versions.profile_id",
                "chat.assistant_profile_versions.version",
            ],
            name="fk_tenant_assistant_profile_assignments_active_profile",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            name=op.f("pk_tenant_assistant_profile_assignments"),
        ),
        schema="chat",
    )
    op.add_column(
        "agent_runs",
        sa.Column("assistant_profile_id", sa.Uuid(), nullable=True),
        schema="chat",
    )
    op.add_column(
        "agent_runs",
        sa.Column("assistant_profile_version", sa.BigInteger(), nullable=True),
        schema="chat",
    )

    # Every pre-existing tenant receives one deterministic v1 profile. Known
    # demo tenants retain their configured brand; every other tenant gets the
    # same safe generic profile that runtime lazy bootstrap uses.
    op.execute(
        f"""
        INSERT INTO chat.assistant_profile_versions (
            tenant_id,
            profile_id,
            version,
            prompt_template_version,
            assistant_name,
            organization_name,
            response_tone,
            expertise,
            communication_style,
            context_character_limit,
            created_at,
            created_by
        )
        SELECT
            tenant_id,
            '{DEFAULT_PROFILE_ID}'::uuid,
            1,
            'enterprise-chat.v1',
            CASE tenant_id::text
                WHEN '11111111-1111-4111-8111-111111111111' THEN 'Acme Assistant'
                WHEN '22222222-2222-4222-8222-222222222222' THEN 'TechStart Support'
                ELSE 'Enterprise Assistant'
            END,
            CASE tenant_id::text
                WHEN '11111111-1111-4111-8111-111111111111' THEN 'Acme Corp'
                WHEN '22222222-2222-4222-8222-222222222222' THEN 'TechStart Inc'
                ELSE 'your organization'
            END,
            CASE tenant_id::text
                WHEN '22222222-2222-4222-8222-222222222222' THEN 'friendly'
                ELSE 'professional'
            END,
            CASE tenant_id::text
                WHEN '22222222-2222-4222-8222-222222222222' THEN 'product_specialist'
                ELSE 'senior_engineer'
            END,
            CASE tenant_id::text
                WHEN '22222222-2222-4222-8222-222222222222' THEN 'concise_supportive'
                ELSE 'direct_helpful'
            END,
            CASE tenant_id::text
                WHEN '22222222-2222-4222-8222-222222222222' THEN 2048
                ELSE 4096
            END,
            first_created_at,
            '{DEFAULT_PROFILE_CREATED_BY}'::uuid
        FROM (
            SELECT tenant_id, min(created_at) AS first_created_at
            FROM chat.conversations
            GROUP BY tenant_id
        ) AS existing_tenants
        ON CONFLICT (tenant_id, profile_id, version) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO chat.tenant_assistant_profile_assignments (
            tenant_id,
            profile_id,
            profile_version,
            revision,
            activated_at,
            activated_by
        )
        SELECT
            tenant_id,
            profile_id,
            version,
            0,
            created_at,
            created_by
        FROM chat.assistant_profile_versions
        WHERE profile_id = '{DEFAULT_PROFILE_ID}'::uuid
          AND version = 1
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        UPDATE chat.agent_runs
        SET assistant_profile_id = '{DEFAULT_PROFILE_ID}'::uuid,
            assistant_profile_version = 1
        WHERE assistant_profile_id IS NULL
           OR assistant_profile_version IS NULL
        """
    )
    op.alter_column(
        "agent_runs",
        "assistant_profile_id",
        existing_type=sa.Uuid(),
        nullable=False,
        schema="chat",
    )
    op.alter_column(
        "agent_runs",
        "assistant_profile_version",
        existing_type=sa.BigInteger(),
        nullable=False,
        schema="chat",
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_assistant_profile_version_positive"),
        "agent_runs",
        "assistant_profile_version > 0",
        schema="chat",
    )
    op.create_foreign_key(
        "fk_agent_runs_assistant_profile",
        "agent_runs",
        "assistant_profile_versions",
        ["tenant_id", "assistant_profile_id", "assistant_profile_version"],
        ["tenant_id", "profile_id", "version"],
        source_schema="chat",
        referent_schema="chat",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_agent_runs_tenant_assistant_profile",
        "agent_runs",
        ["tenant_id", "assistant_profile_id", "assistant_profile_version"],
        unique=False,
        schema="chat",
    )

    op.execute("REVOKE ALL PRIVILEGES ON TABLE chat.assistant_profile_versions FROM PUBLIC")
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE chat.tenant_assistant_profile_assignments FROM PUBLIC"
    )
    _protect_runtime_columns()


def _protect_runtime_columns() -> None:
    """Make profile revisions and run pins immutable to runtime roles."""
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
                AND table_name = 'assistant_profile_versions'
                AND grantee <> current_user
                AND privilege_type IN ('UPDATE', 'DELETE')
            LOOP
                EXECUTE format(
                    'REVOKE UPDATE, DELETE ON TABLE '
                    'chat.assistant_profile_versions FROM %I',
                    grantee_name
                );
            END LOOP;

            FOR grantee_name IN
                SELECT DISTINCT grantee
                FROM information_schema.role_table_grants
                WHERE table_schema = 'chat'
                AND table_name = 'tenant_assistant_profile_assignments'
                AND grantee <> current_user
                AND privilege_type IN ('UPDATE', 'DELETE')
            LOOP
                EXECUTE format(
                    'REVOKE UPDATE, DELETE ON TABLE '
                    'chat.tenant_assistant_profile_assignments FROM %I',
                    grantee_name
                );
                EXECUTE format(
                    'GRANT UPDATE ('
                    'profile_id, profile_version, revision, activated_at, activated_by'
                    ') ON TABLE chat.tenant_assistant_profile_assignments TO %I',
                    grantee_name
                );
            END LOOP;

            FOR grantee_name IN
                SELECT DISTINCT grantee
                FROM information_schema.role_table_grants
                WHERE table_schema = 'chat'
                AND table_name = 'agent_runs'
                AND grantee <> current_user
                AND privilege_type IN ('UPDATE', 'DELETE')
            LOOP
                EXECUTE format(
                    'REVOKE UPDATE, DELETE ON TABLE chat.agent_runs FROM %I',
                    grantee_name
                );
                EXECUTE format(
                    'GRANT UPDATE ('
                    'status, started_at, finished_at, output_message_id, '
                    'failure_code, failure_safe_message'
                    ') ON TABLE chat.agent_runs TO %I',
                    grantee_name
                );
            END LOOP;
        END
        $$
        """
    )


def downgrade() -> None:
    """Remove versioned profile persistence and agent-run pins."""
    op.drop_index(
        "ix_agent_runs_tenant_assistant_profile",
        table_name="agent_runs",
        schema="chat",
    )
    op.drop_constraint(
        "fk_agent_runs_assistant_profile",
        "agent_runs",
        schema="chat",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_agent_runs_assistant_profile_version_positive"),
        "agent_runs",
        schema="chat",
        type_="check",
    )
    op.drop_column("agent_runs", "assistant_profile_version", schema="chat")
    op.drop_column("agent_runs", "assistant_profile_id", schema="chat")
    op.drop_table("tenant_assistant_profile_assignments", schema="chat")
    op.drop_table("assistant_profile_versions", schema="chat")

    # Restore the pre-migration runtime grants for the existing mutable table.
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
                AND table_name = 'agent_runs'
                AND grantee <> current_user
                AND privilege_type = 'INSERT'
            LOOP
                EXECUTE format(
                    'GRANT UPDATE, DELETE ON TABLE chat.agent_runs TO %I',
                    grantee_name
                );
            END LOOP;
        END
        $$
        """
    )
