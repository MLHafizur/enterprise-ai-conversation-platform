"""SQLAlchemy persistence model for conversations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.infrastructure.persistence.metadata import Base


class ConversationRecord(Base):
    """Database representation of a tenant-scoped conversation."""

    __tablename__ = "conversations"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    last_message_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="status_valid",
        ),
        CheckConstraint(
            "version >= 0",
            name="version_nonnegative",
        ),
        CheckConstraint(
            "last_message_sequence >= 0",
            name="last_message_sequence_nonnegative",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="timestamps_ordered",
        ),
    )
