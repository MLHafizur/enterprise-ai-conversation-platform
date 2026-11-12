"""SQLAlchemy repository for tenant-scoped conversations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
)


class SqlAlchemyConversationRepository:
    """Load conversation aggregates using a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        """Return one tenant-scoped conversation or None."""
        statement = select(ConversationRecord).where(
            ConversationRecord.tenant_id == tenant_id.value,
            ConversationRecord.id == conversation_id.value,
        )

        result = await self._session.execute(statement)
        record = result.scalar_one_or_none()

        if record is None:
            return None

        return conversation_from_record(record)
