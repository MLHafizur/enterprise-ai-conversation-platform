"""SQLAlchemy model for durable idempotency receipts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.application.accept_user_turn import (
    ACCEPT_USER_TURN_OPERATION,
)
from enterprise_chat_platform.domain.primitives import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)

MAX_IDEMPOTENCY_OPERATION_LENGTH = 64
SHA256_HEX_LENGTH = 64


class IdempotencyReceiptRecord(Base):
    """Original result recorded for one idempotent request scope."""

    __tablename__ = "idempotency_receipts"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    operation: Mapped[str] = mapped_column(
        String(
            MAX_IDEMPOTENCY_OPERATION_LENGTH,
            collation="C",
        ),
        primary_key=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(
            MAX_IDEMPOTENCY_KEY_LENGTH,
            collation="C",
        ),
        primary_key=True,
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(
            SHA256_HEX_LENGTH,
            collation="C",
        ),
        nullable=False,
    )
    conversation_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    message_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "conversation_id",
            ],
            [
                f"{APP_SCHEMA}.conversations.tenant_id",
                f"{APP_SCHEMA}.conversations.id",
            ],
            name="fk_idempotency_receipts_conversation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "conversation_id",
                "message_id",
            ],
            [
                f"{APP_SCHEMA}.messages.tenant_id",
                f"{APP_SCHEMA}.messages.conversation_id",
                f"{APP_SCHEMA}.messages.id",
            ],
            name="fk_idempotency_receipts_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "conversation_id",
                "agent_run_id",
            ],
            [
                f"{APP_SCHEMA}.agent_runs.tenant_id",
                f"{APP_SCHEMA}.agent_runs.conversation_id",
                f"{APP_SCHEMA}.agent_runs.id",
            ],
            name="fk_idempotency_receipts_agent_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"operation = '{ACCEPT_USER_TURN_OPERATION}'",
            name="operation_valid",
        ),
        CheckConstraint(
            (f"char_length(idempotency_key) BETWEEN 1 AND {MAX_IDEMPOTENCY_KEY_LENGTH}"),
            name="idempotency_key_length_valid",
        ),
        CheckConstraint(
            "idempotency_key = btrim(idempotency_key)",
            name="idempotency_key_trimmed",
        ),
        CheckConstraint(
            "idempotency_key !~ '[[:cntrl:]]'",
            name="idempotency_key_printable",
        ),
        CheckConstraint(
            (f"request_fingerprint ~ '^[0-9a-f]{{{SHA256_HEX_LENGTH}}}$'"),
            name="request_fingerprint_valid",
        ),
        CheckConstraint(
            "conversation_version > 0",
            name="conversation_version_positive",
        ),
        CheckConstraint(
            "message_sequence > 0",
            name="message_sequence_positive",
        ),
    )
