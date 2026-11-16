"""Application orchestration for accepting one user turn."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol, Self

from enterprise_chat_platform.application.errors import (
    ConversationNotFoundError,
    IdempotencyConflictError,
)
from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
from enterprise_chat_platform.domain.assistant_profile import AssistantProfile
from enterprise_chat_platform.domain.chat import AcceptedUserTurn, Conversation
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey

ACCEPT_USER_TURN_OPERATION: Literal["accept_user_turn.v1"] = "accept_user_turn.v1"
AGENT_RUN_QUEUED_EVENT_TYPE: Literal["agent_run.queued.v1"] = "agent_run.queued.v1"


@dataclass(frozen=True, slots=True, kw_only=True)
class ActorContext:
    """Identity obtained from trusted authentication infrastructure."""

    tenant_id: TenantId
    actor_id: UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptUserTurnCommand:
    """Client-controlled data required to accept a user turn."""

    conversation_id: ConversationId
    content: str
    idempotency_key: IdempotencyKey


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptUserTurnResult:
    """Stable response produced by a successful acceptance."""

    conversation_id: ConversationId
    conversation_version: int
    message_id: MessageId
    message_sequence: int
    agent_run_id: AgentRunId
    accepted_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class IdempotencyScope:
    """Unique scope in which an idempotency key is interpreted."""

    tenant_id: TenantId
    conversation_id: ConversationId
    key: IdempotencyKey
    operation: Literal["accept_user_turn.v1"] = field(
        default=ACCEPT_USER_TURN_OPERATION,
        init=False,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class IdempotencyReceipt:
    """Original result recorded for an idempotently accepted command."""

    scope: IdempotencyScope
    request_fingerprint: str
    result: AcceptUserTurnResult


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRunQueuedEvent:
    """Outbox event requesting asynchronous agent execution."""

    id: EventId
    tenant_id: TenantId
    conversation_id: ConversationId
    agent_run_id: AgentRunId
    input_message_id: MessageId
    conversation_version: int
    occurred_at: datetime
    event_type: Literal["agent_run.queued.v1"] = field(
        default=AGENT_RUN_QUEUED_EVENT_TYPE,
        init=False,
    )
    schema_version: Literal[1] = field(default=1, init=False)


class Clock(Protocol):
    """Supplies server-controlled time."""

    def now(self) -> datetime: ...


class TurnIds(Protocol):
    """Generates strongly typed identifiers for one accepted turn."""

    def new_message_id(self) -> MessageId: ...

    def new_agent_run_id(self) -> AgentRunId: ...

    def new_event_id(self) -> EventId: ...


class ConversationWriteAuthorizer(Protocol):
    """Checks whether an authenticated actor may write a conversation."""

    async def can_write(
        self,
        *,
        actor: ActorContext,
        conversation: Conversation,
    ) -> bool: ...


class AcceptUserTurnUnitOfWork(Protocol):
    """Transactional persistence boundary for this user case."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def get_conversation(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None: ...

    async def get_idempotency_receipt(
        self,
        *,
        scope: IdempotencyScope,
    ) -> IdempotencyReceipt | None: ...

    async def get_or_create_active_assistant_profile(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
    ) -> AssistantProfile: ...

    def stage_accepted_turn(
        self,
        *,
        expected_conversation_version: int,
        accepted_turn: AcceptedUserTurn,
        receipt: IdempotencyReceipt,
        event: AgentRunQueuedEvent,
    ) -> None: ...

    async def commit(self) -> None: ...


class AcceptUserTurnHandler:
    """Coordinates authorization, idempotency, domain logic, and commit."""

    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], AcceptUserTurnUnitOfWork],
        clock: Clock,
        ids: TurnIds,
        authorizer: ConversationWriteAuthorizer,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._ids = ids
        self._authorizer = authorizer

    async def handle(
        self,
        *,
        actor: ActorContext,
        command: AcceptUserTurnCommand,
    ) -> AcceptUserTurnResult:
        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.get_conversation(
                tenant_id=actor.tenant_id,
                conversation_id=command.conversation_id,
            )

            if conversation is None:
                raise ConversationNotFoundError("Conversation was not found")

            can_write = await self._authorizer.can_write(
                actor=actor,
                conversation=conversation,
            )

            if not can_write:
                raise ConversationNotFoundError("Conversation was not found")

            scope = IdempotencyScope(
                tenant_id=actor.tenant_id,
                conversation_id=command.conversation_id,
                key=command.idempotency_key,
            )
            request_fingerprint = _create_request_fingerprint(
                actor=actor,
                command=command,
            )

            existing_receipt = await unit_of_work.get_idempotency_receipt(
                scope=scope,
            )

            if existing_receipt is not None:
                if existing_receipt.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used for another request"
                    )

                return replace(
                    existing_receipt.result,
                    replayed=True,
                )

            accepted_at = self._clock.now()
            profile = await unit_of_work.get_or_create_active_assistant_profile(
                tenant_id=actor.tenant_id,
                default_profile=default_assistant_profile(
                    tenant_id=actor.tenant_id,
                    created_at=accepted_at,
                ),
            )
            if profile.tenant_id != actor.tenant_id:
                raise RuntimeError("Active assistant profile belongs to another tenant")

            expected_version = conversation.version

            accepted_turn = conversation.accept_user_turn(
                trusted_tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                message_id=self._ids.new_message_id(),
                agent_run_id=self._ids.new_agent_run_id(),
                assistant_profile_ref=profile.ref,
                content=command.content,
                idempotency_key=command.idempotency_key,
                at=accepted_at,
            )

            result = AcceptUserTurnResult(
                conversation_id=accepted_turn.updated_conversation.id,
                conversation_version=accepted_turn.updated_conversation.version,
                message_id=accepted_turn.message.id,
                message_sequence=accepted_turn.message.sequence,
                agent_run_id=accepted_turn.agent_run.id,
                accepted_at=accepted_turn.message.created_at,
                replayed=False,
            )

            event = AgentRunQueuedEvent(
                id=self._ids.new_event_id(),
                tenant_id=accepted_turn.agent_run.tenant_id,
                conversation_id=accepted_turn.agent_run.conversation_id,
                agent_run_id=accepted_turn.agent_run.id,
                input_message_id=accepted_turn.message.id,
                conversation_version=accepted_turn.updated_conversation.version,
                occurred_at=accepted_turn.message.created_at,
            )

            receipt = IdempotencyReceipt(
                scope=scope,
                request_fingerprint=request_fingerprint,
                result=result,
            )

            unit_of_work.stage_accepted_turn(
                expected_conversation_version=expected_version,
                accepted_turn=accepted_turn,
                receipt=receipt,
                event=event,
            )
            await unit_of_work.commit()

            return result


def _create_request_fingerprint(
    *,
    actor: ActorContext,
    command: AcceptUserTurnCommand,
) -> str:
    """Create a stable digest without storing duplicate message content."""

    payload = json.dumps(
        {
            "actor_id": str(actor.actor_id),
            "content": command.content,
            "conversation_id": str(command.conversation_id),
            "operation": ACCEPT_USER_TURN_OPERATION,
            "tenant_id": str(actor.tenant_id),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()
