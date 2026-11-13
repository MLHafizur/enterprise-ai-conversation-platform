from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)

from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    MessageRecord,
    registered_model_metadata,
)


def _message_table() -> Table:
    """Give mypy the concrete type created by declarative mapping."""
    return cast(Table, MessageRecord.__table__)


def test_message_record_has_expected_table_shape() -> None:
    table = _message_table()

    assert table.schema == APP_SCHEMA == "chat"
    assert table.name == "messages"

    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "conversation_id",
        "author_id",
        "role",
        "content",
        "sequence",
        "idempotency_key",
        "created_at",
    )

    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )

    assert all(not column.nullable for column in table.columns)

    assert isinstance(table.c.tenant_id.type, Uuid)
    assert isinstance(table.c.id.type, Uuid)
    assert isinstance(table.c.conversation_id.type, Uuid)
    assert isinstance(table.c.author_id.type, Uuid)

    role_type = table.c.role.type
    assert isinstance(role_type, String)
    assert role_type.length == 16

    assert isinstance(table.c.content.type, Text)
    assert isinstance(table.c.sequence.type, BigInteger)

    idempotency_key_type = table.c.idempotency_key.type
    assert isinstance(idempotency_key_type, String)
    assert idempotency_key_type.length == 128

    created_at_type = table.c.created_at.type
    assert isinstance(created_at_type, DateTime)
    assert created_at_type.timezone is True


def test_message_record_has_tenant_safe_relationships_and_ordering() -> None:
    table = _message_table()

    foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]

    assert len(foreign_keys) == 1

    conversation_foreign_key = foreign_keys[0]

    assert str(conversation_foreign_key.name) == (
        "fk_messages_tenant_id_conversation_id_conversations"
    )
    assert tuple(conversation_foreign_key.column_keys) == (
        "tenant_id",
        "conversation_id",
    )
    assert tuple(element.target_fullname for element in conversation_foreign_key.elements) == (
        "chat.conversations.tenant_id",
        "chat.conversations.id",
    )
    assert conversation_foreign_key.ondelete == "RESTRICT"

    unique_constraints = {
        str(constraint.name): tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints == {
        "uq_messages_tenant_id_conversation_id_sequence": (
            "tenant_id",
            "conversation_id",
            "sequence",
        )
    }


def test_message_record_preserves_domain_invariants() -> None:
    table = _message_table()

    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert checks == {
        "ck_messages_role_valid": "role IN ('user', 'assistant')",
        "ck_messages_sequence_positive": "sequence > 0",
        "ck_messages_content_length_valid": ("char_length(content) BETWEEN 1 AND 16000"),
        "ck_messages_content_not_blank": "btrim(content) <> ''",
        "ck_messages_idempotency_key_length_valid": (
            "char_length(idempotency_key) BETWEEN 1 AND 128"
        ),
        "ck_messages_idempotency_key_trimmed": ("idempotency_key = btrim(idempotency_key)"),
    }


def test_model_registry_exposes_messages_to_migrations() -> None:
    metadata = registered_model_metadata()

    assert "chat.messages" in metadata.tables
    assert metadata.tables["chat.messages"] is MessageRecord.__table__
