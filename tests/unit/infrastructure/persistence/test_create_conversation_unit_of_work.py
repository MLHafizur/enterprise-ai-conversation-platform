from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence import (
    create_conversation_unit_of_work as uow_module,
)
from enterprise_chat_platform.infrastructure.persistence.create_conversation_unit_of_work import (
    SqlAlchemyCreateConversationUnitOfWork,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = ConversationId.parse("33333333-3333-4333-8333-333333333333")
CREATED_AT = datetime(2026, 11, 13, 16, 0, tzinfo=UTC)


def _conversation() -> Conversation:
    return Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=ACTOR_ID,
        at=CREATED_AT,
    )


@dataclass(frozen=True, slots=True)
class _Harness:
    unit_of_work: SqlAlchemyCreateConversationUnitOfWork
    session_factory: MagicMock
    session: MagicMock
    transaction: MagicMock
    repository: MagicMock
    repository_factory: MagicMock


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    session = MagicMock(spec=AsyncSession)
    transaction = MagicMock(spec=AsyncSessionTransaction)
    transaction.is_active = True
    session.begin.return_value = transaction
    session_factory = MagicMock(return_value=session)
    repository = MagicMock(spec=SqlAlchemyConversationRepository)
    repository_factory = MagicMock(return_value=repository)

    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyConversationRepository",
        repository_factory,
    )

    return _Harness(
        unit_of_work=SqlAlchemyCreateConversationUnitOfWork(
            cast(AsyncSessionFactory, session_factory),
        ),
        session_factory=session_factory,
        session=session,
        transaction=transaction,
        repository=repository,
        repository_factory=repository_factory,
    )


@pytest.mark.asyncio
async def test_context_owns_one_session_transaction_and_repository(
    harness: _Harness,
) -> None:
    async with harness.unit_of_work as entered:
        assert entered is harness.unit_of_work
        harness.session_factory.assert_called_once_with()
        harness.session.begin.assert_called_once_with()
        harness.transaction.start.assert_awaited_once_with()
        harness.repository_factory.assert_called_once_with(harness.session)

    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_successful_insert_can_commit_exactly_owned_transaction(
    harness: _Harness,
) -> None:
    conversation = _conversation()
    harness.repository.insert_if_absent.return_value = True

    async with harness.unit_of_work:
        inserted = await harness.unit_of_work.insert_if_absent(
            conversation=conversation,
        )
        await harness.unit_of_work.commit()

    assert inserted is True
    harness.repository.insert_if_absent.assert_awaited_once_with(
        conversation=conversation,
    )
    harness.transaction.commit.assert_awaited_once_with()
    harness.transaction.rollback.assert_not_awaited()
    harness.session.flush.assert_not_awaited()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_conflict_lookup_uses_same_transaction_and_rolls_back_read_only(
    harness: _Harness,
) -> None:
    conversation = _conversation()
    harness.repository.insert_if_absent.return_value = False
    harness.repository.get.return_value = conversation

    async with harness.unit_of_work:
        inserted = await harness.unit_of_work.insert_if_absent(
            conversation=conversation,
        )
        loaded = await harness.unit_of_work.get_conversation(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
        )

    assert inserted is False
    assert loaded is conversation
    harness.repository.get.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
    )
    harness.transaction.commit.assert_not_awaited()
    harness.transaction.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_only_one_insert_attempt_is_allowed(
    harness: _Harness,
) -> None:
    harness.repository.insert_if_absent.return_value = False

    with pytest.raises(RuntimeError, match="Only one conversation"):
        async with harness.unit_of_work:
            await harness.unit_of_work.insert_if_absent(
                conversation=_conversation(),
            )
            await harness.unit_of_work.insert_if_absent(
                conversation=_conversation(),
            )

    harness.repository.insert_if_absent.assert_awaited_once()
    harness.transaction.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_conflicted_insert_cannot_be_committed(
    harness: _Harness,
) -> None:
    harness.repository.insert_if_absent.return_value = False

    with pytest.raises(RuntimeError, match="newly inserted conversation"):
        async with harness.unit_of_work:
            await harness.unit_of_work.insert_if_absent(
                conversation=_conversation(),
            )
            await harness.unit_of_work.commit()

    harness.transaction.commit.assert_not_awaited()
    harness.transaction.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_closes(
    harness: _Harness,
) -> None:
    harness.repository.insert_if_absent.return_value = True
    harness.transaction.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        async with harness.unit_of_work:
            await harness.unit_of_work.insert_if_absent(
                conversation=_conversation(),
            )
            await harness.unit_of_work.commit()

    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_operations_outside_context_are_rejected(
    harness: _Harness,
) -> None:
    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.insert_if_absent(
            conversation=_conversation(),
        )

    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.get_conversation(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
        )

    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.commit()


@pytest.mark.asyncio
async def test_unit_of_work_instance_cannot_be_reentered(
    harness: _Harness,
) -> None:
    async with harness.unit_of_work:
        pass

    with pytest.raises(RuntimeError, match="cannot be entered more than once"):
        async with harness.unit_of_work:
            pass
