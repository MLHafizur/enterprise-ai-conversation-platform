from enterprise_chat_platform.domain.chat import Message
from enterprise_chat_platform.infrastructure.persistence.models import (
    MessageRecord,
)


def message_to_new_record(message: Message) -> MessageRecord:
    """Create a new transient database record from a domain message."""
    return MessageRecord(
        tenant_id=message.tenant_id.value,
        id=message.id.value,
        conversation_id=message.conversation_id.value,
        author_id=message.author_id.value,
        role=message.role.value,
        content=message.content,
        sequence=message.sequence,
        idempotency_key=message.idempotency_key.value,
        created_at=message.created_at,
    )
