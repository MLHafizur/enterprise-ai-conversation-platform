from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
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


def _session_mock(
    *,
    active_transaction: bool,
) -> tuple[AsyncSession, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.in_transaction.return_value = active_transaction
    return cast(AsyncSession, session_mock), session_mock


@pytest.mark.asyncio
async def test_insert_if_absent_requires_active_transaction() -> None:
    session, session_mock = _session_mock(active_transaction=False)
    repository = SqlAlchemyConversationRepository(session)

    with pytest.raises(RuntimeError, match="active transaction"):
        await repository.insert_if_absent(conversation=_conversation())

    session_mock.execute.assert_not_called()


@pytest.mark.parametrize(
    ("returned_id", "expected"),
    [
        pytest.param(CONVERSATION_ID.value, True, id="inserted"),
        pytest.param(None, False, id="conflict"),
    ],
)
@pytest.mark.asyncio
async def test_insert_if_absent_uses_tenant_scoped_postgres_conflict_target(
    returned_id: object,
    expected: bool,
) -> None:
    session, session_mock = _session_mock(active_transaction=True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = returned_id
    session_mock.execute = AsyncMock(return_value=result)
    repository = SqlAlchemyConversationRepository(session)

    actual = await repository.insert_if_absent(
        conversation=_conversation(),
    )

    assert actual is expected
    session_mock.execute.assert_awaited_once()
    statement = session_mock.execute.await_args.args[0]
    compiled = statement.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
    )
    normalized_sql = " ".join(str(compiled).split())

    assert "INSERT INTO chat.conversations" in normalized_sql
    assert "ON CONFLICT (tenant_id, id) DO NOTHING" in normalized_sql
    assert "RETURNING chat.conversations.id" in normalized_sql
    assert compiled.params == {
        "tenant_id": TENANT_ID.value,
        "id": CONVERSATION_ID.value,
        "created_by": ACTOR_ID.value,
        "status": "active",
        "version": 0,
        "last_message_sequence": 0,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    session_mock.add.assert_not_called()
    session_mock.flush.assert_not_called()
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()
