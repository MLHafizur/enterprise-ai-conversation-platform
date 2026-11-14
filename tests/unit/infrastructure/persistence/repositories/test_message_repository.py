from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import Message
from enterprise_chat_platform.infrastructure.persistence.models import (
    MessageRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.message import (
    SqlAlchemyMessageRepository,
)


def _session_mock(
    *,
    active_transaction: bool,
) -> tuple[AsyncSession, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.in_transaction.return_value = active_transaction
    return cast(AsyncSession, session_mock), session_mock


def test_message_add_requires_active_transaction() -> None:
    session, session_mock = _session_mock(active_transaction=False)
    repository = SqlAlchemyMessageRepository(session)
    message = cast(Message, MagicMock(spec=Message))

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "message.message_to_new_record"
    ) as mapper:
        with pytest.raises(RuntimeError, match="active transaction"):
            repository.add(message=message)

    mapper.assert_not_called()
    session_mock.add.assert_not_called()


def test_message_add_stages_mapped_record_without_database_io() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    repository = SqlAlchemyMessageRepository(session)
    message = cast(Message, MagicMock(spec=Message))
    record = cast(MessageRecord, MagicMock(spec=MessageRecord))

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "message.message_to_new_record",
        return_value=record,
    ) as mapper:
        added = repository.add(message=message)

    assert added is record
    mapper.assert_called_once_with(message)
    session_mock.add.assert_called_once_with(record)
    session_mock.execute.assert_not_called()
    session_mock.flush.assert_not_called()
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()
