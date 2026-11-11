from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest

from enterprise_chat_platform.application.accept_user_turn import (
    ACCEPT_USER_TURN_OPERATION,
    AcceptUserTurnCommand,
    AcceptUserTurnHandler,
    AcceptUserTurnResult,
    ActorContext,
    AgentRunQueuedEvent,
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.application.errors import (
    ConversationNotFoundError,
    IdempotencyConflictError,
)
from enterprise_chat_platform.domain.chat import (
    AcceptedUserTurn,
    Conversation,
    ConversationStatus,
)
from enterprise_chat_platform.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = ConversationId.parse("33333333-3333-4333-8333-333333333333")
MESSAGE_ID = MessageId.parse("44444444-4444-4444-8444-444444444444")
AGENT_RUN_ID = AgentRunId.parse("55555555-5555-4555-8555-555555555555")
EVENT_ID = EventId.parse("66666666-6666-4666-8666-666666666666")
OTHER_ACTOR_ID = UserId.parse("77777777-7777-4777-8777-777777777777")
DEFAULT_CONTENT = "  Explain bounded contexts.\n"
OTHER_TENANT_ID = TenantId.parse("88888888-8888-4888-8888-888888888888")
MISSING_CONVERSATION_ID = ConversationId.parse("99999999-9999-4999-8999-999999999999")

CREATED_AT = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
ACCEPTED_AT = datetime(2026, 11, 9, 15, 1, tzinfo=UTC)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.value


class FixedTurnIds:
    def __init__(self) -> None:
        self.message_calls = 0
        self.agent_run_calls = 0
        self.event_calls = 0

    def new_message_id(self) -> MessageId:
        self.message_calls += 1
        return MESSAGE_ID

    def new_agent_run_id(self) -> AgentRunId:
        self.agent_run_calls += 1
        return AGENT_RUN_ID

    def new_event_id(self) -> EventId:
        self.event_calls += 1
        return EVENT_ID


class StubWriteAuthorizer:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[ActorContext, Conversation]] = []

    async def can_write(
        self,
        *,
        actor: ActorContext,
        conversation: Conversation,
    ) -> bool:
        self.calls.append((actor, conversation))
        return self.allowed


class FakeAcceptUserTurnUnitOfWork:
    def __init__(
        self,
        conversation: Conversation,
        *,
        commit_error: Exception | None = None,
    ) -> None:
        self.conversation = conversation
        self.receipt: IdempotencyReceipt | None = None
        self.commit_error = commit_error

        self.loaded_scope: IdempotencyScope | None = None
        self.staged_expected_version: int | None = None
        self.staged_turn: AcceptedUserTurn | None = None
        self.staged_receipt: IdempotencyReceipt | None = None
        self.staged_event: AgentRunQueuedEvent | None = None

        self.durable_turn: AcceptedUserTurn | None = None
        self.durable_receipt: IdempotencyReceipt | None = None
        self.durable_event: AgentRunQueuedEvent | None = None

        self.commit_count = 0
        self._discard_staged_on_exit = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._discard_staged_on_exit:
            self._clear_staged()
            self._discard_staged_on_exit = False

    async def get_conversation(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        if tenant_id == self.conversation.tenant_id and conversation_id == self.conversation.id:
            return self.conversation
        return None

    async def get_idempotency_receipt(
        self,
        *,
        scope: IdempotencyScope,
    ) -> IdempotencyReceipt | None:
        self.loaded_scope = scope
        return self.receipt

    def stage_accepted_turn(
        self,
        *,
        expected_conversation_version: int,
        accepted_turn: AcceptedUserTurn,
        receipt: IdempotencyReceipt,
        event: AgentRunQueuedEvent,
    ) -> None:
        self.staged_expected_version = expected_conversation_version
        self.staged_turn = accepted_turn
        self.staged_receipt = receipt
        self.staged_event = event

    async def commit(self) -> None:
        self.commit_count += 1

        commit_error = self.commit_error
        if commit_error is not None:
            self._discard_staged_on_exit = True
            raise commit_error

        expected_version = self.staged_expected_version
        staged_turn = self.staged_turn
        staged_receipt = self.staged_receipt
        staged_event = self.staged_event

        if (
            expected_version is None
            or staged_turn is None
            or staged_receipt is None
            or staged_event is None
        ):
            self._discard_staged_on_exit = True
            raise AssertionError("Commit requires a fully staged accepted turn")

        if expected_version != self.conversation.version:
            self._discard_staged_on_exit = True
            raise AssertionError("Conversation version changed before commit")

        self.durable_turn = staged_turn
        self.durable_receipt = staged_receipt
        self.durable_event = staged_event

        self.conversation = staged_turn.updated_conversation
        self.receipt = staged_receipt

    def _clear_staged(self) -> None:
        self.staged_expected_version = None
        self.staged_turn = None
        self.staged_receipt = None
        self.staged_event = None


def _build_handler(
    *,
    write_allowed: bool = True,
    conversation: Conversation | None = None,
    commit_error: Exception | None = None,
) -> tuple[
    AcceptUserTurnHandler,
    FakeAcceptUserTurnUnitOfWork,
    FixedClock,
    FixedTurnIds,
    StubWriteAuthorizer,
]:
    if conversation is None:
        conversation = Conversation.create(
            id=CONVERSATION_ID,
            tenant_id=TENANT_ID,
            created_by=ACTOR_ID,
            at=CREATED_AT,
        )

    clock = FixedClock(ACCEPTED_AT)
    ids = FixedTurnIds()
    authorizer = StubWriteAuthorizer(allowed=write_allowed)
    unit_of_work = FakeAcceptUserTurnUnitOfWork(
        conversation,
        commit_error=commit_error,
    )

    def create_unit_of_work() -> FakeAcceptUserTurnUnitOfWork:
        return unit_of_work

    handler = AcceptUserTurnHandler(
        unit_of_work_factory=create_unit_of_work,
        clock=clock,
        ids=ids,
        authorizer=authorizer,
    )

    return handler, unit_of_work, clock, ids, authorizer


@pytest.mark.asyncio
async def test_accept_user_turn_stages_atomic_write_and_commits_once() -> None:
    conversation = Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=ACTOR_ID,
        at=CREATED_AT,
    )
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content="  Explain bounded contexts.\n",
        idempotency_key=IdempotencyKey("accept-turn-001"),
    )

    clock = FixedClock(ACCEPTED_AT)
    ids = FixedTurnIds()
    authorizer = StubWriteAuthorizer()
    unit_of_work = FakeAcceptUserTurnUnitOfWork(conversation)

    def create_unit_of_work() -> FakeAcceptUserTurnUnitOfWork:
        return unit_of_work

    handler = AcceptUserTurnHandler(
        unit_of_work_factory=create_unit_of_work,
        clock=clock,
        ids=ids,
        authorizer=authorizer,
    )

    result = await handler.handle(
        actor=actor,
        command=command,
    )

    assert result == AcceptUserTurnResult(
        conversation_id=CONVERSATION_ID,
        conversation_version=1,
        message_id=MESSAGE_ID,
        message_sequence=1,
        agent_run_id=AGENT_RUN_ID,
        accepted_at=ACCEPTED_AT,
        replayed=False,
    )

    assert unit_of_work.staged_expected_version == 0
    assert unit_of_work.staged_turn is not None
    assert unit_of_work.staged_turn.message.content == command.content
    assert unit_of_work.staged_turn.updated_conversation.version == 1
    assert unit_of_work.staged_turn.agent_run.id == AGENT_RUN_ID

    assert unit_of_work.staged_event is not None
    assert unit_of_work.staged_event.id == EVENT_ID
    assert unit_of_work.staged_event.event_type == "agent_run.queued.v1"
    assert unit_of_work.staged_event.schema_version == 1
    assert unit_of_work.staged_event.input_message_id == MESSAGE_ID
    assert not hasattr(unit_of_work.staged_event, "content")

    assert unit_of_work.staged_receipt is not None
    assert unit_of_work.staged_receipt.result == result
    assert len(unit_of_work.staged_receipt.request_fingerprint) == 64
    assert unit_of_work.staged_receipt.scope.operation == ACCEPT_USER_TURN_OPERATION
    assert unit_of_work.loaded_scope == unit_of_work.staged_receipt.scope

    assert unit_of_work.commit_count == 1
    assert clock.calls == 1
    assert ids.message_calls == 1
    assert ids.agent_run_calls == 1
    assert ids.event_calls == 1
    assert authorizer.calls == [(actor, conversation)]


@pytest.mark.asyncio
async def test_exact_replay_returns_original_result_without_side_effects() -> None:
    handler, unit_of_work, clock, ids, authorizer = _build_handler()
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content=DEFAULT_CONTENT,
        idempotency_key=IdempotencyKey("accept-turn-replay"),
    )

    first_result = await handler.handle(
        actor=actor,
        command=command,
    )

    assert unit_of_work.staged_receipt is not None

    original_turn = unit_of_work.staged_turn
    original_event = unit_of_work.staged_event
    original_receipt = unit_of_work.staged_receipt

    # Simulate the transaction having made the receipt durable.
    # unit_of_work.receipt = original_receipt

    replayed_result = await handler.handle(
        actor=actor,
        command=command,
    )

    assert replayed_result == replace(first_result, replayed=True)
    assert replayed_result.message_id == first_result.message_id
    assert replayed_result.agent_run_id == first_result.agent_run_id

    assert unit_of_work.staged_turn is original_turn
    assert unit_of_work.staged_event is original_event
    assert unit_of_work.staged_receipt is original_receipt

    assert unit_of_work.commit_count == 1
    assert clock.calls == 1
    assert ids.message_calls == 1
    assert ids.agent_run_calls == 1
    assert ids.event_calls == 1
    assert len(authorizer.calls) == 2


@pytest.mark.parametrize(
    ("second_actor_id", "second_content"),
    [
        (ACTOR_ID, "Different message content"),
        (OTHER_ACTOR_ID, DEFAULT_CONTENT),
    ],
)
@pytest.mark.asyncio
async def test_reused_key_with_different_request_is_rejected(
    second_actor_id: UserId,
    second_content: str,
) -> None:
    handler, unit_of_work, clock, ids, authorizer = _build_handler()
    first_actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    first_command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content=DEFAULT_CONTENT,
        idempotency_key=IdempotencyKey("accept-turn-conflict"),
    )

    await handler.handle(
        actor=first_actor,
        command=first_command,
    )

    assert unit_of_work.staged_receipt is not None

    original_turn = unit_of_work.staged_turn
    original_event = unit_of_work.staged_event
    original_receipt = unit_of_work.staged_receipt

    unit_of_work.receipt = original_receipt

    second_actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=second_actor_id,
    )
    second_command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content=second_content,
        idempotency_key=first_command.idempotency_key,
    )

    with pytest.raises(
        IdempotencyConflictError,
        match="already used",
    ):
        await handler.handle(
            actor=second_actor,
            command=second_command,
        )

    assert unit_of_work.staged_turn is original_turn
    assert unit_of_work.staged_event is original_event
    assert unit_of_work.staged_receipt is original_receipt

    assert unit_of_work.commit_count == 1
    assert clock.calls == 1
    assert ids.message_calls == 1
    assert ids.agent_run_calls == 1
    assert ids.event_calls == 1
    assert len(authorizer.calls) == 2


@pytest.mark.parametrize(
    ("tenant_id", "conversation_id"),
    [
        (OTHER_TENANT_ID, CONVERSATION_ID),
        (TENANT_ID, MISSING_CONVERSATION_ID),
    ],
)
@pytest.mark.asyncio
async def test_unavailable_conversation_is_reported_without_side_effects(
    tenant_id: TenantId,
    conversation_id: ConversationId,
) -> None:
    handler, unit_of_work, clock, ids, authorizer = _build_handler()
    actor = ActorContext(
        tenant_id=tenant_id,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=conversation_id,
        content=DEFAULT_CONTENT,
        idempotency_key=IdempotencyKey("unavailable-conversation"),
    )

    with pytest.raises(
        ConversationNotFoundError,
        match="Conversation was not found",
    ):
        await handler.handle(
            actor=actor,
            command=command,
        )

    assert authorizer.calls == []
    assert unit_of_work.loaded_scope is None
    assert unit_of_work.staged_turn is None
    assert unit_of_work.staged_receipt is None
    assert unit_of_work.staged_event is None
    assert unit_of_work.commit_count == 0
    assert clock.calls == 0
    assert ids.message_calls == 0
    assert ids.agent_run_calls == 0
    assert ids.event_calls == 0


@pytest.mark.asyncio
async def test_unauthorized_actor_receives_not_found_without_side_effects() -> None:
    handler, unit_of_work, clock, ids, authorizer = _build_handler(
        write_allowed=False,
    )
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content=DEFAULT_CONTENT,
        idempotency_key=IdempotencyKey("unauthorized-conversation"),
    )

    with pytest.raises(
        ConversationNotFoundError,
        match="Conversation was not found",
    ):
        await handler.handle(
            actor=actor,
            command=command,
        )

    assert authorizer.calls == [(actor, unit_of_work.conversation)]
    assert unit_of_work.loaded_scope is None
    assert unit_of_work.staged_turn is None
    assert unit_of_work.staged_receipt is None
    assert unit_of_work.staged_event is None
    assert unit_of_work.commit_count == 0
    assert clock.calls == 0
    assert ids.message_calls == 0
    assert ids.agent_run_calls == 0
    assert ids.event_calls == 0


@pytest.mark.asyncio
async def test_archived_conversation_does_not_stage_or_commit() -> None:
    archived_conversation = replace(
        Conversation.create(
            id=CONVERSATION_ID,
            tenant_id=TENANT_ID,
            created_by=ACTOR_ID,
            at=CREATED_AT,
        ),
        status=ConversationStatus.ARCHIVED,
    )
    handler, unit_of_work, _, _, _ = _build_handler(
        conversation=archived_conversation,
    )
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content=DEFAULT_CONTENT,
        idempotency_key=IdempotencyKey("archived-conversation"),
    )

    with pytest.raises(
        InvalidStateTransitionError,
        match="Archived conversation",
    ):
        await handler.handle(
            actor=actor,
            command=command,
        )

    assert unit_of_work.staged_turn is None
    assert unit_of_work.staged_receipt is None
    assert unit_of_work.staged_event is None
    assert unit_of_work.commit_count == 0


@pytest.mark.asyncio
async def test_invalid_content_does_not_stage_or_commit() -> None:
    handler, unit_of_work, _, _, _ = _build_handler()
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content="   ",
        idempotency_key=IdempotencyKey("invalid-content"),
    )

    with pytest.raises(
        DomainValidationError,
        match="cannot be blank",
    ):
        await handler.handle(
            actor=actor,
            command=command,
        )

    assert unit_of_work.staged_turn is None
    assert unit_of_work.staged_receipt is None
    assert unit_of_work.staged_event is None
    assert unit_of_work.commit_count == 0


@pytest.mark.asyncio
async def test_definitive_commit_failure_rolls_back_and_can_be_retried() -> None:
    handler, unit_of_work, _, _, _ = _build_handler(
        commit_error=RuntimeError("database commit failed"),
    )
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )
    command = AcceptUserTurnCommand(
        conversation_id=CONVERSATION_ID,
        content=DEFAULT_CONTENT,
        idempotency_key=IdempotencyKey("retry-after-rollback"),
    )

    with pytest.raises(
        RuntimeError,
        match="database commit failed",
    ):
        await handler.handle(
            actor=actor,
            command=command,
        )

    assert unit_of_work.commit_count == 1
    assert unit_of_work.staged_turn is None
    assert unit_of_work.staged_receipt is None
    assert unit_of_work.staged_event is None

    durable_turn_after_failure = unit_of_work.durable_turn
    durable_receipt_after_failure = unit_of_work.durable_receipt
    durable_event_after_failure = unit_of_work.durable_event

    assert durable_turn_after_failure is None
    assert durable_receipt_after_failure is None
    assert durable_event_after_failure is None

    assert unit_of_work.receipt is None
    assert unit_of_work.conversation.version == 0

    # The simulated database is available again.
    unit_of_work.commit_error = None

    result = await handler.handle(
        actor=actor,
        command=command,
    )

    assert result.replayed is False
    assert unit_of_work.commit_count == 2
    assert unit_of_work.conversation.version == 1

    assert unit_of_work.durable_turn is not None
    assert unit_of_work.durable_turn.message.id == result.message_id
    assert unit_of_work.durable_receipt is not None
    assert unit_of_work.durable_receipt.result == result
    assert unit_of_work.durable_event is not None
    assert unit_of_work.durable_event.agent_run_id == result.agent_run_id
