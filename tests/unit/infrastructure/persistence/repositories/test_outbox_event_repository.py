from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.accept_user_turn import (
    AgentRunQueuedEvent,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.outbox_event import (
    SqlAlchemyOutboxEventRepository,
)


def _session_mock(
    *,
    active_transaction: bool,
) -> tuple[AsyncSession, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.in_transaction.return_value = active_transaction
    return cast(AsyncSession, session_mock), session_mock


def test_outbox_event_add_requires_active_transaction() -> None:
    session, session_mock = _session_mock(active_transaction=False)
    repository = SqlAlchemyOutboxEventRepository(session)
    event = cast(
        AgentRunQueuedEvent,
        MagicMock(spec=AgentRunQueuedEvent),
    )

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "outbox_event.agent_run_queued_event_to_new_record"
    ) as mapper:
        with pytest.raises(RuntimeError, match="active transaction"):
            repository.add(event=event)

    mapper.assert_not_called()
    session_mock.add.assert_not_called()


def test_outbox_event_add_stages_mapped_record_without_database_io() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    repository = SqlAlchemyOutboxEventRepository(session)
    event = cast(
        AgentRunQueuedEvent,
        MagicMock(spec=AgentRunQueuedEvent),
    )
    record = cast(
        OutboxEventRecord,
        MagicMock(spec=OutboxEventRecord),
    )

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "outbox_event.agent_run_queued_event_to_new_record",
        return_value=record,
    ) as mapper:
        repository.add(event=event)

    mapper.assert_called_once_with(event)
    session_mock.add.assert_called_once_with(record)
    session_mock.execute.assert_not_called()
    session_mock.flush.assert_not_called()
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()
