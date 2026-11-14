from dataclasses import replace
from datetime import UTC, datetime

import pytest

from enterprise_chat_platform.domain.chat import (
    MAX_USER_MESSAGE_LENGTH,
    Conversation,
    ConversationStatus,
    MessageRole,
)
from enterprise_chat_platform.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    TenantMismatchError,
)
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = TenantId.parse("22222222-2222-4222-8222-222222222222")
OWNER_ID = UserId.parse("33333333-3333-4333-8333-333333333333")
SERVICE_AUTHOR_ID = UserId.parse("44444444-4444-4444-8444-444444444444")
CONVERSATION_ID = ConversationId.parse("55555555-5555-4555-8555-555555555555")
MESSAGE_ID = MessageId.parse("66666666-6666-4666-8666-666666666666")
CREATED_AT = datetime(2026, 11, 13, 16, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 11, 13, 16, 1, tzinfo=UTC)


def _conversation() -> Conversation:
    return Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=OWNER_ID,
        at=CREATED_AT,
    )


def test_append_assistant_message_advances_conversation_once() -> None:
    original = _conversation()

    accepted = original.append_assistant_message(
        trusted_tenant_id=TENANT_ID,
        service_author_id=SERVICE_AUTHOR_ID,
        message_id=MESSAGE_ID,
        content="A deterministic assistant response.",
        idempotency_key=IdempotencyKey("agent-run:run-001"),
        at=COMPLETED_AT,
    )

    assert original.version == 0
    assert original.last_message_sequence == 0
    assert accepted.updated_conversation.version == 1
    assert accepted.updated_conversation.last_message_sequence == 1
    assert accepted.updated_conversation.updated_at == COMPLETED_AT
    assert accepted.message.role is MessageRole.ASSISTANT
    assert accepted.message.author_id == SERVICE_AUTHOR_ID
    assert accepted.message.sequence == 1
    assert accepted.message.created_at == COMPLETED_AT


def test_append_assistant_message_rejects_untrusted_tenant() -> None:
    with pytest.raises(TenantMismatchError, match="trusted tenant"):
        _conversation().append_assistant_message(
            trusted_tenant_id=OTHER_TENANT_ID,
            service_author_id=SERVICE_AUTHOR_ID,
            message_id=MESSAGE_ID,
            content="Response",
            idempotency_key=IdempotencyKey("agent-run:run-001"),
            at=COMPLETED_AT,
        )


def test_archived_conversation_rejects_assistant_message() -> None:
    conversation = replace(
        _conversation(),
        status=ConversationStatus.ARCHIVED,
    )

    with pytest.raises(InvalidStateTransitionError, match="Archived conversation"):
        conversation.append_assistant_message(
            trusted_tenant_id=TENANT_ID,
            service_author_id=SERVICE_AUTHOR_ID,
            message_id=MESSAGE_ID,
            content="Response",
            idempotency_key=IdempotencyKey("agent-run:run-001"),
            at=COMPLETED_AT,
        )


@pytest.mark.parametrize(
    "content",
    ["", "   ", "contains\x00nul", "a" * (MAX_USER_MESSAGE_LENGTH + 1)],
)
def test_assistant_message_uses_message_content_invariants(content: str) -> None:
    with pytest.raises(DomainValidationError, match="Assistant message content"):
        _conversation().append_assistant_message(
            trusted_tenant_id=TENANT_ID,
            service_author_id=SERVICE_AUTHOR_ID,
            message_id=MESSAGE_ID,
            content=content,
            idempotency_key=IdempotencyKey("agent-run:run-001"),
            at=COMPLETED_AT,
        )
