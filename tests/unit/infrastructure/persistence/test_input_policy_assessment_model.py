from typing import cast

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    String,
    Table,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from enterprise_chat_platform.domain.governance import (
    MAX_POLICY_FINDINGS,
    MAX_POLICY_VERSION_LENGTH,
    PolicyAction,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import APP_SCHEMA
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
    registered_model_metadata,
)


def _assessment_table() -> Table:
    return cast(Table, InputPolicyAssessmentRecord.__table__)


def test_assessment_record_has_expected_shape() -> None:
    table = _assessment_table()

    assert table.schema == APP_SCHEMA == "chat"
    assert table.name == "input_policy_assessments"
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "agent_run_id",
        "policy_version",
        "action",
        "risk_score",
        "findings",
        "assessed_at",
    )
    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "agent_run_id",
    )
    assert all(not column.nullable for column in table.columns)

    assert isinstance(table.c.tenant_id.type, Uuid)
    assert isinstance(table.c.agent_run_id.type, Uuid)

    policy_version_type = table.c.policy_version.type
    assert isinstance(policy_version_type, String)
    assert policy_version_type.length == MAX_POLICY_VERSION_LENGTH

    assert isinstance(table.c.action.type, String)
    assert isinstance(table.c.risk_score.type, Double)
    assert isinstance(table.c.findings.type, JSONB)
    assert isinstance(table.c.assessed_at.type, DateTime)
    assert table.c.assessed_at.type.timezone is True


def test_assessment_is_tenant_scoped_to_agent_run() -> None:
    table = _assessment_table()

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
        "fk_input_policy_assessments_agent_run": (
            ("tenant_id", "agent_run_id"),
            (
                "chat.agent_runs.tenant_id",
                "chat.agent_runs.id",
            ),
            "RESTRICT",
        )
    }


def test_assessment_preserves_policy_invariants() -> None:
    table = _assessment_table()

    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) == {
        "ck_input_policy_assessments_policy_version_valid",
        "ck_input_policy_assessments_action_valid",
        "ck_input_policy_assessments_risk_score_bounded",
        "ck_input_policy_assessments_findings_array",
        "ck_input_policy_assessments_findings_bounded",
        "ck_input_policy_assessments_non_allow_requires_finding",
    }

    action_check = checks["ck_input_policy_assessments_action_valid"]
    for action in PolicyAction:
        assert f"'{action.value}'" in action_check

    assert str(MAX_POLICY_FINDINGS) in checks["ck_input_policy_assessments_findings_bounded"]


def test_assessment_supports_tenant_action_dashboard_queries() -> None:
    table = _assessment_table()

    indexes = {
        str(index.name): tuple(column.name for column in index.columns) for index in table.indexes
    }

    assert indexes == {
        "ix_input_policy_assessments_tenant_action_assessed": (
            "tenant_id",
            "action",
            "assessed_at",
            "agent_run_id",
        )
    }


def test_model_registry_exposes_input_policy_assessments() -> None:
    metadata = registered_model_metadata()

    assert "chat.input_policy_assessments" in metadata.tables
    assert metadata.tables["chat.input_policy_assessments"] is InputPolicyAssessmentRecord.__table__
