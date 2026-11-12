"""establish migration foundation

Revision ID: c2c5f5d2de31
Revises:
Create Date: 2026-11-11 21:51:24.705801

"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "c2c5f5d2de31"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Protect Alembic's migration history from non-owner roles."""
    op.execute("REVOKE ALL PRIVILEGES ON TABLE chat.alembic_version FROM PUBLIC")
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
                AND table_name = 'alembic_version'
                AND grantee <> current_user
            LOOP
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE '
                    'chat.alembic_version FROM %I',
                    grantee_name
                );
            END LOOP;
        END
        $$
        """
    )


def downgrade() -> None:
    """Keep migration-history permissions restricted when returning to base."""
    pass
