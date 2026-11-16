import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.application.accept_user_turn import (
    ACCEPT_USER_TURN_OPERATION,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    AssistantProfileVersionRecord,
    ConversationRecord,
    IdempotencyReceiptRecord,
    MessageRecord,
)
from enterprise_chat_platform.settings import (
    MigrationSettings,
    Settings,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
IDEMPOTENCY_KEY = "Request-AbC-001"
REQUEST_FINGERPRINT = "a" * 64
ASSISTANT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000201")
ASSISTANT_PROFILE_VERSION = 1


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
    message_id: UUID,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": message_id,
        "conversation_id": conversation_id,
        "author_id": uuid4(),
        "role": "user",
        "content": "Explain durable idempotency.",
        "sequence": 1,
        "idempotency_key": IDEMPOTENCY_KEY,
        "created_at": BASE_TIME,
    }


def _agent_run_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    agent_run_id: UUID,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": agent_run_id,
        "conversation_id": conversation_id,
        "input_message_id": message_id,
        "assistant_profile_id": ASSISTANT_PROFILE_ID,
        "assistant_profile_version": ASSISTANT_PROFILE_VERSION,
        "status": "queued",
        "created_at": BASE_TIME,
        "started_at": None,
        "finished_at": None,
        "output_message_id": None,
        "failure_code": None,
        "failure_safe_message": None,
    }


def _receipt_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    agent_run_id: UUID,
    idempotency_key: str = IDEMPOTENCY_KEY,
    request_fingerprint: str = REQUEST_FINGERPRINT,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "operation": ACCEPT_USER_TURN_OPERATION,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
        "conversation_version": 1,
        "message_id": message_id,
        "message_sequence": 1,
        "agent_run_id": agent_run_id,
        "accepted_at": BASE_TIME,
    }


async def _insert_valid_parents(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    agent_run_id: UUID,
) -> None:
    """Insert the valid rows referenced by one receipt."""
    await connection.execute(
        insert(AssistantProfileVersionRecord).values(
            tenant_id=tenant_id,
            profile_id=ASSISTANT_PROFILE_ID,
            version=ASSISTANT_PROFILE_VERSION,
            prompt_template_version="enterprise-chat.v1",
            assistant_name="Schema Test Assistant",
            organization_name="Schema Test Organization",
            response_tone="professional",
            expertise="senior_engineer",
            communication_style="direct_helpful",
            context_character_limit=4_096,
            created_at=BASE_TIME,
            created_by=uuid4(),
        )
    )
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
                message_id=message_id,
            )
        )
    )
    await connection.execute(
        insert(AgentRunRecord).values(
            _agent_run_values(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_id=message_id,
                agent_run_id=agent_run_id,
            )
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_receipts_table_exists_and_is_owned_by_migrator() -> None:
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
                        SELECT
                            to_regclass(
                                'chat.idempotency_receipts'
                            ) IS NOT NULL
                        """
                    )
                )

                assert table_exists is True

                owner = await connection.scalar(
                    text(
                        """
                        SELECT tableowner
                        FROM pg_catalog.pg_tables
                        WHERE schemaname = 'chat'
                        AND tablename = 'idempotency_receipts'
                        """
                    )
                )
    finally:
        await engine.dispose()

    assert owner == migration_url.username


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_role_can_insert_and_read_valid_receipt() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )

                    result = await connection.execute(
                        insert(IdempotencyReceiptRecord).values(
                            _receipt_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                agent_run_id=agent_run_id,
                            )
                        )
                    )

                    stored = (
                        await connection.execute(
                            select(
                                IdempotencyReceiptRecord.request_fingerprint,
                                IdempotencyReceiptRecord.message_id,
                                IdempotencyReceiptRecord.agent_run_id,
                            ).where(
                                IdempotencyReceiptRecord.tenant_id == tenant_id,
                                IdempotencyReceiptRecord.conversation_id == conversation_id,
                                IdempotencyReceiptRecord.operation == ACCEPT_USER_TURN_OPERATION,
                                IdempotencyReceiptRecord.idempotency_key == IDEMPOTENCY_KEY,
                            )
                        )
                    ).one()

                    assert result.rowcount == 1
                    assert stored.request_fingerprint == REQUEST_FINGERPRINT
                    assert stored.message_id == message_id
                    assert stored.agent_run_id == agent_run_id
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_duplicate_receipt_scope() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    engine = _create_local_runtime_engine()

    first_receipt = _receipt_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        agent_run_id=agent_run_id,
    )
    conflicting_receipt = _receipt_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        agent_run_id=agent_run_id,
        request_fingerprint="b" * 64,
    )

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await connection.execute(insert(IdempotencyReceiptRecord).values(first_receipt))
                    await connection.execute(
                        insert(IdempotencyReceiptRecord).values(conflicting_receipt)
                    )
    finally:
        await engine.dispose()

    assert "pk_idempotency_receipts" in str(exc_info.value.orig)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idempotency_key_is_case_sensitive_in_postgres() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    lowercase_key = IDEMPOTENCY_KEY.lower()

    assert lowercase_key != IDEMPOTENCY_KEY

    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )

                    uppercase_result = await connection.execute(
                        insert(IdempotencyReceiptRecord).values(
                            _receipt_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                agent_run_id=agent_run_id,
                            )
                        )
                    )
                    lowercase_result = await connection.execute(
                        insert(IdempotencyReceiptRecord).values(
                            _receipt_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                agent_run_id=agent_run_id,
                                idempotency_key=lowercase_key,
                            )
                        )
                    )

                    assert uppercase_result.rowcount == 1
                    assert lowercase_result.rowcount == 1
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_scope_values_are_isolated_by_tenant() -> None:
    tenant_a_id = uuid4()
    tenant_b_id = uuid4()

    # Deliberately reuse every non-tenant identifier.
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()

    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_a_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_b_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )

                    tenant_a_result = await connection.execute(
                        insert(IdempotencyReceiptRecord).values(
                            _receipt_values(
                                tenant_id=tenant_a_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                agent_run_id=agent_run_id,
                            )
                        )
                    )
                    tenant_b_result = await connection.execute(
                        insert(IdempotencyReceiptRecord).values(
                            _receipt_values(
                                tenant_id=tenant_b_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                agent_run_id=agent_run_id,
                            )
                        )
                    )

                    assert tenant_a_result.rowcount == 1
                    assert tenant_b_result.rowcount == 1
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("foreign_reference", "expected_constraint"),
    [
        pytest.param(
            "message",
            "fk_idempotency_receipts_message",
            id="message",
        ),
        pytest.param(
            "agent_run",
            "fk_idempotency_receipts_agent_run",
            id="agent-run",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_cross_tenant_receipt_reference(
    foreign_reference: str,
    expected_constraint: str,
) -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    conversation_id = uuid4()

    message_id = uuid4()
    agent_run_id = uuid4()
    other_message_id = uuid4()
    other_agent_run_id = uuid4()

    receipt_message_id = other_message_id if foreign_reference == "message" else message_id
    receipt_agent_run_id = other_agent_run_id if foreign_reference == "agent_run" else agent_run_id

    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await _insert_valid_parents(
                        connection,
                        tenant_id=other_tenant_id,
                        conversation_id=conversation_id,
                        message_id=other_message_id,
                        agent_run_id=other_agent_run_id,
                    )

                    await connection.execute(
                        insert(IdempotencyReceiptRecord).values(
                            _receipt_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                message_id=receipt_message_id,
                                agent_run_id=receipt_agent_run_id,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert expected_constraint in str(exc_info.value.orig)


@pytest.mark.parametrize(
    ("overrides", "expected_constraint"),
    [
        pytest.param(
            {
                "operation": "delete_conversation.v1",
            },
            "ck_idempotency_receipts_operation_valid",
            id="unsupported-operation",
        ),
        pytest.param(
            {
                "idempotency_key": "",
            },
            "ck_idempotency_receipts_idempotency_key_length_valid",
            id="empty-key",
        ),
        pytest.param(
            {
                "idempotency_key": " Request-AbC-001 ",
            },
            "ck_idempotency_receipts_idempotency_key_trimmed",
            id="untrimmed-key",
        ),
        pytest.param(
            {
                "idempotency_key": "Request\nAbC-001",
            },
            "ck_idempotency_receipts_idempotency_key_printable",
            id="control-character-in-key",
        ),
        pytest.param(
            {
                "request_fingerprint": "A" * 64,
            },
            "ck_idempotency_receipts_request_fingerprint_valid",
            id="invalid-fingerprint",
        ),
        pytest.param(
            {
                "conversation_version": 0,
            },
            "ck_idempotency_receipts_conversation_version_positive",
            id="non-positive-conversation-version",
        ),
        pytest.param(
            {
                "message_sequence": 0,
            },
            "ck_idempotency_receipts_message_sequence_positive",
            id="non-positive-message-sequence",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_invalid_receipt(
    overrides: dict[str, object],
    expected_constraint: str,
) -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()

    receipt_values = _receipt_values(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        agent_run_id=agent_run_id,
    )
    receipt_values.update(overrides)

    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_valid_parents(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await connection.execute(
                        insert(IdempotencyReceiptRecord).values(receipt_values)
                    )
    finally:
        await engine.dispose()

    assert expected_constraint in str(exc_info.value.orig)
