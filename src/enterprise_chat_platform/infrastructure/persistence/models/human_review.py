"""SQLAlchemy model for durable policy-escalation reviews."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.domain.governance import PolicyAction
from enterprise_chat_platform.domain.human_review import (
    HumanReviewResolution,
    HumanReviewStatus,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)


class HumanReviewRecord(Base):
    """Mutable workflow state for one escalated input-policy decision."""

    __tablename__ = "human_reviews"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    policy_action: Mapped[str] = mapped_column(
        String(16, collation="C"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16, collation="C"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolution: Mapped[str | None] = mapped_column(
        String(16, collation="C"),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "agent_run_id", "policy_action"],
            [
                f"{APP_SCHEMA}.input_policy_assessments.tenant_id",
                f"{APP_SCHEMA}.input_policy_assessments.agent_run_id",
                f"{APP_SCHEMA}.input_policy_assessments.action",
            ],
            name="fk_human_reviews_input_policy_assessment",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"policy_action = '{PolicyAction.ESCALATE.value}'",
            name="policy_action_escalate",
        ),
        CheckConstraint(
            (
                "status IN ("
                f"'{HumanReviewStatus.PENDING.value}', "
                f"'{HumanReviewStatus.RESOLVED.value}'"
                ")"
            ),
            name="status_valid",
        ),
        CheckConstraint(
            (
                "resolution IS NULL OR resolution IN ("
                f"'{HumanReviewResolution.CONFIRMED.value}', "
                f"'{HumanReviewResolution.DISMISSED.value}'"
                ")"
            ),
            name="resolution_valid",
        ),
        CheckConstraint("version >= 0", name="version_nonnegative"),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at",
            name="timestamps_ordered",
        ),
        CheckConstraint(
            (
                "(status = 'pending' AND resolved_by IS NULL "
                "AND resolved_at IS NULL AND resolution IS NULL AND version = 0) OR "
                "(status = 'resolved' AND resolved_by IS NOT NULL "
                "AND resolved_at IS NOT NULL AND resolution IS NOT NULL AND version = 1)"
            ),
            name="lifecycle_state_valid",
        ),
        Index(
            "ix_human_reviews_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
            "agent_run_id",
        ),
    )
