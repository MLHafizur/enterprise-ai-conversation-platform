from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Table,
    Uuid,
)

from enterprise_chat_platform.application.accept_user_turn import (
    ACCEPT_USER_TURN_OPERATION,
)
from enterprise_chat_platform.domain.primitives import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    IdempotencyReceiptRecord,
    registered_model_metadata,
)


def _receipt_table() -> Table:
    """Give mypy the concrete type created by declarative mapping."""
    return cast(Table, IdempotencyReceiptRecord.__table__)


def test_idempotency_receipt_has_expected_table_shape() -> None:
    table = _receipt_table()

    assert table.schema == APP_SCHEMA == "chat"
    assert table.name == "idempotency_receipts"

    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "conversation_id",
        "operation",
        "idempotency_key",
        "request_fingerprint",
        "conversation_version",
        "message_id",
        "message_sequence",
        "agent_run_id",
        "accepted_at",
    )

    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "conversation_id",
        "operation",
        "idempotency_key",
    )

    assert all(column.nullable is False for column in table.columns)

    for column_name in (
        "tenant_id",
        "conversation_id",
        "message_id",
        "agent_run_id",
    ):
        assert isinstance(table.c[column_name].type, Uuid)

    operation_type = table.c.operation.type
    assert isinstance(operation_type, String)
    assert operation_type.length == 64
    assert operation_type.collation == "C"

    key_type = table.c.idempotency_key.type
    assert isinstance(key_type, String)
    assert key_type.length == MAX_IDEMPOTENCY_KEY_LENGTH
    assert key_type.collation == "C"

    fingerprint_type = table.c.request_fingerprint.type
    assert isinstance(fingerprint_type, String)
    assert fingerprint_type.length == 64
    assert fingerprint_type.collation == "C"

    assert isinstance(table.c.conversation_version.type, BigInteger)
    assert isinstance(table.c.message_sequence.type, BigInteger)

    accepted_at_type = table.c.accepted_at.type
    assert isinstance(accepted_at_type, DateTime)
    assert accepted_at_type.timezone is True


def test_model_registry_exposes_idempotency_receipts() -> None:
    metadata = registered_model_metadata()

    assert "chat.idempotency_receipts" in metadata.tables
    assert metadata.tables["chat.idempotency_receipts"] is IdempotencyReceiptRecord.__table__


def test_idempotency_receipt_has_tenant_safe_relationships() -> None:
    table = _receipt_table()

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
        "fk_idempotency_receipts_conversation": (
            (
                "tenant_id",
                "conversation_id",
            ),
            (
                "chat.conversations.tenant_id",
                "chat.conversations.id",
            ),
            "RESTRICT",
        ),
        "fk_idempotency_receipts_message": (
            (
                "tenant_id",
                "conversation_id",
                "message_id",
            ),
            (
                "chat.messages.tenant_id",
                "chat.messages.conversation_id",
                "chat.messages.id",
            ),
            "RESTRICT",
        ),
        "fk_idempotency_receipts_agent_run": (
            (
                "tenant_id",
                "conversation_id",
                "agent_run_id",
            ),
            (
                "chat.agent_runs.tenant_id",
                "chat.agent_runs.conversation_id",
                "chat.agent_runs.id",
            ),
            "RESTRICT",
        ),
    }


def test_idempotency_receipt_preserves_request_and_result_invariants() -> None:
    table = _receipt_table()

    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) == {
        "ck_idempotency_receipts_operation_valid",
        "ck_idempotency_receipts_idempotency_key_length_valid",
        "ck_idempotency_receipts_idempotency_key_trimmed",
        "ck_idempotency_receipts_idempotency_key_printable",
        "ck_idempotency_receipts_request_fingerprint_valid",
        "ck_idempotency_receipts_conversation_version_positive",
        "ck_idempotency_receipts_message_sequence_positive",
    }

    assert (
        f"operation = '{ACCEPT_USER_TURN_OPERATION}'"
        in checks["ck_idempotency_receipts_operation_valid"]
    )
    assert (
        "char_length(idempotency_key) BETWEEN 1 AND "
        f"{MAX_IDEMPOTENCY_KEY_LENGTH}"
        in checks["ck_idempotency_receipts_idempotency_key_length_valid"]
    )
    assert (
        "idempotency_key = btrim(idempotency_key)"
        in checks["ck_idempotency_receipts_idempotency_key_trimmed"]
    )
    assert "[[:cntrl:]]" in checks["ck_idempotency_receipts_idempotency_key_printable"]
    assert "^[0-9a-f]{64}$" in checks["ck_idempotency_receipts_request_fingerprint_valid"]
    assert (
        "conversation_version > 0"
        in checks["ck_idempotency_receipts_conversation_version_positive"]
    )
    assert "message_sequence > 0" in checks["ck_idempotency_receipts_message_sequence_positive"]
