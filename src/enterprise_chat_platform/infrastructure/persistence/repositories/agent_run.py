"""Append-only SQLAlchemy repository for new agent runs."""

from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.agent_run import AgentRun, AgentRunStatus
from enterprise_chat_platform.infrastructure.persistence.mappers.agent_run import (
    agent_run_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import AgentRunRecord


class SqlAlchemyAgentRunRepository:
    """Stage new agent runs in a caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        agent_run: AgentRun,
    ) -> AgentRunRecord:
        """Stage and return an agent-run record without database I/O."""
        self._require_active_transaction()

        if agent_run.status is not AgentRunStatus.QUEUED:
            raise ValueError("A newly persisted agent run must be queued")

        record = agent_run_to_new_record(agent_run)
        self._session.add(record)
        return record

    def _require_active_transaction(self) -> None:
        if not self._session.in_transaction():
            raise RuntimeError("Agent run persistence requires an active transaction")
