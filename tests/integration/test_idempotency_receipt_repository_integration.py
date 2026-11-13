from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.sql.elements import TextClause

from enterprise_chat_platform.application.accept_user_turn import (
    AcceptUserTurnResult,
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.domain.chat import (
    AcceptedUserTurn,
    Conversation,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.agent_run import (
    agent_run_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.message import (
    message_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    IdempotencyReceiptRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.idempotency_receipt import (
    SqlAlchemyIdempotencyReceiptRepository,
    idempotency_scope_advisory_key,
)
from enterprise_chat_platform.settings import Settings

LOOPBACK_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
    }
)
ACCEPTED_AT = datetime(
    2026,
    11,
    13,
    16,
    0,
    tzinfo=UTC,
)
REQUEST_FINGERPRINT = "a" * 64

_TRY_SCOPE_LOCK: TextClause = text("SELECT pg_try_advisory_xact_lock(:lock_key)").bindparams(
    bindparam(
        "lock_key",
        type_=BigInteger(),
    )
)


def _create_local_database_resources() -> tuple[
    AsyncEngine,
    AsyncSessionFactory,
]:
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    engine = create_database_engine(settings)

    return engine, create_session_factory(engine)


@asynccontextmanager
async def _local_runtime_session() -> AsyncIterator[AsyncSession]:
    """Provide a local transaction that is always rolled back."""
    engine, session_factory = _create_local_database_resources()

    try:
        async with session_factory() as session:
            transaction = await session.begin()

            try:
                yield session
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


def _build_receipt() -> tuple[
    AcceptedUserTurn,
    IdempotencyReceipt,
]:
    tenant_id = TenantId.new()
    conversation_id = ConversationId.new()
    actor_id = UserId.new()
    key = IdempotencyKey("Request-AbC-001")

    conversation = Conversation.create(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=actor_id,
        at=ACCEPTED_AT,
    )
    accepted_turn = conversation.accept_user_turn(
        trusted_tenant_id=tenant_id,
        actor_id=actor_id,
        message_id=MessageId.new(),
        agent_run_id=AgentRunId.new(),
        content="Explain transactional idempotency.",
        idempotency_key=key,
        at=ACCEPTED_AT,
    )
    result = AcceptUserTurnResult(
        conversation_id=conversation_id,
        conversation_version=(accepted_turn.updated_conversation.version),
        message_id=accepted_turn.message.id,
        message_sequence=accepted_turn.message.sequence,
        agent_run_id=accepted_turn.agent_run.id,
        accepted_at=accepted_turn.message.created_at,
        replayed=False,
    )
    receipt = IdempotencyReceipt(
        scope=IdempotencyScope(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            key=key,
        ),
        request_fingerprint=REQUEST_FINGERPRINT,
        result=result,
    )

    return accepted_turn, receipt


async def _stage_parent_graph(
    session: AsyncSession,
    accepted_turn: AcceptedUserTurn,
) -> None:
    session.add(conversation_to_new_record(accepted_turn.updated_conversation))
    await session.flush()

    session.add(message_to_new_record(accepted_turn.message))
    await session.flush()

    session.add(agent_run_to_new_record(accepted_turn.agent_run))
    await session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_add_waits_for_caller_flush_and_round_trips() -> None:
    accepted_turn, expected = _build_receipt()

    async with (
        asyncio.timeout(5),
        _local_runtime_session() as session,
    ):
        await _stage_parent_graph(session, accepted_turn)
        repository = SqlAlchemyIdempotencyReceiptRepository(session)

        repository.add(receipt=expected)

        pending_receipts = [
            value
            for value in session.new
            if isinstance(
                value,
                IdempotencyReceiptRecord,
            )
        ]

        assert len(pending_receipts) == 1

        # The project session has autoflush=False. Therefore add()
        # must not make the receipt visible before the caller flushes.
        before_flush = await repository.get(scope=expected.scope)

        assert before_flush is None

        await session.flush()
        session.expunge_all()

        stored = await repository.get(scope=expected.scope)

        assert stored == expected
        assert stored is not None
        assert stored.result.replayed is False
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_returns_none_for_missing_scope() -> None:
    _, receipt = _build_receipt()

    async with (
        asyncio.timeout(5),
        _local_runtime_session() as session,
    ):
        repository = SqlAlchemyIdempotencyReceiptRepository(session)

        stored = await repository.get(scope=receipt.scope)

        assert stored is None
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_uses_complete_case_sensitive_scope() -> None:
    accepted_turn, expected = _build_receipt()

    async with (
        asyncio.timeout(5),
        _local_runtime_session() as session,
    ):
        await _stage_parent_graph(session, accepted_turn)
        repository = SqlAlchemyIdempotencyReceiptRepository(session)
        repository.add(receipt=expected)
        await session.flush()
        session.expunge_all()

        exact = await repository.get(scope=expected.scope)

        wrong_scopes = (
            IdempotencyScope(
                tenant_id=TenantId.new(),
                conversation_id=expected.scope.conversation_id,
                key=expected.scope.key,
            ),
            IdempotencyScope(
                tenant_id=expected.scope.tenant_id,
                conversation_id=ConversationId.new(),
                key=expected.scope.key,
            ),
            IdempotencyScope(
                tenant_id=expected.scope.tenant_id,
                conversation_id=expected.scope.conversation_id,
                key=IdempotencyKey("request-abc-001"),
            ),
        )

        missing = [await repository.get(scope=scope) for scope in wrong_scopes]

        assert exact == expected
        assert missing == [None, None, None]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_scope_lock_is_released_by_rollback() -> None:
    _, receipt = _build_receipt()
    scope = receipt.scope
    lock_key = idempotency_scope_advisory_key(scope)
    engine, session_factory = _create_local_database_resources()

    try:
        async with asyncio.timeout(5):
            async with (
                session_factory() as holder,
                session_factory() as contender,
            ):
                holder_transaction = await holder.begin()
                contender_transaction = await contender.begin()

                try:
                    holder_repository = SqlAlchemyIdempotencyReceiptRepository(holder)
                    await holder_repository.acquire_scope_lock(scope=scope)

                    first_attempt = await contender.execute(
                        _TRY_SCOPE_LOCK,
                        {"lock_key": lock_key},
                    )

                    assert first_attempt.scalar_one() is False

                    await holder_transaction.rollback()

                    second_attempt = await contender.execute(
                        _TRY_SCOPE_LOCK,
                        {"lock_key": lock_key},
                    )

                    assert second_attempt.scalar_one() is True
                finally:
                    if contender_transaction.is_active:
                        await contender_transaction.rollback()

                    if holder_transaction.is_active:
                        await holder_transaction.rollback()

            # The contender rollback must also release its acquired lock.
            async with session_factory() as verification:
                async with verification.begin():
                    result = await verification.execute(
                        _TRY_SCOPE_LOCK,
                        {"lock_key": lock_key},
                    )

                    assert result.scalar_one() is True
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_different_scope_locks_do_not_block() -> None:
    _, receipt = _build_receipt()
    first_scope = receipt.scope
    second_scope = IdempotencyScope(
        tenant_id=first_scope.tenant_id,
        conversation_id=first_scope.conversation_id,
        key=IdempotencyKey("request-abc-001"),
    )
    engine, session_factory = _create_local_database_resources()

    try:
        async with asyncio.timeout(5):
            async with (
                session_factory() as first_session,
                session_factory() as second_session,
            ):
                first_transaction = await first_session.begin()
                second_transaction = await second_session.begin()

                try:
                    first_repository = SqlAlchemyIdempotencyReceiptRepository(first_session)
                    second_repository = SqlAlchemyIdempotencyReceiptRepository(second_session)

                    await first_repository.acquire_scope_lock(scope=first_scope)

                    async with asyncio.timeout(1):
                        await second_repository.acquire_scope_lock(scope=second_scope)
                finally:
                    if second_transaction.is_active:
                        await second_transaction.rollback()

                    if first_transaction.is_active:
                        await first_transaction.rollback()
    finally:
        await engine.dispose()
