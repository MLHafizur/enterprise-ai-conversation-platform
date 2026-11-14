"""Append-only SQLAlchemy repository for chat messages."""

from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import Message
from enterprise_chat_platform.infrastructure.persistence.mappers.message import (
    message_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import MessageRecord


class SqlAlchemyMessageRepository:
    """Stage immutable messages in a caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        message: Message,
    ) -> MessageRecord:
        """Stage and return a message record without database I/O."""
        self._require_active_transaction()
        record = message_to_new_record(message)
        self._session.add(record)
        return record

    def _require_active_transaction(self) -> None:
        if not self._session.in_transaction():
            raise RuntimeError("Message persistence requires an active transaction")
