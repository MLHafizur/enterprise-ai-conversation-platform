from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from enterprise_chat_platform.application.accept_user_turn import (
    AcceptUserTurnResult,
    AgentRunQueuedEvent,
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.application.errors import ConversationConcurrencyError
from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
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
from enterprise_chat_platform.infrastructure.persistence import unit_of_work as uow_module
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.agent_run import (
    SqlAlchemyAgentRunRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.assistant_profile import (
    SqlAlchemyAssistantProfileRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.idempotency_receipt import (
    SqlAlchemyIdempotencyReceiptRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.message import (
    SqlAlchemyMessageRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.outbox_event import (
    SqlAlchemyOutboxEventRepository,
)
from enterprise_chat_platform.infrastructure.persistence.unit_of_work import (
    SqlAlchemyAcceptUserTurnUnitOfWork,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = TenantId.parse("99999999-9999-4999-8999-999999999999")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = ConversationId.parse("33333333-3333-4333-8333-333333333333")
OTHER_CONVERSATION_ID = ConversationId.parse("88888888-8888-4888-8888-888888888888")
MESSAGE_ID = MessageId.parse("44444444-4444-4444-8444-444444444444")
OTHER_MESSAGE_ID = MessageId.parse("77777777-7777-4777-8777-777777777777")
AGENT_RUN_ID = AgentRunId.parse("55555555-5555-4555-8555-555555555555")
OTHER_AGENT_RUN_ID = AgentRunId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
EVENT_ID = EventId.parse("66666666-6666-4666-8666-666666666666")
CREATED_AT = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)
ACCEPTED_AT = CREATED_AT + timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class _TurnBundle:
    expected_version: int
    accepted_turn: AcceptedUserTurn
    receipt: IdempotencyReceipt
    event: AgentRunQueuedEvent


@dataclass(frozen=True, slots=True)
class _Repositories:
    conversation: MagicMock
    receipt: MagicMock
    message: MagicMock
    agent_run: MagicMock
    assistant_profile: MagicMock
    outbox: MagicMock


@dataclass(frozen=True, slots=True)
class _Harness:
    unit_of_work: SqlAlchemyAcceptUserTurnUnitOfWork
    session_factory: MagicMock
    session: MagicMock
    transaction: MagicMock
    repositories: _Repositories
    repository_factories: tuple[MagicMock, ...]


def _turn_bundle() -> _TurnBundle:
    conversation = Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=ACTOR_ID,
        at=CREATED_AT,
    )
    accepted_turn = conversation.accept_user_turn(
        trusted_tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        message_id=MESSAGE_ID,
        agent_run_id=AGENT_RUN_ID,
        assistant_profile_ref=default_assistant_profile(
            tenant_id=TENANT_ID,
            created_at=ACCEPTED_AT,
        ).ref,
        content="Explain transaction boundaries.",
        idempotency_key=IdempotencyKey("turn-uow-test"),
        at=ACCEPTED_AT,
    )
    result = AcceptUserTurnResult(
        conversation_id=CONVERSATION_ID,
        conversation_version=accepted_turn.updated_conversation.version,
        message_id=MESSAGE_ID,
        message_sequence=accepted_turn.message.sequence,
        agent_run_id=AGENT_RUN_ID,
        accepted_at=ACCEPTED_AT,
        replayed=False,
    )
    receipt = IdempotencyReceipt(
        scope=IdempotencyScope(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
            key=accepted_turn.message.idempotency_key,
        ),
        request_fingerprint="a" * 64,
        result=result,
    )
    event = AgentRunQueuedEvent(
        id=EVENT_ID,
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        agent_run_id=AGENT_RUN_ID,
        input_message_id=MESSAGE_ID,
        conversation_version=accepted_turn.updated_conversation.version,
        occurred_at=ACCEPTED_AT,
    )
    return _TurnBundle(
        expected_version=conversation.version,
        accepted_turn=accepted_turn,
        receipt=receipt,
        event=event,
    )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    session = MagicMock(spec=AsyncSession)
    transaction = MagicMock(spec=AsyncSessionTransaction)
    transaction.is_active = True
    session.begin.return_value = transaction
    session_factory = MagicMock(return_value=session)

    repositories = _Repositories(
        conversation=MagicMock(spec=SqlAlchemyConversationRepository),
        receipt=MagicMock(spec=SqlAlchemyIdempotencyReceiptRepository),
        message=MagicMock(spec=SqlAlchemyMessageRepository),
        agent_run=MagicMock(spec=SqlAlchemyAgentRunRepository),
        assistant_profile=MagicMock(spec=SqlAlchemyAssistantProfileRepository),
        outbox=MagicMock(spec=SqlAlchemyOutboxEventRepository),
    )
    repositories.conversation.update_if_version_matches.return_value = True

    conversation_factory = MagicMock(return_value=repositories.conversation)
    receipt_factory = MagicMock(return_value=repositories.receipt)
    message_factory = MagicMock(return_value=repositories.message)
    agent_run_factory = MagicMock(return_value=repositories.agent_run)
    assistant_profile_factory = MagicMock(return_value=repositories.assistant_profile)
    outbox_factory = MagicMock(return_value=repositories.outbox)

    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyConversationRepository",
        conversation_factory,
    )
    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyIdempotencyReceiptRepository",
        receipt_factory,
    )
    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyMessageRepository",
        message_factory,
    )
    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyAgentRunRepository",
        agent_run_factory,
    )
    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyAssistantProfileRepository",
        assistant_profile_factory,
    )
    monkeypatch.setattr(
        uow_module,
        "SqlAlchemyOutboxEventRepository",
        outbox_factory,
    )

    typed_factory = cast(AsyncSessionFactory, session_factory)
    return _Harness(
        unit_of_work=SqlAlchemyAcceptUserTurnUnitOfWork(typed_factory),
        session_factory=session_factory,
        session=session,
        transaction=transaction,
        repositories=repositories,
        repository_factories=(
            conversation_factory,
            receipt_factory,
            message_factory,
            agent_run_factory,
            assistant_profile_factory,
            outbox_factory,
        ),
    )


@pytest.mark.asyncio
async def test_context_owns_one_session_transaction_and_repository_set(
    harness: _Harness,
) -> None:
    async with harness.unit_of_work as entered:
        assert entered is harness.unit_of_work
        harness.session_factory.assert_called_once_with()
        harness.session.begin.assert_called_once_with()
        harness.transaction.start.assert_awaited_once_with()

        for repository_factory in harness.repository_factories:
            repository_factory.assert_called_once_with(harness.session)

    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_conversation_delegates_inside_owned_transaction(
    harness: _Harness,
) -> None:
    conversation = _turn_bundle().accepted_turn.updated_conversation
    harness.repositories.conversation.get.return_value = conversation

    async with harness.unit_of_work:
        loaded = await harness.unit_of_work.get_conversation(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
        )

    assert loaded is conversation
    harness.repositories.conversation.get.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
    )


@pytest.mark.asyncio
async def test_receipt_lookup_acquires_scope_lock_before_read(
    harness: _Harness,
) -> None:
    bundle = _turn_bundle()
    order = MagicMock()
    order.attach_mock(harness.repositories.receipt.acquire_scope_lock, "lock")
    order.attach_mock(harness.repositories.receipt.get, "get")
    harness.repositories.receipt.get.return_value = bundle.receipt

    async with harness.unit_of_work:
        loaded = await harness.unit_of_work.get_idempotency_receipt(
            scope=bundle.receipt.scope,
        )

    assert loaded is bundle.receipt
    assert order.mock_calls == [
        call.lock(scope=bundle.receipt.scope),
        call.get(scope=bundle.receipt.scope),
    ]


@pytest.mark.asyncio
async def test_active_assistant_profile_lookup_delegates_in_owned_transaction(
    harness: _Harness,
) -> None:
    profile = default_assistant_profile(
        tenant_id=TENANT_ID,
        created_at=ACCEPTED_AT,
    )
    harness.repositories.assistant_profile.get_or_create_active.return_value = profile

    async with harness.unit_of_work:
        loaded = await harness.unit_of_work.get_or_create_active_assistant_profile(
            tenant_id=TENANT_ID,
            default_profile=profile,
        )

    assert loaded is profile
    harness.repositories.assistant_profile.get_or_create_active.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        default_profile=profile,
    )


@pytest.mark.asyncio
async def test_stage_and_commit_cas_flushes_and_commits_exactly_once(
    harness: _Harness,
) -> None:
    bundle = _turn_bundle()
    commit_order = MagicMock()
    commit_order.attach_mock(
        harness.repositories.conversation.update_if_version_matches,
        "conversation_cas",
    )
    commit_order.attach_mock(harness.session.flush, "flush")
    commit_order.attach_mock(harness.transaction.commit, "commit")

    async with harness.unit_of_work:
        harness.unit_of_work.stage_accepted_turn(
            expected_conversation_version=bundle.expected_version,
            accepted_turn=bundle.accepted_turn,
            receipt=bundle.receipt,
            event=bundle.event,
        )
        await harness.unit_of_work.commit()

    harness.repositories.message.add.assert_called_once_with(message=bundle.accepted_turn.message)
    harness.repositories.agent_run.add.assert_called_once_with(
        agent_run=bundle.accepted_turn.agent_run
    )
    harness.repositories.receipt.add.assert_called_once_with(receipt=bundle.receipt)
    harness.repositories.outbox.add.assert_called_once_with(event=bundle.event)
    assert commit_order.mock_calls == [
        call.conversation_cas(
            expected_version=bundle.expected_version,
            updated_conversation=bundle.accepted_turn.updated_conversation,
        ),
        call.flush(),
        call.flush(),
        call.flush(),
        call.commit(),
    ]
    harness.transaction.rollback.assert_not_awaited()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_normal_uncommitted_exit_rolls_back_and_closes(
    harness: _Harness,
) -> None:
    async with harness.unit_of_work:
        pass

    harness.transaction.rollback.assert_awaited_once_with()
    harness.transaction.commit.assert_not_awaited()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_exception_exit_rolls_back_closes_and_propagates(
    harness: _Harness,
) -> None:
    with pytest.raises(RuntimeError, match="application failed"):
        async with harness.unit_of_work:
            raise RuntimeError("application failed")

    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_conversation_cas_conflict_rolls_back_without_flush_or_commit(
    harness: _Harness,
) -> None:
    bundle = _turn_bundle()
    harness.repositories.conversation.update_if_version_matches.return_value = False

    with pytest.raises(ConversationConcurrencyError, match="Conversation changed"):
        async with harness.unit_of_work:
            harness.unit_of_work.stage_accepted_turn(
                expected_conversation_version=bundle.expected_version,
                accepted_turn=bundle.accepted_turn,
                receipt=bundle.receipt,
                event=bundle.event,
            )
            await harness.unit_of_work.commit()

    harness.session.flush.assert_not_awaited()
    harness.transaction.commit.assert_not_awaited()
    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_flush_failure_rolls_back_and_never_commits(
    harness: _Harness,
) -> None:
    bundle = _turn_bundle()
    harness.session.flush.side_effect = RuntimeError("flush failed")

    with pytest.raises(RuntimeError, match="flush failed"):
        async with harness.unit_of_work:
            harness.unit_of_work.stage_accepted_turn(
                expected_conversation_version=bundle.expected_version,
                accepted_turn=bundle.accepted_turn,
                receipt=bundle.receipt,
                event=bundle.event,
            )
            await harness.unit_of_work.commit()

    harness.transaction.commit.assert_not_awaited()
    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_commit_failure_attempts_rollback_and_always_closes(
    harness: _Harness,
) -> None:
    bundle = _turn_bundle()
    harness.transaction.commit.side_effect = RuntimeError("commit outcome unknown")

    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        async with harness.unit_of_work:
            harness.unit_of_work.stage_accepted_turn(
                expected_conversation_version=bundle.expected_version,
                accepted_turn=bundle.accepted_turn,
                receipt=bundle.receipt,
                event=bundle.event,
            )
            await harness.unit_of_work.commit()

    assert harness.session.flush.await_args_list == [
        call(),
        call(),
        call(),
    ]
    harness.transaction.commit.assert_awaited_once_with()
    harness.transaction.rollback.assert_awaited_once_with()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_transaction_start_failure_always_closes_session(
    harness: _Harness,
) -> None:
    harness.transaction.is_active = False
    harness.transaction.start.side_effect = RuntimeError("begin failed")

    with pytest.raises(RuntimeError, match="begin failed"):
        async with harness.unit_of_work:
            pytest.fail("context body must not run")

    harness.transaction.rollback.assert_not_awaited()
    harness.session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_commit_requires_a_staged_turn(harness: _Harness) -> None:
    async with harness.unit_of_work:
        with pytest.raises(RuntimeError, match="staged before commit"):
            await harness.unit_of_work.commit()

    harness.repositories.conversation.update_if_version_matches.assert_not_awaited()
    harness.session.flush.assert_not_awaited()
    harness.transaction.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unit_of_work_cannot_stage_two_turns(harness: _Harness) -> None:
    bundle = _turn_bundle()

    async with harness.unit_of_work:
        harness.unit_of_work.stage_accepted_turn(
            expected_conversation_version=bundle.expected_version,
            accepted_turn=bundle.accepted_turn,
            receipt=bundle.receipt,
            event=bundle.event,
        )

        with pytest.raises(RuntimeError, match="Only one accepted turn"):
            harness.unit_of_work.stage_accepted_turn(
                expected_conversation_version=bundle.expected_version,
                accepted_turn=bundle.accepted_turn,
                receipt=bundle.receipt,
                event=bundle.event,
            )

    harness.repositories.message.add.assert_not_called()
    harness.repositories.agent_run.add.assert_not_called()
    harness.repositories.receipt.add.assert_not_called()
    harness.repositories.outbox.add.assert_not_called()


@pytest.mark.asyncio
async def test_operations_are_rejected_outside_context(harness: _Harness) -> None:
    bundle = _turn_bundle()

    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.get_conversation(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
        )
    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.get_idempotency_receipt(scope=bundle.receipt.scope)
    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.get_or_create_active_assistant_profile(
            tenant_id=TENANT_ID,
            default_profile=default_assistant_profile(
                tenant_id=TENANT_ID,
                created_at=ACCEPTED_AT,
            ),
        )
    with pytest.raises(RuntimeError, match="active transaction"):
        harness.unit_of_work.stage_accepted_turn(
            expected_conversation_version=bundle.expected_version,
            accepted_turn=bundle.accepted_turn,
            receipt=bundle.receipt,
            event=bundle.event,
        )
    with pytest.raises(RuntimeError, match="active transaction"):
        await harness.unit_of_work.commit()

    harness.session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_unit_of_work_instance_cannot_be_reentered(harness: _Harness) -> None:
    async with harness.unit_of_work:
        pass

    with pytest.raises(RuntimeError, match="cannot be entered more than once"):
        async with harness.unit_of_work:
            pytest.fail("context body must not run")

    harness.session_factory.assert_called_once_with()


def _make_incoherent_bundle(
    bundle: _TurnBundle,
    case: str,
) -> _TurnBundle:
    accepted_turn = bundle.accepted_turn
    receipt = bundle.receipt
    event = bundle.event
    expected_version = bundle.expected_version

    if case == "negative expected version":
        expected_version = -1
    elif case == "version did not advance once":
        expected_version = accepted_turn.updated_conversation.version
    elif case == "run is not queued":
        accepted_turn = replace(
            accepted_turn,
            agent_run=accepted_turn.agent_run.start(at=ACCEPTED_AT),
        )
    elif case == "entity timestamp differs":
        accepted_turn = replace(
            accepted_turn,
            agent_run=replace(
                accepted_turn.agent_run,
                created_at=ACCEPTED_AT + timedelta(seconds=1),
            ),
        )
    elif case == "receipt tenant differs":
        receipt = replace(
            receipt,
            scope=replace(receipt.scope, tenant_id=OTHER_TENANT_ID),
        )
    elif case == "receipt conversation differs":
        receipt = replace(
            receipt,
            scope=replace(receipt.scope, conversation_id=OTHER_CONVERSATION_ID),
        )
    elif case == "receipt key differs":
        receipt = replace(
            receipt,
            scope=replace(receipt.scope, key=IdempotencyKey("different-key")),
        )
    elif case == "receipt is replayed":
        receipt = replace(
            receipt,
            result=replace(receipt.result, replayed=True),
        )
    elif case == "result conversation differs":
        receipt = replace(
            receipt,
            result=replace(receipt.result, conversation_id=OTHER_CONVERSATION_ID),
        )
    elif case == "result version differs":
        receipt = replace(
            receipt,
            result=replace(receipt.result, conversation_version=2),
        )
    elif case == "result message differs":
        receipt = replace(
            receipt,
            result=replace(receipt.result, message_id=OTHER_MESSAGE_ID),
        )
    elif case == "result sequence differs":
        receipt = replace(
            receipt,
            result=replace(receipt.result, message_sequence=2),
        )
    elif case == "result run differs":
        receipt = replace(
            receipt,
            result=replace(receipt.result, agent_run_id=OTHER_AGENT_RUN_ID),
        )
    elif case == "result timestamp differs":
        receipt = replace(
            receipt,
            result=replace(
                receipt.result,
                accepted_at=ACCEPTED_AT + timedelta(seconds=1),
            ),
        )
    elif case == "event tenant differs":
        event = replace(event, tenant_id=OTHER_TENANT_ID)
    elif case == "event conversation differs":
        event = replace(event, conversation_id=OTHER_CONVERSATION_ID)
    elif case == "event run differs":
        event = replace(event, agent_run_id=OTHER_AGENT_RUN_ID)
    elif case == "event message differs":
        event = replace(event, input_message_id=OTHER_MESSAGE_ID)
    elif case == "event version differs":
        event = replace(event, conversation_version=2)
    elif case == "event timestamp differs":
        event = replace(event, occurred_at=ACCEPTED_AT + timedelta(seconds=1))
    else:
        raise AssertionError(f"Unknown test case: {case}")

    return _TurnBundle(
        expected_version=expected_version,
        accepted_turn=accepted_turn,
        receipt=receipt,
        event=event,
    )


@pytest.mark.parametrize(
    "case",
    [
        "negative expected version",
        "version did not advance once",
        "run is not queued",
        "entity timestamp differs",
        "receipt tenant differs",
        "receipt conversation differs",
        "receipt key differs",
        "receipt is replayed",
        "result conversation differs",
        "result version differs",
        "result message differs",
        "result sequence differs",
        "result run differs",
        "result timestamp differs",
        "event tenant differs",
        "event conversation differs",
        "event run differs",
        "event message differs",
        "event version differs",
        "event timestamp differs",
    ],
)
@pytest.mark.asyncio
async def test_cross_object_mismatch_is_rejected_before_any_record_is_staged(
    case: str,
    harness: _Harness,
) -> None:
    bundle = _make_incoherent_bundle(_turn_bundle(), case)

    async with harness.unit_of_work:
        with pytest.raises(ValueError):
            harness.unit_of_work.stage_accepted_turn(
                expected_conversation_version=bundle.expected_version,
                accepted_turn=bundle.accepted_turn,
                receipt=bundle.receipt,
                event=bundle.event,
            )

    harness.repositories.message.add.assert_not_called()
    harness.repositories.agent_run.add.assert_not_called()
    harness.repositories.receipt.add.assert_not_called()
    harness.repositories.outbox.add.assert_not_called()
