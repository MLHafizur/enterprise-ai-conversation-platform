from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)
from enterprise_chat_platform.settings import Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CREATED_AT = datetime(2026, 11, 13, 16, 0, tzinfo=UTC)


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
    owner_id: UserId,
    at: datetime = CREATED_AT,
) -> Conversation:
    return Conversation.create(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=owner_id,
        at=at,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_first_insert_wins_and_conflict_preserves_original_row() -> None:
    tenant_id = TenantId.new()
    conversation_id = ConversationId.new()
    original = _conversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        owner_id=UserId.new(),
    )
    conflicting = _conversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        owner_id=UserId.new(),
        at=CREATED_AT + timedelta(minutes=1),
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        repository = SqlAlchemyConversationRepository(session)

        first_inserted = await repository.insert_if_absent(
            conversation=original,
        )
        second_inserted = await repository.insert_if_absent(
            conversation=conflicting,
        )
        stored = await repository.get(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )

        assert first_inserted is True
        assert second_inserted is False
        assert stored == original
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_client_selected_id_is_isolated_by_tenant() -> None:
    shared_conversation_id = ConversationId.new()
    tenant_a = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=shared_conversation_id,
        owner_id=UserId.new(),
    )
    tenant_b = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=shared_conversation_id,
        owner_id=UserId.new(),
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        repository = SqlAlchemyConversationRepository(session)

        tenant_a_inserted = await repository.insert_if_absent(
            conversation=tenant_a,
        )
        tenant_b_inserted = await repository.insert_if_absent(
            conversation=tenant_b,
        )

        assert tenant_a_inserted is True
        assert tenant_b_inserted is True
        assert (
            await repository.get(
                tenant_id=tenant_a.tenant_id,
                conversation_id=shared_conversation_id,
            )
            == tenant_a
        )
        assert (
            await repository.get(
                tenant_id=tenant_b.tenant_id,
                conversation_id=shared_conversation_id,
            )
            == tenant_b
        )
