from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    CONSTRAINT_NAMING_CONVENTION,
    Base,
)


def test_base_metadata_uses_the_application_schema_and_naming_contract() -> None:
    assert Base.metadata.schema == APP_SCHEMA == "chat"
    assert Base.metadata.naming_convention == CONSTRAINT_NAMING_CONVENTION


def test_constraint_names_are_generated_deterministically() -> None:
    metadata = MetaData(
        schema=APP_SCHEMA,
        naming_convention=CONSTRAINT_NAMING_CONVENTION,
    )
    table = Table(
        "messages",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", String(36), nullable=False),
        Column("external_id", String(64), nullable=False),
        Column("content", String, nullable=False),
        UniqueConstraint("tenant_id", "external_id"),
        CheckConstraint("content <> ''", name="content_not_blank"),
    )

    unique_constraint = next(
        constraint for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    )
    check_constraint = next(
        constraint for constraint in table.constraints if isinstance(constraint, CheckConstraint)
    )

    assert table.schema == "chat"
    assert table.primary_key.name == "pk_messages"
    assert unique_constraint.name == "uq_messages_tenant_id_external_id"
    assert check_constraint.name == "ck_messages_content_not_blank"
