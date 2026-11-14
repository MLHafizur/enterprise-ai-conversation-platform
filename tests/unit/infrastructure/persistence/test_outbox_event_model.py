from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    OutboxEventRecord,
    registered_model_metadata,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    MAX_OUTBOX_AGGREGATE_TYPE_LENGTH,
    MAX_OUTBOX_EVENT_TYPE_LENGTH,
    OUTBOX_AGGREGATE_TYPE,
)


def _outbox_table() -> Table:
    """Give mypy the concrete type created by declarative mapping."""
    return cast(Table, OutboxEventRecord.__table__)


def test_outbox_event_record_has_expected_table_shape() -> None:
    table = _outbox_table()

    assert table.schema == APP_SCHEMA == "chat"
    assert table.name == "outbox_events"
    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "event_type",
        "schema_version",
        "payload",
        "occurred_at",
        "available_at",
        "attempt_count",
        "last_attempt_at",
        "lease_token",
        "lease_expires_at",
        "published_at",
    )
    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )
    assert {column.name for column in table.columns if column.nullable} == {
        "last_attempt_at",
        "lease_token",
        "lease_expires_at",
        "published_at",
    }

    for column_name in (
        "tenant_id",
        "id",
        "aggregate_id",
        "lease_token",
    ):
        assert isinstance(table.c[column_name].type, Uuid)

    aggregate_type = table.c.aggregate_type.type
    assert isinstance(aggregate_type, String)
    assert aggregate_type.length == MAX_OUTBOX_AGGREGATE_TYPE_LENGTH
    assert aggregate_type.collation == "C"

    event_type = table.c.event_type.type
    assert isinstance(event_type, String)
    assert event_type.length == MAX_OUTBOX_EVENT_TYPE_LENGTH
    assert event_type.collation == "C"

    assert isinstance(table.c.aggregate_version.type, BigInteger)
    assert isinstance(table.c.schema_version.type, SmallInteger)
    assert isinstance(table.c.payload.type, JSONB)
    assert isinstance(table.c.attempt_count.type, Integer)

    for column_name in (
        "occurred_at",
        "available_at",
        "last_attempt_at",
        "lease_expires_at",
        "published_at",
    ):
        timestamp_type = table.c[column_name].type
        assert isinstance(timestamp_type, DateTime)
        assert timestamp_type.timezone is True


def test_outbox_event_record_has_tenant_safe_aggregate_relationship() -> None:
    table = _outbox_table()

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
        "fk_outbox_events_conversation": (
            (
                "tenant_id",
                "aggregate_id",
            ),
            (
                "chat.conversations.tenant_id",
                "chat.conversations.id",
            ),
            "RESTRICT",
        )
    }


def test_outbox_event_record_deduplicates_one_logical_event() -> None:
    table = _outbox_table()

    unique_constraints = {
        str(constraint.name): tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints == {
        "uq_outbox_events_logical_event": (
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
        )
    }


def test_outbox_event_record_preserves_delivery_invariants() -> None:
    table = _outbox_table()

    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) == {
        "ck_outbox_events_aggregate_type_valid",
        "ck_outbox_events_event_type_valid",
        "ck_outbox_events_aggregate_version_nonnegative",
        "ck_outbox_events_schema_version_positive",
        "ck_outbox_events_payload_object",
        "ck_outbox_events_availability_ordered",
        "ck_outbox_events_attempt_count_nonnegative",
        "ck_outbox_events_attempt_state_valid",
        "ck_outbox_events_lease_state_valid",
        "ck_outbox_events_publication_state_valid",
    }
    assert (
        f"aggregate_type = '{OUTBOX_AGGREGATE_TYPE}'"
        in checks["ck_outbox_events_aggregate_type_valid"]
    )
    assert "^[a-z][a-z0-9_.-]*$" in checks["ck_outbox_events_event_type_valid"]
    assert "jsonb_typeof(payload) = 'object'" in checks["ck_outbox_events_payload_object"]
    assert (
        "attempt_count = 0 AND last_attempt_at IS NULL"
        in checks["ck_outbox_events_attempt_state_valid"]
    )
    assert "lease_expires_at > last_attempt_at" in checks["ck_outbox_events_lease_state_valid"]
    assert (
        "lease_token IS NULL AND lease_expires_at IS NULL"
        in checks["ck_outbox_events_publication_state_valid"]
    )


def test_outbox_event_record_has_pending_dispatch_index() -> None:
    table = _outbox_table()
    indexes: dict[str, Index] = {str(index.name): index for index in table.indexes}

    assert set(indexes) == {"ix_outbox_events_dispatch_pending"}
    dispatch_index = indexes["ix_outbox_events_dispatch_pending"]
    assert tuple(column.name for column in dispatch_index.columns) == (
        "available_at",
        "occurred_at",
        "tenant_id",
        "id",
    )
    assert dispatch_index.unique is False
    assert str(dispatch_index.dialect_options["postgresql"]["where"]) == "published_at IS NULL"


def test_outbox_event_record_has_no_hidden_database_defaults() -> None:
    table = _outbox_table()

    assert all(column.default is None for column in table.columns)
    assert all(column.server_default is None for column in table.columns)


def test_model_registry_exposes_outbox_events_to_migrations() -> None:
    metadata = registered_model_metadata()

    assert "chat.outbox_events" in metadata.tables
    assert metadata.tables["chat.outbox_events"] is OutboxEventRecord.__table__
