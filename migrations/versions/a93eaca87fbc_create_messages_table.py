"""create messages table

Revision ID: a93eaca87fbc
Revises: 2f95df906d05
Create Date: 2026-11-12 19:24:55.613459

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "a93eaca87fbc"
down_revision: str | Sequence[str] | None = "2f95df906d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "messages",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("btrim(content) <> ''", name=op.f("ck_messages_content_not_blank")),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role_valid")),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 16000",
            name=op.f("ck_messages_content_length_valid"),
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 128",
            name=op.f("ck_messages_idempotency_key_length_valid"),
        ),
        sa.CheckConstraint(
            "idempotency_key = btrim(idempotency_key)",
            name=op.f("ck_messages_idempotency_key_trimmed"),
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_messages_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["chat.conversations.tenant_id", "chat.conversations.id"],
            name=op.f("fk_messages_tenant_id_conversation_id_conversations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
            name=op.f("uq_messages_tenant_id_conversation_id_sequence"),
        ),
        schema="chat",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("messages", schema="chat")
