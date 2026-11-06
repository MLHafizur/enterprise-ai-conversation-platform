"""Core tenant-scoped chat entities and behavior."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

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


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _validate_user_message_content(content: str) -> None:
    if not content.strip():
        raise DomainValidationError("User message content cannot be blank")

    if "\x00" in content:
        raise DomainValidationError("User message content cannot contain NUL characters")

    if len(content) > MAX_USER_MESSAGE_LENGTH:
        raise DomainValidationError(
            f"User message content cannot exceed {MAX_USER_MESSAGE_LENGTH} characters"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Message:
    """An immutable user-authored message."""

    id: MessageId
    tenant_id: TenantId
    conversation_id: ConversationId
    author_id: UserId
    content: str
    sequence: int
    idempotency_key: IdempotencyKey
    created_at: datetime
    role: MessageRole = field(
        default=MessageRole.USER,
        init=False,
    )

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise DomainValidationError("Message sequence must be positive")

        _validate_user_message_content(self.content)

        created_at = normalize_utc(
            self.created_at,
            field_name="message.created_at",
        )
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRun:
    """An asynchronous agent run created from a user message."""

    id: AgentRunId
    tenant_id: TenantId
    conversation_id: ConversationId
    input_message_id: MessageId
    created_at: datetime
    status: AgentRunStatus = field(
        default=AgentRunStatus.QUEUED,
        init=False,
    )

    def __post_init__(self) -> None:
        created_at = normalize_utc(
            self.created_at,
            field_name="agent_run.created_at",
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
