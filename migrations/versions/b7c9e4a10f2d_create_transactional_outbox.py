"""create transactional outbox

Revision ID: b7c9e4a10f2d
Revises: 00a8955cc7e6
Create Date: 2026-11-13 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers used by Alembic.
revision: str = "b7c9e4a10f2d"
down_revision: str | Sequence[str] | None = "00a8955cc7e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable, tenant-scoped outbox table."""
    op.create_table(
        "outbox_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "aggregate_type",
            sa.String(length=64, collation="C"),
            nullable=False,
        ),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "event_type",
            sa.String(length=128, collation="C"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "aggregate_type = 'conversation'",
            name=op.f("ck_outbox_events_aggregate_type_valid"),
        ),
        sa.CheckConstraint(
            "event_type ~ '^[a-z][a-z0-9_.-]*$'",
            name=op.f("ck_outbox_events_event_type_valid"),
        ),
        sa.CheckConstraint(
            "aggregate_version >= 0",
            name=op.f("ck_outbox_events_aggregate_version_nonnegative"),
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name=op.f("ck_outbox_events_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name=op.f("ck_outbox_events_payload_object"),
        ),
        sa.CheckConstraint(
            "available_at >= occurred_at",
            name=op.f("ck_outbox_events_availability_ordered"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_outbox_events_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            (
                "(attempt_count = 0 AND last_attempt_at IS NULL) OR "
                "(attempt_count > 0 AND last_attempt_at IS NOT NULL "
                "AND last_attempt_at >= occurred_at)"
            ),
            name=op.f("ck_outbox_events_attempt_state_valid"),
        ),
        sa.CheckConstraint(
            (
                "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
                "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
                "AND last_attempt_at IS NOT NULL "
                "AND lease_expires_at > last_attempt_at)"
            ),
            name=op.f("ck_outbox_events_lease_state_valid"),
        ),
        sa.CheckConstraint(
            (
                "published_at IS NULL OR "
                "(attempt_count > 0 AND last_attempt_at IS NOT NULL "
                "AND published_at >= last_attempt_at "
                "AND lease_token IS NULL AND lease_expires_at IS NULL)"
            ),
            name=op.f("ck_outbox_events_publication_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "aggregate_id"],
            ["chat.conversations.tenant_id", "chat.conversations.id"],
            name="fk_outbox_events_conversation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name=op.f("pk_outbox_events"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            name="uq_outbox_events_logical_event",
        ),
        schema="chat",
    )
    op.create_index(
        "ix_outbox_events_dispatch_pending",
        "outbox_events",
        [
            "available_at",
            "occurred_at",
            "tenant_id",
            "id",
        ],
        unique=False,
        schema="chat",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE chat.outbox_events FROM PUBLIC")


def downgrade() -> None:
    """Remove the transactional outbox table."""
    op.drop_index(
        "ix_outbox_events_dispatch_pending",
        table_name="outbox_events",
        schema="chat",
    )
    op.drop_table("outbox_events", schema="chat")
