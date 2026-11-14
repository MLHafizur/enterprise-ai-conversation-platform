"""SQLAlchemy model for transactional outbox events."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)

OUTBOX_AGGREGATE_TYPE = "conversation"
MAX_OUTBOX_AGGREGATE_TYPE_LENGTH = 64
MAX_OUTBOX_EVENT_TYPE_LENGTH = 128


class OutboxEventRecord(Base):
    """Durable event awaiting asynchronous publication."""

    __tablename__ = "outbox_events"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    aggregate_type: Mapped[str] = mapped_column(
        String(
            MAX_OUTBOX_AGGREGATE_TYPE_LENGTH,
            collation="C",
        ),
        nullable=False,
    )
    aggregate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    aggregate_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(
            MAX_OUTBOX_EVENT_TYPE_LENGTH,
            collation="C",
        ),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "aggregate_id"],
            [
                f"{APP_SCHEMA}.conversations.tenant_id",
                f"{APP_SCHEMA}.conversations.id",
            ],
            name="fk_outbox_events_conversation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            name="uq_outbox_events_logical_event",
        ),
        CheckConstraint(
            f"aggregate_type = '{OUTBOX_AGGREGATE_TYPE}'",
            name="aggregate_type_valid",
        ),
        CheckConstraint(
            "event_type ~ '^[a-z][a-z0-9_.-]*$'",
            name="event_type_valid",
        ),
        CheckConstraint(
            "aggregate_version >= 0",
            name="aggregate_version_nonnegative",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="schema_version_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="payload_object",
        ),
        CheckConstraint(
            "available_at >= occurred_at",
            name="availability_ordered",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
        CheckConstraint(
            (
                "(attempt_count = 0 AND last_attempt_at IS NULL) OR "
                "(attempt_count > 0 AND last_attempt_at IS NOT NULL "
                "AND last_attempt_at >= occurred_at)"
            ),
            name="attempt_state_valid",
        ),
        CheckConstraint(
            (
                "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
                "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
                "AND last_attempt_at IS NOT NULL "
                "AND lease_expires_at > last_attempt_at)"
            ),
            name="lease_state_valid",
        ),
        CheckConstraint(
            (
                "published_at IS NULL OR "
                "(attempt_count > 0 AND last_attempt_at IS NOT NULL "
                "AND published_at >= last_attempt_at "
                "AND lease_token IS NULL AND lease_expires_at IS NULL)"
            ),
            name="publication_state_valid",
        ),
        Index(
            "ix_outbox_events_dispatch_pending",
            "available_at",
            "occurred_at",
            "tenant_id",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
