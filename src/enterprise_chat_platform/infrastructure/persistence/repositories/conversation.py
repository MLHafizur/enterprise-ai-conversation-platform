"""SQLAlchemy repository for tenant-scoped conversations."""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.chat import (
    Conversation,
    ConversationStatus,
)
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

    async def insert_if_absent(
        self,
        *,
        conversation: Conversation,
    ) -> bool:
        """Atomically insert a conversation unless its tenant-scoped ID exists."""
        self._require_active_transaction(
            "Conversation provisioning",
        )

        statement = (
            insert(ConversationRecord)
            .values(
                tenant_id=conversation.tenant_id.value,
                id=conversation.id.value,
                created_by=conversation.created_by.value,
                status=conversation.status.value,
                version=conversation.version,
                last_message_sequence=conversation.last_message_sequence,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ConversationRecord.tenant_id,
                    ConversationRecord.id,
                ]
            )
            .returning(ConversationRecord.id)
        )

        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

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

    async def update_if_version_matches(
        self,
        *,
        expected_version: int,
        updated_conversation: Conversation,
    ) -> bool:
        """Atomically persist mutable fields when the old version matches."""
        if expected_version < 0:
            raise ValueError("Expected conversation version cannot be negative")

        if updated_conversation.version != expected_version + 1:
            raise ValueError(
                "Updated conversation version must be exactly one greater than the expected version"
            )

        statement = (
            update(ConversationRecord)
            .where(
                ConversationRecord.tenant_id == updated_conversation.tenant_id.value,
                ConversationRecord.id == updated_conversation.id.value,
                ConversationRecord.version == expected_version,
                ConversationRecord.status == ConversationStatus.ACTIVE.value,
            )
            .values(
                status=updated_conversation.status.value,
                version=updated_conversation.version,
                last_message_sequence=(updated_conversation.last_message_sequence),
                updated_at=updated_conversation.updated_at,
            )
            .returning(ConversationRecord.version)
            .execution_options(synchronize_session="fetch")
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None

    def _require_active_transaction(
        self,
        action: str,
    ) -> None:
        if not self._session.in_transaction():
            raise RuntimeError(f"{action} requires an active transaction")
