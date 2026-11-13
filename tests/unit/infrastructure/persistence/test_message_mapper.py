from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import inspect

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
from enterprise_chat_platform.infrastructure.persistence.models import (
    MessageRecord,
)

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_UUID = UUID("22222222-2222-4222-8222-222222222222")
MESSAGE_UUID = UUID("33333333-3333-4333-8333-333333333333")
AUTHOR_UUID = UUID("44444444-4444-4444-8444-444444444444")

CREATED_AT = datetime(2026, 11, 12, 18, 30, tzinfo=UTC)
CONTENT = "  Preserve this exact user formatting.\n"
IDEMPOTENCY_KEY = "Request-AbC-001"


def _message() -> Message:
    return Message(
        id=MessageId(MESSAGE_UUID),
        tenant_id=TenantId(TENANT_UUID),
        conversation_id=ConversationId(CONVERSATION_UUID),
        author_id=UserId(AUTHOR_UUID),
        content=CONTENT,
        sequence=7,
        idempotency_key=IdempotencyKey(IDEMPOTENCY_KEY),
        created_at=CREATED_AT,
    )


def test_message_maps_to_new_record_without_losing_information() -> None:
    record = message_to_new_record(_message())

    assert isinstance(record, MessageRecord)
    assert record.tenant_id == TENANT_UUID
    assert record.id == MESSAGE_UUID
    assert record.conversation_id == CONVERSATION_UUID
    assert record.author_id == AUTHOR_UUID
    assert record.role == MessageRole.USER.value == "user"
    assert record.content == CONTENT
    assert record.sequence == 7
    assert record.idempotency_key == IDEMPOTENCY_KEY
    assert record.created_at == CREATED_AT


def test_message_mapper_returns_an_unsaved_transient_record() -> None:
    record = message_to_new_record(_message())

    state = inspect(record)

    assert state.transient is True
    assert state.session is None
