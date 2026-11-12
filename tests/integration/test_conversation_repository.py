from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import (
    Conversation,
    ConversationStatus,
)
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)
from enterprise_chat_platform.settings import Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CREATED_AT = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 11, 12, 14, 5, tzinfo=UTC)


@asynccontextmanager
async def _local_runtime_session() -> AsyncIterator[AsyncSession]:
    """Provide a local transaction that is always rolled back."""
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

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


def _conversation(
    *,
    tenant_id: TenantId,
    conversation_id: ConversationId,
    created_by: UserId,
    status: ConversationStatus = ConversationStatus.ACTIVE,
    version: int = 0,
    last_message_sequence: int = 0,
) -> Conversation:
    return Conversation(
        tenant_id=tenant_id,
        id=conversation_id,
        created_by=created_by,
        status=status,
        version=version,
        last_message_sequence=last_message_sequence,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_returns_domain_conversation() -> None:
    expected = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        created_by=UserId.new(),
        status=ConversationStatus.ARCHIVED,
        version=7,
        last_message_sequence=6,
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        session.add(conversation_to_new_record(expected))
        await session.flush()

        # Remove the inserted ORM object from memory so the repository must
        # reconstruct it from database query results.
        session.expunge_all()

        repository = SqlAlchemyConversationRepository(session)

        actual = await repository.get(
            tenant_id=expected.tenant_id,
            conversation_id=expected.id,
        )

        assert actual == expected
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_returns_none_for_missing_conversation() -> None:
    async with asyncio.timeout(5), _local_runtime_session() as session:
        repository = SqlAlchemyConversationRepository(session)

        result = await repository.get(
            tenant_id=TenantId.new(),
            conversation_id=ConversationId.new(),
        )

        assert result is None
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_isolated_same_conversation_id_by_tenant() -> None:
    shared_conversation_id = ConversationId.new()

    tenant_a_conversation = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=shared_conversation_id,
        created_by=UserId.new(),
        version=2,
        last_message_sequence=2,
    )
    tenant_b_conversation = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=shared_conversation_id,
        created_by=UserId.new(),
        status=ConversationStatus.ARCHIVED,
        version=9,
        last_message_sequence=8,
    )
    unknown_tenant_id = TenantId.new()

    async with asyncio.timeout(5), _local_runtime_session() as session:
        session.add_all(
            [
                conversation_to_new_record(tenant_a_conversation),
                conversation_to_new_record(tenant_b_conversation),
            ]
        )
        await session.flush()
        session.expunge_all()

        repository = SqlAlchemyConversationRepository(session)

        tenant_a_result = await repository.get(
            tenant_id=tenant_a_conversation.tenant_id,
            conversation_id=shared_conversation_id,
        )
        tenant_b_result = await repository.get(
            tenant_id=tenant_b_conversation.tenant_id,
            conversation_id=shared_conversation_id,
        )
        unknown_tenant_result = await repository.get(
            tenant_id=unknown_tenant_id,
            conversation_id=shared_conversation_id,
        )

        assert tenant_a_result == tenant_a_conversation
        assert tenant_b_result == tenant_b_conversation
        assert unknown_tenant_result is None
        assert session.in_transaction()
