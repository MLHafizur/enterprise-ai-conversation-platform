from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Uuid,
)

from enterprise_chat_platform.infrastructure.persistence.metadata import APP_SCHEMA
from enterprise_chat_platform.infrastructure.persistence.models import (
    AssistantProfileVersionRecord,
    TenantAssistantProfileAssignmentRecord,
    registered_model_metadata,
)


def _profile_table() -> Table:
    return cast(Table, AssistantProfileVersionRecord.__table__)


def _assignment_table() -> Table:
    return cast(Table, TenantAssistantProfileAssignmentRecord.__table__)


def test_profile_version_has_immutable_tenant_scoped_shape() -> None:
    table = _profile_table()

    assert table.schema == APP_SCHEMA
    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "profile_id",
        "version",
    )
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "profile_id",
        "version",
        "prompt_template_version",
        "assistant_name",
        "organization_name",
        "response_tone",
        "expertise",
        "communication_style",
        "context_character_limit",
        "created_at",
        "created_by",
    )
    assert not any(column.nullable for column in table.columns)
    assert isinstance(table.c.tenant_id.type, Uuid)
    assert isinstance(table.c.profile_id.type, Uuid)
    assert isinstance(table.c.version.type, BigInteger)
    assert isinstance(table.c.context_character_limit.type, Integer)
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True
    assert isinstance(table.c.prompt_template_version.type, String)
    assert table.c.prompt_template_version.type.collation == "C"


def test_profile_version_checks_match_typed_domain_allowlists() -> None:
    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in _profile_table().constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) == {
        "ck_assistant_profile_versions_version_positive",
        "ck_assistant_profile_versions_prompt_template_version_valid",
        "ck_assistant_profile_versions_assistant_name_valid",
        "ck_assistant_profile_versions_organization_name_valid",
        "ck_assistant_profile_versions_response_tone_valid",
        "ck_assistant_profile_versions_expertise_valid",
        "ck_assistant_profile_versions_communication_style_valid",
        "ck_assistant_profile_versions_context_character_limit_valid",
    }
    assert "professional" in checks["ck_assistant_profile_versions_response_tone_valid"]
    assert "friendly" in checks["ck_assistant_profile_versions_response_tone_valid"]
    assert "senior_engineer" in checks["ck_assistant_profile_versions_expertise_valid"]
    assert "product_specialist" in checks["ck_assistant_profile_versions_expertise_valid"]
    assert (
        "BETWEEN 1 AND 64000"
        in checks["ck_assistant_profile_versions_context_character_limit_valid"]
    )


def test_assignment_points_to_one_exact_profile_revision() -> None:
    table = _assignment_table()

    assert tuple(column.name for column in table.primary_key.columns) == ("tenant_id",)
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "profile_id",
        "profile_version",
        "revision",
        "activated_at",
        "activated_by",
    )
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
        "fk_tenant_assistant_profile_assignments_active_profile": (
            ("tenant_id", "profile_id", "profile_version"),
            (
                "chat.assistant_profile_versions.tenant_id",
                "chat.assistant_profile_versions.profile_id",
                "chat.assistant_profile_versions.version",
            ),
            "RESTRICT",
        )
    }


def test_model_registry_exposes_profile_tables_to_alembic() -> None:
    metadata = registered_model_metadata()

    assert metadata.tables["chat.assistant_profile_versions"] is (
        AssistantProfileVersionRecord.__table__
    )
    assert metadata.tables["chat.tenant_assistant_profile_assignments"] is (
        TenantAssistantProfileAssignmentRecord.__table__
    )
