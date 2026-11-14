from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest

from enterprise_chat_platform.application.create_conversation import (
    ActorContext,
    CreateConversationCommand,
    CreateConversationHandler,
)
from enterprise_chat_platform.application.errors import (
    ConversationNotFoundError,
)
from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
OTHER_ACTOR_ID = UserId.parse("77777777-7777-4777-8777-777777777777")
OTHER_TENANT_ID = TenantId.parse("88888888-8888-4888-8888-888888888888")
CONVERSATION_ID = ConversationId.parse("33333333-3333-4333-8333-333333333333")
OTHER_CONVERSATION_ID = ConversationId.parse("99999999-9999-4999-8999-999999999999")
REQUEST_TIME = datetime(2026, 11, 13, 16, 0, tzinfo=UTC)
ORIGINAL_TIME = datetime(2026, 11, 13, 15, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.value


@dataclass
class FakeCreateConversationUnitOfWork:
    insert_result: bool
    existing: Conversation | None = None
    insert_error: BaseException | None = None
    commit_error: BaseException | None = None

    def __post_init__(self) -> None:
        self.entered = False
        self.exited = False
        self.exit_exception_type: type[BaseException] | None = None
        self.inserted_candidate: Conversation | None = None
        self.get_arguments: tuple[TenantId, ConversationId] | None = None
        self.commit_calls = 0

    async def __aenter__(self) -> Self:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc, traceback
        self.exited = True
        self.exit_exception_type = exc_type

    async def insert_if_absent(
        self,
        *,
        conversation: Conversation,
    ) -> bool:
        self.inserted_candidate = conversation
        if self.insert_error is not None:
            raise self.insert_error
        return self.insert_result

    async def get_conversation(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        self.get_arguments = (
            tenant_id,
            conversation_id,
        )
        return self.existing

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )


def _command() -> CreateConversationCommand:
    return CreateConversationCommand(
        conversation_id=CONVERSATION_ID,
    )


def _existing(
    *,
    tenant_id: TenantId = TENANT_ID,
    conversation_id: ConversationId = CONVERSATION_ID,
    owner_id: UserId = ACTOR_ID,
) -> Conversation:
    return Conversation.create(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=owner_id,
        at=ORIGINAL_TIME,
    )


@pytest.mark.asyncio
async def test_first_request_creates_client_selected_conversation_and_commits() -> None:
    unit_of_work = FakeCreateConversationUnitOfWork(insert_result=True)
    clock = FixedClock(REQUEST_TIME)
    handler = CreateConversationHandler(
        unit_of_work_factory=lambda: unit_of_work,
        clock=clock,
    )

    result = await handler.handle(
        actor=_actor(),
        command=_command(),
    )

    assert result.conversation_id == CONVERSATION_ID
    assert result.created_at == REQUEST_TIME
    assert result.replayed is False
    assert clock.calls == 1
    assert unit_of_work.entered is True
    assert unit_of_work.exited is True
    assert unit_of_work.exit_exception_type is None
    assert unit_of_work.commit_calls == 1
    assert unit_of_work.get_arguments is None
    assert unit_of_work.inserted_candidate == Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=ACTOR_ID,
        at=REQUEST_TIME,
    )


@pytest.mark.asyncio
async def test_same_owner_replay_returns_original_creation_result() -> None:
    original = _existing()
    unit_of_work = FakeCreateConversationUnitOfWork(
        insert_result=False,
        existing=original,
    )
    handler = CreateConversationHandler(
        unit_of_work_factory=lambda: unit_of_work,
        clock=FixedClock(REQUEST_TIME),
    )

    result = await handler.handle(
        actor=_actor(),
        command=_command(),
    )

    assert result.conversation_id == CONVERSATION_ID
    assert result.created_at == ORIGINAL_TIME
    assert result.replayed is True
    assert unit_of_work.get_arguments == (
        TENANT_ID,
        CONVERSATION_ID,
    )
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.exit_exception_type is None


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(None, id="unavailable"),
        pytest.param(_existing(owner_id=OTHER_ACTOR_ID), id="different-owner"),
        pytest.param(_existing(tenant_id=OTHER_TENANT_ID), id="unexpected-tenant"),
        pytest.param(
            _existing(conversation_id=OTHER_CONVERSATION_ID),
            id="unexpected-conversation",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unsafe_conflicts_are_indistinguishable_not_found(
    existing: Conversation | None,
) -> None:
    unit_of_work = FakeCreateConversationUnitOfWork(
        insert_result=False,
        existing=existing,
    )
    handler = CreateConversationHandler(
        unit_of_work_factory=lambda: unit_of_work,
        clock=FixedClock(REQUEST_TIME),
    )

    with pytest.raises(
        ConversationNotFoundError,
        match="^Conversation was not found$",
    ):
        await handler.handle(
            actor=_actor(),
            command=_command(),
        )

    assert unit_of_work.commit_calls == 0
    assert unit_of_work.exit_exception_type is ConversationNotFoundError


@pytest.mark.asyncio
async def test_insert_failure_propagates_without_lookup_or_commit() -> None:
    unit_of_work = FakeCreateConversationUnitOfWork(
        insert_result=False,
        insert_error=RuntimeError("database unavailable"),
    )
    handler = CreateConversationHandler(
        unit_of_work_factory=lambda: unit_of_work,
        clock=FixedClock(REQUEST_TIME),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await handler.handle(
            actor=_actor(),
            command=_command(),
        )

    assert unit_of_work.get_arguments is None
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.exit_exception_type is RuntimeError


@pytest.mark.asyncio
async def test_commit_failure_propagates() -> None:
    unit_of_work = FakeCreateConversationUnitOfWork(
        insert_result=True,
        commit_error=RuntimeError("commit failed"),
    )
    handler = CreateConversationHandler(
        unit_of_work_factory=lambda: unit_of_work,
        clock=FixedClock(REQUEST_TIME),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await handler.handle(
            actor=_actor(),
            command=_command(),
        )

    assert unit_of_work.commit_calls == 1
    assert unit_of_work.exit_exception_type is RuntimeError


@pytest.mark.asyncio
async def test_invalid_clock_value_fails_before_opening_transaction() -> None:
    unit_of_work = FakeCreateConversationUnitOfWork(insert_result=True)
    handler = CreateConversationHandler(
        unit_of_work_factory=lambda: unit_of_work,
        clock=FixedClock(datetime(2026, 11, 13, 16, 0)),
    )

    with pytest.raises(
        DomainValidationError,
        match="created_at must be timezone-aware",
    ):
        await handler.handle(
            actor=_actor(),
            command=_command(),
        )

    assert unit_of_work.entered is False
    assert unit_of_work.inserted_candidate is None
