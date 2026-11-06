from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from enterprise_chat_platform.domain.chat import (
    MAX_USER_MESSAGE_LENGTH,
    AcceptedUserTurn,
    AgentRunStatus,
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
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = TenantId.parse("22222222-2222-4222-8222-222222222222")
USER_ID = UserId.parse("33333333-3333-4333-8333-333333333333")
CONVERSATION_ID = ConversationId.parse("44444444-4444-4444-8444-444444444444")
FIRST_MESSAGE_ID = MessageId.parse("55555555-5555-4555-8555-555555555555")
FIRST_RUN_ID = AgentRunId.parse("66666666-6666-4666-8666-666666666666")
SECOND_MESSAGE_ID = MessageId.parse("77777777-7777-4777-8777-777777777777")
SECOND_RUN_ID = AgentRunId.parse("88888888-8888-4888-8888-888888888888")

CREATED_AT = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
FIRST_ACCEPTED_AT = CREATED_AT + timedelta(seconds=1)
SECOND_ACCEPTED_AT = CREATED_AT + timedelta(seconds=2)


def _create_conversation() -> Conversation:
    return Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=USER_ID,
        at=CREATED_AT,
    )


def _accept_first_turn(
    conversation: Conversation,
    *,
    trusted_tenant_id: TenantId = TENANT_ID,
    content: str = "  Preserve this authored text.\n",
    at: datetime = FIRST_ACCEPTED_AT,
) -> AcceptedUserTurn:
    return conversation.accept_user_turn(
        trusted_tenant_id=trusted_tenant_id,
        actor_id=USER_ID,
        message_id=FIRST_MESSAGE_ID,
        agent_run_id=FIRST_RUN_ID,
        content=content,
        idempotency_key=IdempotencyKey("request-001"),
        at=at,
    )


def test_new_conversation_starts_empty_and_active() -> None:
    conversation = _create_conversation()

    assert conversation.id == CONVERSATION_ID
    assert conversation.tenant_id == TENANT_ID
    assert conversation.created_by == USER_ID
    assert conversation.status is ConversationStatus.ACTIVE
    assert conversation.version == 0
    assert conversation.last_message_sequence == 0
    assert conversation.created_at == CREATED_AT
    assert conversation.updated_at == CREATED_AT


def test_accept_user_turn_creates_message_and_queued_run() -> None:
    conversation = _create_conversation()

    accepted = _accept_first_turn(conversation)

    # The original aggregate remains unchanged.
    assert conversation.version == 0
    assert conversation.last_message_sequence == 0

    updated = accepted.updated_conversation
    assert updated is not conversation
    assert updated.version == 1
    assert updated.last_message_sequence == 1
    assert updated.updated_at == FIRST_ACCEPTED_AT

    message = accepted.message
    assert message.id == FIRST_MESSAGE_ID
    assert message.tenant_id == TENANT_ID
    assert message.conversation_id == CONVERSATION_ID
    assert message.author_id == USER_ID
    assert message.role is MessageRole.USER
    assert message.content == "  Preserve this authored text.\n"
    assert message.sequence == 1
    assert message.idempotency_key == IdempotencyKey("request-001")
    assert message.created_at == FIRST_ACCEPTED_AT

    run = accepted.agent_run
    assert run.id == FIRST_RUN_ID
    assert run.tenant_id == TENANT_ID
    assert run.conversation_id == CONVERSATION_ID
    assert run.input_message_id == FIRST_MESSAGE_ID
    assert run.status is AgentRunStatus.QUEUED
    assert run.created_at == FIRST_ACCEPTED_AT


def test_second_turn_increments_version_and_sequence() -> None:
    first = _accept_first_turn(_create_conversation())

    second = first.updated_conversation.accept_user_turn(
        trusted_tenant_id=TENANT_ID,
        actor_id=USER_ID,
        message_id=SECOND_MESSAGE_ID,
        agent_run_id=SECOND_RUN_ID,
        content="Second message",
        idempotency_key=IdempotencyKey("request-002"),
        at=SECOND_ACCEPTED_AT,
    )

    assert second.updated_conversation.version == 2
    assert second.updated_conversation.last_message_sequence == 2
    assert second.message.sequence == 2


def test_accept_user_turn_rejects_cross_tenant_access() -> None:
    conversation = _create_conversation()

    with pytest.raises(
        TenantMismatchError,
        match="trusted tenant",
    ):
        _accept_first_turn(
            conversation,
            trusted_tenant_id=OTHER_TENANT_ID,
        )

    assert conversation.version == 0
    assert conversation.last_message_sequence == 0


def test_archived_conversation_rejects_new_turn() -> None:
    conversation = replace(
        _create_conversation(),
        status=ConversationStatus.ARCHIVED,
    )

    with pytest.raises(
        InvalidStateTransitionError,
        match="Archived conversation",
    ):
        _accept_first_turn(conversation)


@pytest.mark.parametrize("content", ["", " ", "\n\t"])
def test_accept_user_turn_rejects_blank_content(content: str) -> None:
    with pytest.raises(
        DomainValidationError,
        match="cannot be blank",
    ):
        _accept_first_turn(
            _create_conversation(),
            content=content,
        )


def test_accept_user_turn_rejects_oversized_content() -> None:
    content = "a" * (MAX_USER_MESSAGE_LENGTH + 1)

    with pytest.raises(
        DomainValidationError,
        match=f"cannot exceed {MAX_USER_MESSAGE_LENGTH} characters",
    ):
        _accept_first_turn(
            _create_conversation(),
            content=content,
        )


def test_accept_user_turn_rejects_nul_character() -> None:
    with pytest.raises(
        DomainValidationError,
        match="cannot contain NUL characters",
    ):
        _accept_first_turn(
            _create_conversation(),
            content="unsafe\x00content",
        )


def test_accept_user_turn_rejects_naive_timestamp() -> None:
    naive = datetime(2026, 11, 6, 15, 1)

    with pytest.raises(
        DomainValidationError,
        match="accepted_at must be timezone-aware",
    ):
        _accept_first_turn(
            _create_conversation(),
            at=naive,
        )


def test_accepted_entities_are_immutable() -> None:
    accepted = _accept_first_turn(_create_conversation())

    with pytest.raises(FrozenInstanceError):
        # Intentionally exercise the runtime immutability guard.
        setattr(accepted.message, "content", "tampered")  # noqa: B010


def test_accept_user_turn_accepts_maximum_content_length() -> None:
    content = "a" * MAX_USER_MESSAGE_LENGTH

    accepted = _accept_first_turn(
        _create_conversation(),
        content=content,
    )

    assert accepted.message.content == content


def test_accept_user_turn_rejects_timestamp_before_conversation_update() -> None:
    conversation = _create_conversation()
    earlier = CREATED_AT - timedelta(microseconds=1)

    with pytest.raises(
        DomainValidationError,
        match="accepted_at cannot precede conversation updated_at",
    ):
        _accept_first_turn(
            conversation,
            at=earlier,
        )
