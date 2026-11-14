"""Core tenant-scoped chat entities and behavior."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from enterprise_chat_platform.domain.agent_run import AgentRun
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
from enterprise_chat_platform.domain.primitives import (
    IdempotencyKey,
    normalize_utc,
)

MAX_USER_MESSAGE_LENGTH = 16_000


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


def _validate_message_content(
    content: str,
    *,
    role: MessageRole,
) -> None:
    label = role.value.capitalize()

    if not content.strip():
        raise DomainValidationError(f"{label} message content cannot be blank")

    if "\x00" in content:
        raise DomainValidationError(f"{label} message content cannot contain NUL characters")

    if len(content) > MAX_USER_MESSAGE_LENGTH:
        raise DomainValidationError(
            f"{label} message content cannot exceed {MAX_USER_MESSAGE_LENGTH} characters"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Message:
    """An immutable user- or assistant-authored conversation message."""

    id: MessageId
    tenant_id: TenantId
    conversation_id: ConversationId
    author_id: UserId
    content: str
    sequence: int
    idempotency_key: IdempotencyKey
    created_at: datetime
    role: MessageRole = MessageRole.USER

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise DomainValidationError("Message sequence must be positive")

        _validate_message_content(
            self.content,
            role=self.role,
        )

        created_at = normalize_utc(
            self.created_at,
            field_name="message.created_at",
        )
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class Conversation:
    """The consistency boundary for accepting chat turns."""

    id: ConversationId
    tenant_id: TenantId
    created_by: UserId
    status: ConversationStatus
    version: int
    last_message_sequence: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.version < 0:
            raise DomainValidationError("Conversation version cannot be negative")

        if self.last_message_sequence < 0:
            raise DomainValidationError("Last message sequence cannot be negative")

        created_at = normalize_utc(
            self.created_at,
            field_name="conversation.created_at",
        )
        updated_at = normalize_utc(
            self.updated_at,
            field_name="conversation.updated_at",
        )

        if updated_at < created_at:
            raise DomainValidationError("Conversation updated_at cannot precede created_at")

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    @classmethod
    def create(
        cls,
        *,
        id: ConversationId,
        tenant_id: TenantId,
        created_by: UserId,
        at: datetime,
    ) -> Self:
        created_at = normalize_utc(
            at,
            field_name="created_at",
        )

        return cls(
            id=id,
            tenant_id=tenant_id,
            created_by=created_by,
            status=ConversationStatus.ACTIVE,
            version=0,
            last_message_sequence=0,
            created_at=created_at,
            updated_at=created_at,
        )

    def accept_user_turn(
        self,
        *,
        trusted_tenant_id: TenantId,
        actor_id: UserId,
        message_id: MessageId,
        agent_run_id: AgentRunId,
        content: str,
        idempotency_key: IdempotencyKey,
        at: datetime,
    ) -> AcceptedUserTurn:
        if trusted_tenant_id != self.tenant_id:
            raise TenantMismatchError("Conversation does not belong to the trusted tenant")

        if self.status is not ConversationStatus.ACTIVE:
            raise InvalidStateTransitionError("Archived conversation cannot accept user turns")

        accepted_at = normalize_utc(
            at,
            field_name="accepted_at",
        )

        if accepted_at < self.updated_at:
            raise DomainValidationError("accepted_at cannot precede conversation updated_at")

        sequence = self.last_message_sequence + 1

        message = Message(
            id=message_id,
            tenant_id=self.tenant_id,
            conversation_id=self.id,
            author_id=actor_id,
            content=content,
            sequence=sequence,
            idempotency_key=idempotency_key,
            created_at=accepted_at,
        )

        agent_run = AgentRun(
            id=agent_run_id,
            tenant_id=self.tenant_id,
            conversation_id=self.id,
            input_message_id=message.id,
            created_at=accepted_at,
        )

        updated_conversation = replace(
            self,
            version=self.version + 1,
            last_message_sequence=sequence,
            updated_at=accepted_at,
        )

        return AcceptedUserTurn(
            updated_conversation=updated_conversation,
            message=message,
            agent_run=agent_run,
        )

    def append_assistant_message(
        self,
        *,
        trusted_tenant_id: TenantId,
        service_author_id: UserId,
        message_id: MessageId,
        content: str,
        idempotency_key: IdempotencyKey,
        at: datetime,
    ) -> AcceptedAssistantMessage:
        """Append a trusted assistant response and advance the aggregate once."""
        if trusted_tenant_id != self.tenant_id:
            raise TenantMismatchError("Conversation does not belong to the trusted tenant")

        if self.status is not ConversationStatus.ACTIVE:
            raise InvalidStateTransitionError(
                "Archived conversation cannot accept assistant messages"
            )

        accepted_at = normalize_utc(
            at,
            field_name="accepted_at",
        )

        if accepted_at < self.updated_at:
            raise DomainValidationError("accepted_at cannot precede conversation updated_at")

        sequence = self.last_message_sequence + 1
        message = Message(
            id=message_id,
            tenant_id=self.tenant_id,
            conversation_id=self.id,
            author_id=service_author_id,
            content=content,
            sequence=sequence,
            idempotency_key=idempotency_key,
            created_at=accepted_at,
            role=MessageRole.ASSISTANT,
        )
        updated_conversation = replace(
            self,
            version=self.version + 1,
            last_message_sequence=sequence,
            updated_at=accepted_at,
        )

        return AcceptedAssistantMessage(
            updated_conversation=updated_conversation,
            message=message,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedUserTurn:
    """The entities produced by accepting one user turn."""

    updated_conversation: Conversation
    message: Message
    agent_run: AgentRun

    def __post_init__(self) -> None:
        tenant_id = self.updated_conversation.tenant_id

        if self.message.tenant_id != tenant_id or self.agent_run.tenant_id != tenant_id:
            raise TenantMismatchError("Accepted-turn entities must belong to the same tenant")

        conversation_id = self.updated_conversation.id

        if (
            self.message.conversation_id != conversation_id
            or self.agent_run.conversation_id != conversation_id
        ):
            raise DomainValidationError(
                "Accepted-turn entities must reference the same conversation"
            )

        if self.agent_run.input_message_id != self.message.id:
            raise DomainValidationError("Agent run must reference the accepted message")

        if self.updated_conversation.last_message_sequence != self.message.sequence:
            raise DomainValidationError("Accepted message sequence must match the conversation")


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedAssistantMessage:
    """The aggregate snapshot and message produced by an assistant response."""

    updated_conversation: Conversation
    message: Message

    def __post_init__(self) -> None:
        conversation = self.updated_conversation

        if self.message.role is not MessageRole.ASSISTANT:
            raise DomainValidationError("Accepted assistant message must use the assistant role")

        if self.message.tenant_id != conversation.tenant_id:
            raise TenantMismatchError("Assistant message must belong to the conversation tenant")

        if self.message.conversation_id != conversation.id:
            raise DomainValidationError("Assistant message must reference the conversation")

        if self.message.sequence != conversation.last_message_sequence:
            raise DomainValidationError("Assistant message sequence must match the conversation")
