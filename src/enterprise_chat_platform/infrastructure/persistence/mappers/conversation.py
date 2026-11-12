"""Map conversations between domain and persistence representations."""

from enterprise_chat_platform.domain.chat import (
    Conversation,
    ConversationStatus,
)
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
)


def conversation_to_new_record(
    conversation: Conversation,
) -> ConversationRecord:
    """Create a new transient database record from a domain conversation."""
    return ConversationRecord(
        tenant_id=conversation.tenant_id.value,
        id=conversation.id.value,
        created_by=conversation.created_by.value,
        status=conversation.status.value,
        version=conversation.version,
        last_message_sequence=conversation.last_message_sequence,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def conversation_from_record(
    record: ConversationRecord,
) -> Conversation:
    """Rebuild a validated domain conversation from a database record."""
    return Conversation(
        id=ConversationId(record.id),
        tenant_id=TenantId(record.tenant_id),
        created_by=UserId(record.created_by),
        status=ConversationStatus(record.status),
        version=record.version,
        last_message_sequence=record.last_message_sequence,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
