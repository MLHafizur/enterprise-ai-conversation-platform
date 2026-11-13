"""SQLAlchemy persistence model for agent execution attempts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.domain.agent_run import (
    MAX_FAILURE_CODE_LENGTH,
    MAX_SAFE_FAILURE_MESSAGE_LENGTH,
    AgentRunStatus,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)


class AgentRunRecord(Base):
    """Mutable database state for one agent execution attempt."""

    __tablename__ = "agent_runs"

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
    input_message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    output_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(
        String(MAX_FAILURE_CODE_LENGTH),
        nullable=True,
    )
    failure_safe_message: Mapped[str | None] = mapped_column(
        String(MAX_SAFE_FAILURE_MESSAGE_LENGTH),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [
                f"{APP_SCHEMA}.conversations.tenant_id",
                f"{APP_SCHEMA}.conversations.id",
            ],
            name="fk_agent_runs_conversation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "conversation_id",
                "input_message_id",
            ],
            [
                f"{APP_SCHEMA}.messages.tenant_id",
                f"{APP_SCHEMA}.messages.conversation_id",
                f"{APP_SCHEMA}.messages.id",
            ],
            name="fk_agent_runs_input_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "conversation_id",
                "output_message_id",
            ],
            [
                f"{APP_SCHEMA}.messages.tenant_id",
                f"{APP_SCHEMA}.messages.conversation_id",
                f"{APP_SCHEMA}.messages.id",
            ],
            name="fk_agent_runs_output_message",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "status IN ("
                f"'{AgentRunStatus.QUEUED.value}', "
                f"'{AgentRunStatus.RUNNING.value}', "
                f"'{AgentRunStatus.SUCCEEDED.value}', "
                f"'{AgentRunStatus.FAILED.value}', "
                f"'{AgentRunStatus.CANCELLED.value}'"
                ")"
            ),
            name="status_valid",
        ),
        CheckConstraint(
            (
                "(started_at IS NULL OR started_at >= created_at) "
                "AND (finished_at IS NULL OR finished_at >= created_at) "
                "AND (started_at IS NULL OR finished_at IS NULL "
                "OR finished_at >= started_at)"
            ),
            name="timestamps_ordered",
        ),
        CheckConstraint(
            ("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_.-]*$'"),
            name="failure_code_valid",
        ),
        CheckConstraint(
            ("failure_safe_message IS NULL OR btrim(failure_safe_message) <> ''"),
            name="failure_safe_message_not_blank",
        ),
        CheckConstraint(
            ("failure_safe_message IS NULL OR failure_safe_message !~ '[[:cntrl:]]'"),
            name="failure_safe_message_printable",
        ),
        CheckConstraint(
            (
                "("
                f"status = '{AgentRunStatus.QUEUED.value}' "
                "AND started_at IS NULL "
                "AND finished_at IS NULL "
                "AND output_message_id IS NULL "
                "AND failure_code IS NULL "
                "AND failure_safe_message IS NULL"
                ") OR ("
                f"status = '{AgentRunStatus.RUNNING.value}' "
                "AND started_at IS NOT NULL "
                "AND finished_at IS NULL "
                "AND output_message_id IS NULL "
                "AND failure_code IS NULL "
                "AND failure_safe_message IS NULL"
                ") OR ("
                f"status = '{AgentRunStatus.SUCCEEDED.value}' "
                "AND started_at IS NOT NULL "
                "AND finished_at IS NOT NULL "
                "AND output_message_id IS NOT NULL "
                "AND failure_code IS NULL "
                "AND failure_safe_message IS NULL"
                ") OR ("
                f"status = '{AgentRunStatus.FAILED.value}' "
                "AND finished_at IS NOT NULL "
                "AND output_message_id IS NULL "
                "AND failure_code IS NOT NULL "
                "AND failure_safe_message IS NOT NULL"
                ") OR ("
                f"status = '{AgentRunStatus.CANCELLED.value}' "
                "AND finished_at IS NOT NULL "
                "AND output_message_id IS NULL "
                "AND failure_code IS NULL "
                "AND failure_safe_message IS NULL"
                ")"
            ),
            name="lifecycle_state_valid",
        ),
    )
