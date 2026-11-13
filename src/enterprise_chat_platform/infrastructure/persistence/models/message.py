"""SQLAlchemy persistence model for immutable chat messages."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.domain.chat import (
    MAX_USER_MESSAGE_LENGTH,
    MessageRole,
)
from enterprise_chat_platform.domain.primitives import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)


class MessageRecord(Base):
    """Database representation of one immutable conversation message."""

    __tablename__ = "messages"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    author_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(MAX_IDEMPOTENCY_KEY_LENGTH),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [
                f"{APP_SCHEMA}.conversations.tenant_id",
                f"{APP_SCHEMA}.conversations.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
        ),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "id",
        ),
        CheckConstraint(
            (f"role IN ('{MessageRole.USER.value}', '{MessageRole.ASSISTANT.value}')"),
            name="role_valid",
        ),
        CheckConstraint(
            "sequence > 0",
            name="sequence_positive",
        ),
        CheckConstraint(
            (f"char_length(content) BETWEEN 1 AND {MAX_USER_MESSAGE_LENGTH}"),
            name="content_length_valid",
        ),
        CheckConstraint(
            "btrim(content) <> ''",
            name="content_not_blank",
        ),
        CheckConstraint(
            (f"char_length(idempotency_key) BETWEEN 1 AND {MAX_IDEMPOTENCY_KEY_LENGTH}"),
            name="idempotency_key_length_valid",
        ),
        CheckConstraint(
            "idempotency_key = btrim(idempotency_key)",
            name="idempotency_key_trimmed",
        ),
    )
