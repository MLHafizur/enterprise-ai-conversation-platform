import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
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
    MessageRecord,
)
from enterprise_chat_platform.settings import (
    MigrationSettings,
    Settings,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


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
    """Build a valid parent conversation for a message test."""
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


def _message_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build an otherwise valid immutable user-message row."""
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "id": uuid4(),
        "conversation_id": conversation_id,
        "author_id": uuid4(),
        "role": "user",
        "content": "Explain optimistic concurrency.",
        "sequence": 1,
        "idempotency_key": f"accept-turn-{uuid4()}",
        "created_at": BASE_TIME,
    }

    if overrides is not None:
        values.update(overrides)

    return values


@pytest.mark.integration
@pytest.mark.asyncio
async def test_messages_table_exists_and_is_owned_by_migrator() -> None:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()

    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())

    # Never inspect or migrate a remote database from this test.
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

    engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                table_exists = await connection.scalar(
                    text(
                        """
                        SELECT to_regclass('chat.messages') IS NOT NULL
                        """
                    )
                )

                # Stop here when the migration has not been applied. This also
                # avoids querying ownership for a table that does not exist.
                assert table_exists is True

                owner = await connection.scalar(
                    text(
                        """
                        SELECT tableowner
                        FROM pg_catalog.pg_tables
                        WHERE schemaname = 'chat'
                        AND tablename = 'messages'
                        """
                    )
                )
    finally:
        await engine.dispose()

    assert owner == migration_url.username


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_role_can_insert_valid_message() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
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
                    result = await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )

                    assert result.rowcount == 1
                finally:
                    # Leave no test conversation or message behind.
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_cross_tenant_message_reference() -> None:
    conversation_tenant_id = uuid4()
    message_tenant_id = uuid4()
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
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=message_tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "fk_messages_tenant_id_conversation_id_conversations" in str(exc_info.value.orig)


@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    [
        pytest.param(
            {"role": "system"},
            "ck_messages_role_valid",
            id="invalid-role",
        ),
        pytest.param(
            {"sequence": 0},
            "ck_messages_sequence_positive",
            id="non-positive-sequence",
        ),
        pytest.param(
            {"content": "   "},
            "ck_messages_content_not_blank",
            id="blank-content",
        ),
        pytest.param(
            {"content": "x" * 16_001},
            "ck_messages_content_length_valid",
            id="oversized-content",
        ),
        pytest.param(
            {"idempotency_key": ""},
            "ck_messages_idempotency_key_length_valid",
            id="empty-idempotency-key",
        ),
        pytest.param(
            {"idempotency_key": " accept-turn-key "},
            "ck_messages_idempotency_key_trimmed",
            id="untrimmed-idempotency-key",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_invalid_message(
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
                        insert(MessageRecord).values(
                            _message_values(
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
async def test_database_rejects_duplicate_conversation_sequence() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    engine = _create_local_runtime_engine()

    first_message = _message_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )
    second_message = _message_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )

    assert first_message["id"] != second_message["id"]
    assert first_message["idempotency_key"] != second_message["idempotency_key"]
    assert first_message["sequence"] == second_message["sequence"] == 1

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
                    await connection.execute(insert(MessageRecord).values(first_message))
                    await connection.execute(insert(MessageRecord).values(second_message))
    finally:
        await engine.dispose()

    assert "uq_messages_tenant_id_conversation_id_sequence" in str(exc_info.value.orig)
