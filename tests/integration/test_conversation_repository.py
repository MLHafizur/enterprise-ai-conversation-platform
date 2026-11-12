from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
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
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_if_version_matches_persists_one_version_advance() -> None:
    original = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        created_by=UserId.new(),
    )
    updated = replace(
        original,
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=1),
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        session.add(conversation_to_new_record(original))
        await session.flush()
        session.expunge_all()

        repository = SqlAlchemyConversationRepository(session)

        was_updated = await repository.update_if_version_matches(
            expected_version=0,
            updated_conversation=updated,
        )
        stored = await repository.get(
            tenant_id=original.tenant_id,
            conversation_id=original.id,
        )

        assert was_updated is True
        assert stored == updated
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_if_version_matches_rejects_stale_writer() -> None:
    original = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        created_by=UserId.new(),
    )

    first_writer = replace(
        original,
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=1),
    )
    second_writer = replace(
        original,
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=2),
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        session.add(conversation_to_new_record(original))
        await session.flush()
        session.expunge_all()

        repository = SqlAlchemyConversationRepository(session)

        first_result = await repository.update_if_version_matches(
            expected_version=0,
            updated_conversation=first_writer,
        )
        second_result = await repository.update_if_version_matches(
            expected_version=0,
            updated_conversation=second_writer,
        )

        stored = await repository.get(
            tenant_id=original.tenant_id,
            conversation_id=original.id,
        )

        assert first_result is True
        assert second_result is False
        assert stored == first_writer
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_if_version_matches_is_tenant_scoped() -> None:
    shared_conversation_id = ConversationId.new()

    tenant_a_original = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=shared_conversation_id,
        created_by=UserId.new(),
    )
    tenant_b_original = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=shared_conversation_id,
        created_by=UserId.new(),
    )

    tenant_a_updated = replace(
        tenant_a_original,
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=1),
    )
    unknown_tenant_updated = replace(
        tenant_a_original,
        tenant_id=TenantId.new(),
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=2),
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        session.add_all(
            [
                conversation_to_new_record(tenant_a_original),
                conversation_to_new_record(tenant_b_original),
            ]
        )
        await session.flush()
        session.expunge_all()

        repository = SqlAlchemyConversationRepository(session)

        tenant_a_result = await repository.update_if_version_matches(
            expected_version=0,
            updated_conversation=tenant_a_updated,
        )
        unknown_tenant_result = await repository.update_if_version_matches(
            expected_version=0,
            updated_conversation=unknown_tenant_updated,
        )

        stored_tenant_a = await repository.get(
            tenant_id=tenant_a_original.tenant_id,
            conversation_id=shared_conversation_id,
        )
        stored_tenant_b = await repository.get(
            tenant_id=tenant_b_original.tenant_id,
            conversation_id=shared_conversation_id,
        )

        assert tenant_a_result is True
        assert unknown_tenant_result is False
        assert stored_tenant_a == tenant_a_updated
        assert stored_tenant_b == tenant_b_original
        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_if_version_matches_rejects_archived_conversation() -> None:
    archived = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        created_by=UserId.new(),
        status=ConversationStatus.ARCHIVED,
        version=4,
        last_message_sequence=3,
    )
    attempted_update = replace(
        archived,
        version=5,
        updated_at=UPDATED_AT + timedelta(minutes=1),
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        session.add(conversation_to_new_record(archived))
        await session.flush()
        session.expunge_all()

        repository = SqlAlchemyConversationRepository(session)

        was_updated = await repository.update_if_version_matches(
            expected_version=4,
            updated_conversation=attempted_update,
        )
        stored = await repository.get(
            tenant_id=archived.tenant_id,
            conversation_id=archived.id,
        )

        assert was_updated is False
        assert stored == archived
        assert session.in_transaction()


@pytest.mark.parametrize(
    ("expected_version", "updated_version", "message"),
    [
        pytest.param(
            -1,
            0,
            "cannot be negative",
            id="negative-expected-version",
        ),
        pytest.param(
            3,
            3,
            "exactly one greater",
            id="version-not-advanced",
        ),
        pytest.param(
            3,
            5,
            "exactly one greater",
            id="version-skipped",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_if_version_matches_rejects_invalid_version_contract(
    expected_version: int,
    updated_version: int,
    message: str,
) -> None:
    candidate = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        created_by=UserId.new(),
        version=updated_version,
    )

    async with asyncio.timeout(5), _local_runtime_session() as session:
        repository = SqlAlchemyConversationRepository(session)

        with pytest.raises(ValueError, match=message):
            await repository.update_if_version_matches(
                expected_version=expected_version,
                updated_conversation=candidate,
            )

        assert session.in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_if_version_matches_allows_one_concurrent_writer() -> None:
    original = _conversation(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        created_by=UserId.new(),
    )
    writer_a_candidate = replace(
        original,
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=1),
    )
    writer_b_candidate = replace(
        original,
        version=1,
        last_message_sequence=1,
        updated_at=UPDATED_AT + timedelta(minutes=2),
    )

    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    start_barrier = asyncio.Barrier(2)

    async def attempt_update(candidate: Conversation) -> bool:
        async with session_factory() as writer_session:
            async with writer_session.begin():
                repository = SqlAlchemyConversationRepository(writer_session)

                # Both transactions wait here so that they attempt the same
                # expected-version update at approximately the same time.
                await start_barrier.wait()

                return await repository.update_if_version_matches(
                    expected_version=0,
                    updated_conversation=candidate,
                )

    try:
        async with asyncio.timeout(10):
            # Commit the original row so both independent sessions can see it.
            async with session_factory() as seed_session:
                async with seed_session.begin():
                    seed_session.add(conversation_to_new_record(original))

            results = await asyncio.gather(
                attempt_update(writer_a_candidate),
                attempt_update(writer_b_candidate),
            )

            assert results.count(True) == 1
            assert results.count(False) == 1

            async with session_factory() as verification_session:
                repository = SqlAlchemyConversationRepository(verification_session)
                stored = await repository.get(
                    tenant_id=original.tenant_id,
                    conversation_id=original.id,
                )

                assert stored is not None
                assert stored in (
                    writer_a_candidate,
                    writer_b_candidate,
                )
                assert stored.version == 1
    finally:
        try:
            # This test committed its fixture, so remove exactly that row.
            async with session_factory() as cleanup_session:
                async with cleanup_session.begin():
                    await cleanup_session.execute(
                        delete(ConversationRecord).where(
                            ConversationRecord.tenant_id == original.tenant_id.value,
                            ConversationRecord.id == original.id.value,
                        )
                    )
        finally:
            await engine.dispose()
