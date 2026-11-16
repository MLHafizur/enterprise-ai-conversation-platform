import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    AssistantProfileVersionRecord,
    ConversationRecord,
    MessageRecord,
)
from enterprise_chat_platform.settings import (
    MigrationSettings,
    Settings,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
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
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "id": message_id,
        "conversation_id": conversation_id,
        "author_id": uuid4(),
        "role": "user",
        "content": "Explain transactional outboxes.",
        "sequence": 1,
        "idempotency_key": f"accept-turn-{uuid4()}",
        "created_at": BASE_TIME,
    }

    if overrides is not None:
        values.update(overrides)

    return values


def _queued_agent_run_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    input_message_id: UUID,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "id": uuid4(),
        "conversation_id": conversation_id,
        "input_message_id": input_message_id,
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

    if overrides is not None:
        values.update(overrides)

    return values


async def _insert_assistant_profile(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
) -> None:
    """Insert the immutable profile revision referenced by a test run."""
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_runs_table_exists_and_is_owned_by_migrator() -> None:
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
                        SELECT to_regclass('chat.agent_runs') IS NOT NULL
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
                        AND tablename = 'agent_runs'
                        """
                    )
                )
    finally:
        await engine.dispose()

    assert owner == migration_url.username


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_role_can_insert_valid_queued_agent_run() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    input_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
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
                                message_id=input_message_id,
                            )
                        )
                    )
                    result = await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                input_message_id=input_message_id,
                            )
                        )
                    )

                    assert result.rowcount == 1
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_input_message_from_another_conversation() -> None:
    tenant_id = uuid4()
    message_conversation_id = uuid4()
    run_conversation_id = uuid4()
    input_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=message_conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=run_conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=tenant_id,
                                conversation_id=message_conversation_id,
                                message_id=input_message_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=run_conversation_id,
                                input_message_id=input_message_id,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "fk_agent_runs_input_message" in str(exc_info.value.orig)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "started_at": BASE_TIME,
            },
            id="queued-with-start-time",
        ),
        pytest.param(
            {
                "status": "running",
            },
            id="running-without-start-time",
        ),
        pytest.param(
            {
                "status": "succeeded",
                "started_at": BASE_TIME,
                "finished_at": BASE_TIME,
            },
            id="succeeded-without-output",
        ),
        pytest.param(
            {
                "status": "failed",
                "finished_at": BASE_TIME,
            },
            id="failed-without-failure-details",
        ),
        pytest.param(
            {
                "status": "cancelled",
                "finished_at": BASE_TIME,
                "failure_code": "unexpected_failure",
                "failure_safe_message": "Unexpected failure",
            },
            id="cancelled-with-failure-details",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_invalid_agent_run_lifecycle(
    overrides: Mapping[str, object],
) -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    input_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
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
                                message_id=input_message_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                input_message_id=input_message_id,
                                overrides=overrides,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "ck_agent_runs_lifecycle_state_valid" in str(exc_info.value.orig)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "status": "running",
                "started_at": BASE_TIME,
            },
            id="running",
        ),
        pytest.param(
            {
                "status": "failed",
                "finished_at": BASE_TIME,
                "failure_code": "provider_timeout",
                "failure_safe_message": "The AI provider timed out.",
            },
            id="failed-before-start",
        ),
        pytest.param(
            {
                "status": "failed",
                "started_at": BASE_TIME,
                "finished_at": BASE_TIME,
                "failure_code": "provider_timeout",
                "failure_safe_message": "The AI provider timed out.",
            },
            id="failed-after-start",
        ),
        pytest.param(
            {
                "status": "cancelled",
                "finished_at": BASE_TIME,
            },
            id="cancelled-before-start",
        ),
        pytest.param(
            {
                "status": "cancelled",
                "started_at": BASE_TIME,
                "finished_at": BASE_TIME,
            },
            id="cancelled-after-start",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_accepts_valid_non_success_lifecycle(
    overrides: Mapping[str, object],
) -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    input_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
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
                                message_id=input_message_id,
                            )
                        )
                    )
                    result = await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                input_message_id=input_message_id,
                                overrides=overrides,
                            )
                        )
                    )

                    assert result.rowcount == 1
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_accepts_valid_succeeded_agent_run() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    input_message_id = uuid4()
    output_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
                    conversation_values = _conversation_values(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                    )
                    conversation_values["last_message_sequence"] = 2

                    await connection.execute(insert(ConversationRecord).values(conversation_values))
                    await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                message_id=input_message_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                message_id=output_message_id,
                                overrides={
                                    "role": "assistant",
                                    "content": (
                                        "A transactional outbox stores the "
                                        "business change and event together."
                                    ),
                                    "sequence": 2,
                                    "idempotency_key": (f"agent-output-{uuid4()}"),
                                },
                            )
                        )
                    )

                    result = await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                input_message_id=input_message_id,
                                overrides={
                                    "status": "succeeded",
                                    "started_at": BASE_TIME,
                                    "finished_at": BASE_TIME,
                                    "output_message_id": output_message_id,
                                },
                            )
                        )
                    )

                    assert result.rowcount == 1
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_output_message_from_another_conversation() -> None:
    tenant_id = uuid4()
    run_conversation_id = uuid4()
    other_conversation_id = uuid4()
    input_message_id = uuid4()
    output_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=run_conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=other_conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=tenant_id,
                                conversation_id=run_conversation_id,
                                message_id=input_message_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=tenant_id,
                                conversation_id=other_conversation_id,
                                message_id=output_message_id,
                                overrides={
                                    "role": "assistant",
                                    "content": "Output from another conversation.",
                                    "idempotency_key": (f"agent-output-{uuid4()}"),
                                },
                            )
                        )
                    )

                    await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=run_conversation_id,
                                input_message_id=input_message_id,
                                overrides={
                                    "status": "succeeded",
                                    "started_at": BASE_TIME,
                                    "finished_at": BASE_TIME,
                                    "output_message_id": output_message_id,
                                },
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "fk_agent_runs_output_message" in str(exc_info.value.orig)


@pytest.mark.parametrize(
    ("overrides", "expected_constraint"),
    [
        pytest.param(
            {
                "status": "running",
                "started_at": BASE_TIME - timedelta(seconds=1),
            },
            "ck_agent_runs_timestamps_ordered",
            id="started-before-created",
        ),
        pytest.param(
            {
                "status": "cancelled",
                "started_at": BASE_TIME + timedelta(seconds=2),
                "finished_at": BASE_TIME + timedelta(seconds=1),
            },
            "ck_agent_runs_timestamps_ordered",
            id="finished-before-started",
        ),
        pytest.param(
            {
                "status": "failed",
                "finished_at": BASE_TIME,
                "failure_code": "Provider Timeout",
                "failure_safe_message": "The AI provider timed out.",
            },
            "ck_agent_runs_failure_code_valid",
            id="invalid-failure-code",
        ),
        pytest.param(
            {
                "status": "failed",
                "finished_at": BASE_TIME,
                "failure_code": "provider_timeout",
                "failure_safe_message": "   ",
            },
            "ck_agent_runs_failure_safe_message_not_blank",
            id="blank-failure-message",
        ),
        pytest.param(
            {
                "status": "failed",
                "finished_at": BASE_TIME,
                "failure_code": "provider_timeout",
                "failure_safe_message": ("The AI provider timed out.\nPlease retry."),
            },
            "ck_agent_runs_failure_safe_message_printable",
            id="control-character-in-failure-message",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_malformed_agent_run_data(
    overrides: Mapping[str, object],
    expected_constraint: str,
) -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    input_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
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
                                message_id=input_message_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                input_message_id=input_message_id,
                                overrides=overrides,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert expected_constraint in str(exc_info.value.orig)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_input_message_from_another_tenant() -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    conversation_id = uuid4()
    input_message_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_assistant_profile(connection, tenant_id=tenant_id)
                    # Both tenants deliberately use the same conversation UUID.
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )
                    await connection.execute(
                        insert(ConversationRecord).values(
                            _conversation_values(
                                tenant_id=other_tenant_id,
                                conversation_id=conversation_id,
                            )
                        )
                    )

                    # The message belongs only to the other tenant.
                    await connection.execute(
                        insert(MessageRecord).values(
                            _message_values(
                                tenant_id=other_tenant_id,
                                conversation_id=conversation_id,
                                message_id=input_message_id,
                            )
                        )
                    )

                    # This run attempts to reference that other tenant's message.
                    await connection.execute(
                        insert(AgentRunRecord).values(
                            _queued_agent_run_values(
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                input_message_id=input_message_id,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "fk_agent_runs_input_message" in str(exc_info.value.orig)
