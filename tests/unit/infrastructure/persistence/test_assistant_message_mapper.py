from datetime import UTC, datetime
from uuid import UUID

from enterprise_chat_platform.domain.chat import Message, MessageRole
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.mappers.message import (
    message_to_new_record,
)


def test_assistant_message_maps_role_and_trusted_service_author() -> None:
    tenant_id = TenantId(UUID("11111111-1111-4111-8111-111111111111"))
    conversation_id = ConversationId(UUID("22222222-2222-4222-8222-222222222222"))
    message_id = MessageId(UUID("33333333-3333-4333-8333-333333333333"))
    service_author_id = UserId(UUID("44444444-4444-4444-8444-444444444444"))
    created_at = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
    message = Message(
        id=message_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        author_id=service_author_id,
        role=MessageRole.ASSISTANT,
        content="Assistant response",
        sequence=2,
        idempotency_key=IdempotencyKey("agent-run:run-001"),
        created_at=created_at,
    )

    record = message_to_new_record(message)

    assert record.role == MessageRole.ASSISTANT.value
    assert record.author_id == service_author_id.value
    assert record.content == "Assistant response"
    assert record.sequence == 2
