from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.agent_run import AgentRun, AgentRunStatus
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.agent_run import (
    SqlAlchemyAgentRunRepository,
)


def _session_mock(
    *,
    active_transaction: bool,
) -> tuple[AsyncSession, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.in_transaction.return_value = active_transaction
    return cast(AsyncSession, session_mock), session_mock


def test_agent_run_add_requires_active_transaction() -> None:
    session, session_mock = _session_mock(active_transaction=False)
    repository = SqlAlchemyAgentRunRepository(session)
    agent_run = cast(
        AgentRun,
        MagicMock(spec=AgentRun, status=AgentRunStatus.QUEUED),
    )

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "agent_run.agent_run_to_new_record"
    ) as mapper:
        with pytest.raises(RuntimeError, match="active transaction"):
            repository.add(agent_run=agent_run)

    mapper.assert_not_called()
    session_mock.add.assert_not_called()


def test_agent_run_add_stages_mapped_record_without_database_io() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    repository = SqlAlchemyAgentRunRepository(session)
    agent_run = cast(
        AgentRun,
        MagicMock(spec=AgentRun, status=AgentRunStatus.QUEUED),
    )
    record = cast(AgentRunRecord, MagicMock(spec=AgentRunRecord))

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "agent_run.agent_run_to_new_record",
        return_value=record,
    ) as mapper:
        added = repository.add(agent_run=agent_run)

    assert added is record
    mapper.assert_called_once_with(agent_run)
    session_mock.add.assert_called_once_with(record)
    session_mock.execute.assert_not_called()
    session_mock.flush.assert_not_called()
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [
        AgentRunStatus.RUNNING,
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    ],
)
def test_agent_run_add_rejects_nonqueued_lifecycle_snapshots(
    status: AgentRunStatus,
) -> None:
    session, session_mock = _session_mock(active_transaction=True)
    repository = SqlAlchemyAgentRunRepository(session)
    agent_run = cast(
        AgentRun,
        MagicMock(spec=AgentRun, status=status),
    )

    with patch(
        "enterprise_chat_platform.infrastructure.persistence.repositories."
        "agent_run.agent_run_to_new_record"
    ) as mapper:
        with pytest.raises(ValueError, match="must be queued"):
            repository.add(agent_run=agent_run)

    mapper.assert_not_called()
    session_mock.add.assert_not_called()
