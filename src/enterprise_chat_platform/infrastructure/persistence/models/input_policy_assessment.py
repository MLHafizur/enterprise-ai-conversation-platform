"""Persistence model for immutable input-policy assessments."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.domain.governance import (
    MAX_POLICY_FINDINGS,
    MAX_POLICY_VERSION_LENGTH,
    PolicyAction,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)


class InputPolicyAssessmentRecord(Base):
    """One immutable, server-generated input-policy result per agent run."""

    __tablename__ = "input_policy_assessments"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    policy_version: Mapped[str] = mapped_column(
        String(MAX_POLICY_VERSION_LENGTH, collation="C"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(16, collation="C"),
        nullable=False,
    )
    risk_score: Mapped[float] = mapped_column(
        Double,
        nullable=False,
    )
    findings: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"],
            [
                f"{APP_SCHEMA}.agent_runs.tenant_id",
                f"{APP_SCHEMA}.agent_runs.id",
            ],
            name="fk_input_policy_assessments_agent_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "policy_version ~ '^[a-z0-9][a-z0-9._-]*$'",
            name="policy_version_valid",
        ),
        CheckConstraint(
            (
                "action IN ("
                f"'{PolicyAction.ALLOW.value}', "
                f"'{PolicyAction.BLOCK.value}', "
                f"'{PolicyAction.ESCALATE.value}'"
                ")"
            ),
            name="action_valid",
        ),
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 1",
            name="risk_score_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(findings) = 'array'",
            name="findings_array",
        ),
        CheckConstraint(
            f"jsonb_array_length(findings) <= {MAX_POLICY_FINDINGS}",
            name="findings_bounded",
        ),
        CheckConstraint(
            (f"action = '{PolicyAction.ALLOW.value}' OR jsonb_array_length(findings) > 0"),
            name="non_allow_requires_finding",
        ),
        Index(
            "ix_input_policy_assessments_tenant_action_assessed",
            "tenant_id",
            "action",
            "assessed_at",
            "agent_run_id",
        ),
    )
