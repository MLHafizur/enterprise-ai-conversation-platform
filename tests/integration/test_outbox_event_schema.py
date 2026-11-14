import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
    OutboxEventRecord,
)
from enterprise_chat_platform.settings import (
    MigrationSettings,
    Settings,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
EXPECTED_RUNTIME_PRIVILEGES = (
    "DELETE",
    "INSERT",
    "SELECT",
    "UPDATE",
)


def _validated_database_urls() -> tuple[URL, URL]:
    """Return runtime and migration URLs only when both target local Compose."""
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())

    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
    assert (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    ) == (
        runtime_url.host,
        runtime_url.port,
        runtime_url.database,
    )
    assert runtime_url.username is not None
    assert migration_url.username is not None

    return runtime_url, migration_url


def _create_local_runtime_engine() -> AsyncEngine:
    """Create a runtime engine only after proving the target is local."""
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    return create_database_engine(settings)


def _conversation_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
) -> dict[str, object]:
    """Build the parent aggregate referenced by an outbox event."""
    return {
        "tenant_id": tenant_id,
        "id": conversation_id,
        "created_by": uuid4(),
        "status": "active",
        "version": 1,
        "last_message_sequence": 1,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }


def _outbox_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    event_id: UUID | None = None,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build an otherwise valid, initially pending outbox row."""
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "id": event_id or uuid4(),
        "aggregate_type": "conversation",
        "aggregate_id": conversation_id,
        "aggregate_version": 1,
        "event_type": "agent_run.queued.v1",
        "schema_version": 1,
        "payload": {
            "agent_run_id": str(uuid4()),
            "input_message_id": str(uuid4()),
        },
        "occurred_at": BASE_TIME,
        "available_at": BASE_TIME,
        "attempt_count": 0,
        "last_attempt_at": None,
        "lease_token": None,
        "lease_expires_at": None,
        "published_at": None,
    }

    if overrides is not None:
        values.update(overrides)

    return values


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outbox_table_has_protected_ownership_and_runtime_crud() -> None:
    runtime_url, migration_url = _validated_database_urls()
    engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                table_exists = await connection.scalar(
                    text("SELECT to_regclass('chat.outbox_events') IS NOT NULL")
                )

                # Stop before privilege queries when the migration is absent.
                assert table_exists is True

                owner = await connection.scalar(
                    text(
                        """
                        SELECT tableowner
                        FROM pg_catalog.pg_tables
                        WHERE schemaname = 'chat'
                        AND tablename = 'outbox_events'
                        """
                    )
                )
                public_privileges = tuple(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT privilege_type
                                FROM information_schema.table_privileges
                                WHERE table_schema = 'chat'
                                AND table_name = 'outbox_events'
                                AND grantee = 'PUBLIC'
                                ORDER BY privilege_type
                                """
                            )
                        )
                    ).scalars()
                )
                runtime_privileges = tuple(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT privilege_type
                                FROM information_schema.table_privileges
                                WHERE table_schema = 'chat'
                                AND table_name = 'outbox_events'
                                AND grantee = :runtime_role
                                ORDER BY privilege_type
                                """
                            ),
                            {"runtime_role": runtime_url.username},
                        )
                    ).scalars()
                )
    finally:
        await engine.dispose()

    assert owner == migration_url.username
    assert public_privileges == ()
    assert runtime_privileges == EXPECTED_RUNTIME_PRIVILEGES


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_role_can_insert_and_read_pending_outbox_event() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    event_id = uuid4()
    values = _outbox_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        event_id=event_id,
    )
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
                    result = await connection.execute(insert(OutboxEventRecord).values(values))
                    stored = (
                        await connection.execute(
                            select(
                                OutboxEventRecord.aggregate_type,
                                OutboxEventRecord.aggregate_id,
                                OutboxEventRecord.aggregate_version,
                                OutboxEventRecord.event_type,
                                OutboxEventRecord.schema_version,
                                OutboxEventRecord.payload,
                                OutboxEventRecord.available_at,
                                OutboxEventRecord.attempt_count,
                                OutboxEventRecord.last_attempt_at,
                                OutboxEventRecord.lease_token,
                                OutboxEventRecord.lease_expires_at,
                                OutboxEventRecord.published_at,
                            ).where(
                                OutboxEventRecord.tenant_id == tenant_id,
                                OutboxEventRecord.id == event_id,
                            )
                        )
                    ).one()

                    assert result.rowcount == 1
                    assert stored.aggregate_type == "conversation"
                    assert stored.aggregate_id == conversation_id
                    assert stored.aggregate_version == 1
                    assert stored.event_type == "agent_run.queued.v1"
                    assert stored.schema_version == 1
                    assert stored.payload == values["payload"]
                    assert stored.available_at == BASE_TIME
                    assert stored.attempt_count == 0
                    assert stored.last_attempt_at is None
                    assert stored.lease_token is None
                    assert stored.lease_expires_at is None
                    assert stored.published_at is None
                finally:
                    # Leave no conversation or outbox event behind.
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_cross_tenant_outbox_aggregate_reference() -> None:
    conversation_tenant_id = uuid4()
    event_tenant_id = uuid4()
    conversation_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=conversation_tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(OutboxEventRecord).values(
                            _outbox_values(
                                tenant_id=event_tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "fk_outbox_events_conversation" in str(exc_info.value.orig)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_duplicate_logical_outbox_event() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    first_event = _outbox_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )
    duplicate_event = _outbox_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )

    assert first_event["id"] != duplicate_event["id"]
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
                    await connection.execute(insert(OutboxEventRecord).values(first_event))
                    await connection.execute(insert(OutboxEventRecord).values(duplicate_event))
    finally:
        await engine.dispose()

    assert "uq_outbox_events_logical_event" in str(exc_info.value.orig)


@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    [
        pytest.param(
            {"aggregate_type": "user"},
            "ck_outbox_events_aggregate_type_valid",
            id="unsupported-aggregate-type",
        ),
        pytest.param(
            {"event_type": "AgentRunQueued"},
            "ck_outbox_events_event_type_valid",
            id="invalid-event-type",
        ),
        pytest.param(
            {"aggregate_version": -1},
            "ck_outbox_events_aggregate_version_nonnegative",
            id="negative-aggregate-version",
        ),
        pytest.param(
            {"schema_version": 0},
            "ck_outbox_events_schema_version_positive",
            id="non-positive-schema-version",
        ),
        pytest.param(
            {"payload": ["not", "an", "object"]},
            "ck_outbox_events_payload_object",
            id="non-object-payload",
        ),
        pytest.param(
            {"available_at": BASE_TIME - timedelta(microseconds=1)},
            "ck_outbox_events_availability_ordered",
            id="available-before-occurred",
        ),
        pytest.param(
            {"last_attempt_at": BASE_TIME},
            "ck_outbox_events_attempt_state_valid",
            id="zero-attempts-with-attempt-time",
        ),
        pytest.param(
            {
                "attempt_count": 1,
                "last_attempt_at": BASE_TIME,
                "lease_token": uuid4(),
            },
            "ck_outbox_events_lease_state_valid",
            id="incomplete-lease",
        ),
        pytest.param(
            {"published_at": BASE_TIME},
            "ck_outbox_events_publication_state_valid",
            id="published-without-attempt",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_invalid_outbox_state(
    overrides: Mapping[str, object],
    constraint_name: str,
) -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(OutboxEventRecord).values(
                            _outbox_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                overrides=overrides,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert constraint_name in str(exc_info.value.orig)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pending_dispatch_index_has_expected_predicate_and_order() -> None:
    _, migration_url = _validated_database_urls()
    engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                index = (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                pg_get_indexdef(indexrelid) AS definition,
                                pg_get_expr(indpred, indrelid) AS predicate,
                                indisvalid,
                                indisready
                            FROM pg_catalog.pg_index
                            WHERE indexrelid = (
                                'chat.ix_outbox_events_dispatch_pending'
                            )::regclass
                            """
                        )
                    )
                ).one()
    finally:
        await engine.dispose()

    assert "(available_at, occurred_at, tenant_id, id)" in index.definition
    assert "published_at IS NULL" in index.predicate
    assert index.indisvalid is True
    assert index.indisready is True
