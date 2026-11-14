"""Application orchestration for idempotent conversation provisioning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from enterprise_chat_platform.application.accept_user_turn import (
    ActorContext as ActorContext,
)
from enterprise_chat_platform.application.errors import (
    ConversationNotFoundError,
)
from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateConversationCommand:
    """Client-selected identity for a PUT-style conversation resource."""

    conversation_id: ConversationId


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateConversationResult:
    """Stable provisioning result returned for creation and safe replay."""

    conversation_id: ConversationId
    created_at: datetime
    replayed: bool


class Clock(Protocol):
    """Supply server-controlled time."""

    def now(self) -> datetime: ...


class CreateConversationUnitOfWork(Protocol):
    """Transactional persistence boundary for conversation provisioning."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def insert_if_absent(
        self,
        *,
        conversation: Conversation,
    ) -> bool: ...

    async def get_conversation(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None: ...

    async def commit(self) -> None: ...


class CreateConversationHandler:
    """Provision one client-addressable conversation without ownership leaks."""

    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], CreateConversationUnitOfWork],
        clock: Clock,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    async def handle(
        self,
        *,
        actor: ActorContext,
        command: CreateConversationCommand,
    ) -> CreateConversationResult:
        candidate = Conversation.create(
            id=command.conversation_id,
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            at=self._clock.now(),
        )

        async with self._unit_of_work_factory() as unit_of_work:
            inserted = await unit_of_work.insert_if_absent(
                conversation=candidate,
            )

            if inserted:
                await unit_of_work.commit()
                return _result_from_conversation(
                    candidate,
                    replayed=False,
                )

            existing = await unit_of_work.get_conversation(
                tenant_id=actor.tenant_id,
                conversation_id=command.conversation_id,
            )

            if not _is_safe_replay(
                existing=existing,
                actor=actor,
                command=command,
            ):
                raise ConversationNotFoundError("Conversation was not found")

            assert existing is not None
            return _result_from_conversation(
                existing,
                replayed=True,
            )


def _is_safe_replay(
    *,
    existing: Conversation | None,
    actor: ActorContext,
    command: CreateConversationCommand,
) -> bool:
    return (
        existing is not None
        and existing.tenant_id == actor.tenant_id
        and existing.id == command.conversation_id
        and existing.created_by == actor.actor_id
    )


def _result_from_conversation(
    conversation: Conversation,
    *,
    replayed: bool,
) -> CreateConversationResult:
    return CreateConversationResult(
        conversation_id=conversation.id,
        created_at=conversation.created_at,
        replayed=replayed,
    )
