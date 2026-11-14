"""Append-only SQLAlchemy repository for transactional outbox events."""

from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.accept_user_turn import (
    AgentRunQueuedEvent,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.outbox_event import (
    agent_run_queued_event_to_new_record,
)


class SqlAlchemyOutboxEventRepository:
    """Stage outbox events in a caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        event: AgentRunQueuedEvent,
    ) -> None:
        """Stage an outbox event without flushing or committing."""
        self._require_active_transaction()
        self._session.add(agent_run_queued_event_to_new_record(event))

    def _require_active_transaction(self) -> None:
        if not self._session.in_transaction():
            raise RuntimeError("Outbox persistence requires an active transaction")
