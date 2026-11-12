from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    String,
    Table,
    Uuid,
)

from enterprise_chat_platform.infrastructure.persistence.metadata import APP_SCHEMA
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
    registered_model_metadata,
)


def _conversation_table() -> Table:
    """Give mypy the concrete type created by SQLAlchemy declarative mapping."""
    return cast(Table, ConversationRecord.__table__)


def test_conversation_record_has_the_expected_table_shape() -> None:
    table = _conversation_table()

    assert table.schema == APP_SCHEMA == "chat"
    assert table.name == "conversations"

    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "created_by",
        "status",
        "version",
        "last_message_sequence",
        "created_at",
        "updated_at",
    )

    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )

    assert all(not column.nullable for column in table.columns)

    assert isinstance(table.c.tenant_id.type, Uuid)
    assert isinstance(table.c.id.type, Uuid)
    assert isinstance(table.c.created_by.type, Uuid)

    status_type = table.c.status.type
    assert isinstance(status_type, String)
    assert status_type.length == 16

    assert isinstance(table.c.version.type, BigInteger)
    assert isinstance(table.c.last_message_sequence.type, BigInteger)

    created_at_type = table.c.created_at.type
    updated_at_type = table.c.updated_at.type

    assert isinstance(created_at_type, DateTime)
    assert isinstance(updated_at_type, DateTime)
    assert created_at_type.timezone is True
    assert updated_at_type.timezone is True


def test_conversation_record_preserves_domain_invariants() -> None:
    table = _conversation_table()

    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert checks == {
        "ck_conversations_status_valid": ("status IN ('active', 'archived')"),
        "ck_conversations_version_nonnegative": "version >= 0",
        "ck_conversations_last_message_sequence_nonnegative": ("last_message_sequence >= 0"),
        "ck_conversations_timestamps_ordered": ("updated_at >= created_at"),
    }


def test_model_registry_exposes_conversation_to_migrations() -> None:
    metadata = registered_model_metadata()

    assert "chat.conversations" in metadata.tables
    assert metadata.tables["chat.conversations"] is ConversationRecord.__table__
