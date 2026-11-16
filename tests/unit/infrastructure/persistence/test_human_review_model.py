from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)

from enterprise_chat_platform.infrastructure.persistence.metadata import APP_SCHEMA
from enterprise_chat_platform.infrastructure.persistence.models import (
    HumanReviewRecord,
    InputPolicyAssessmentRecord,
    registered_model_metadata,
)


def _review_table() -> Table:
    return cast(Table, HumanReviewRecord.__table__)


def test_human_review_has_expected_tenant_scoped_shape() -> None:
    table = _review_table()

    assert table.schema == APP_SCHEMA
    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "agent_run_id",
    )
    assert tuple(table.columns) == (
        table.c.tenant_id,
        table.c.agent_run_id,
        table.c.policy_action,
        table.c.status,
        table.c.version,
        table.c.created_at,
        table.c.resolved_by,
        table.c.resolved_at,
        table.c.resolution,
    )
    assert isinstance(table.c.tenant_id.type, Uuid)
    assert isinstance(table.c.agent_run_id.type, Uuid)
    assert isinstance(table.c.policy_action.type, String)
    assert isinstance(table.c.status.type, String)
    assert isinstance(table.c.version.type, BigInteger)
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True


def test_human_review_requires_the_exact_escalation_assessment() -> None:
    table = _review_table()
    foreign_keys = {
        str(constraint.name): (
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert foreign_keys == {
        "fk_human_reviews_input_policy_assessment": (
            ("tenant_id", "agent_run_id", "policy_action"),
            (
                "chat.input_policy_assessments.tenant_id",
                "chat.input_policy_assessments.agent_run_id",
                "chat.input_policy_assessments.action",
            ),
            "RESTRICT",
        )
    }
    assessment_table = cast(Table, InputPolicyAssessmentRecord.__table__)
    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in assessment_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "agent_run_id", "action") in unique_constraints


def test_human_review_enforces_two_exact_lifecycle_states() -> None:
    table = _review_table()
    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) == {
        "ck_human_reviews_policy_action_escalate",
        "ck_human_reviews_status_valid",
        "ck_human_reviews_resolution_valid",
        "ck_human_reviews_version_nonnegative",
        "ck_human_reviews_timestamps_ordered",
        "ck_human_reviews_lifecycle_state_valid",
    }
    lifecycle = checks["ck_human_reviews_lifecycle_state_valid"]
    assert "status = 'pending'" in lifecycle
    assert "version = 0" in lifecycle
    assert "status = 'resolved'" in lifecycle
    assert "version = 1" in lifecycle
    assert "resolved_by IS NOT NULL" in lifecycle


def test_human_review_queue_has_deterministic_tenant_status_index() -> None:
    table = _review_table()
    indexes = {
        str(index.name): tuple(column.name for column in index.columns) for index in table.indexes
    }

    assert indexes == {
        "ix_human_reviews_tenant_status_created": (
            "tenant_id",
            "status",
            "created_at",
            "agent_run_id",
        )
    }


def test_model_registry_exposes_human_reviews() -> None:
    metadata = registered_model_metadata()

    assert "chat.human_reviews" in metadata.tables
    assert metadata.tables["chat.human_reviews"] is HumanReviewRecord.__table__
