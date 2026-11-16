from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.application.accept_user_turn import (
    AcceptUserTurnCommand,
    AcceptUserTurnHandler,
    AcceptUserTurnResult,
    ActorContext,
    Clock,
    ConversationWriteAuthorizer,
    TurnIds,
)
from enterprise_chat_platform.application.errors import (
    ConversationConcurrencyError,
    ConversationNotFoundError,
    IdempotencyConflictError,
)
from enterprise_chat_platform.application.tenant_catalog import (
    default_assistant_profile,
)
from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.assistant_profile import (
    assistant_profile_to_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    AssistantProfileVersionRecord,
    ConversationRecord,
    IdempotencyReceiptRecord,
    MessageRecord,
    OutboxEventRecord,
    TenantAssistantProfileAssignmentRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    OUTBOX_AGGREGATE_TYPE,
)
from enterprise_chat_platform.infrastructure.persistence.unit_of_work import (
    SqlAlchemyAcceptUserTurnUnitOfWork,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import (
    OwnerOnlyConversationWriteAuthorizer,
    UuidTurnIds,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CREATED_AT = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)
ACCEPTED_AT = datetime(2026, 11, 13, 14, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True, slots=True)
class _FixedTurnIds:
    message_id: MessageId
    agent_run_id: AgentRunId
    event_id: EventId

    def new_message_id(self) -> MessageId:
        return self.message_id

    def new_agent_run_id(self) -> AgentRunId:
        return self.agent_run_id

    def new_event_id(self) -> EventId:
        return self.event_id


@dataclass(frozen=True, slots=True)
class _BarrierOwnerAuthorizer:
    barrier: asyncio.Barrier

    async def can_write(
        self,
        *,
        actor: ActorContext,
        conversation: Conversation,
    ) -> bool:
        allowed = (
            actor.tenant_id == conversation.tenant_id and actor.actor_id == conversation.created_by
        )
        await self.barrier.wait()
        return allowed


@dataclass(frozen=True, slots=True)
class _DatabaseCase:
    engine: AsyncEngine
    session_factory: AsyncSessionFactory
    conversation: Conversation
    actor: ActorContext


@dataclass(frozen=True, slots=True)
class _StoredGraph:
    conversation: ConversationRecord
    messages: tuple[MessageRecord, ...]
    agent_runs: tuple[AgentRunRecord, ...]
    receipts: tuple[IdempotencyReceiptRecord, ...]
    outbox_events: tuple[OutboxEventRecord, ...]


type _AttemptOutcome = AcceptUserTurnResult | Exception


def _create_local_database_resources() -> tuple[AsyncEngine, AsyncSessionFactory]:
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    engine = create_database_engine(settings)
    return engine, create_session_factory(engine)


def _create_local_migration_engine() -> AsyncEngine:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())

    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
    assert (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    ) == (
        runtime_url.host,
        runtime_url.port,
        runtime_url.database,
    )

    return create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )


@asynccontextmanager
async def _database_case(
    *,
    sentinel_event_id: EventId | None = None,
) -> AsyncIterator[_DatabaseCase]:
    engine, session_factory = _create_local_database_resources()
    tenant_id = TenantId.new()
    actor_id = UserId.new()
    conversation = Conversation.create(
        id=ConversationId.new(),
        tenant_id=tenant_id,
        created_by=actor_id,
        at=CREATED_AT,
    )

    try:
        async with session_factory() as session, session.begin():
            session.add(conversation_to_new_record(conversation))

            if sentinel_event_id is not None:
                session.add(
                    OutboxEventRecord(
                        tenant_id=tenant_id.value,
                        id=sentinel_event_id.value,
                        aggregate_type=OUTBOX_AGGREGATE_TYPE,
                        aggregate_id=conversation.id.value,
                        aggregate_version=99,
                        event_type="test.constraint-sentinel.v1",
                        schema_version=1,
                        payload={},
                        occurred_at=CREATED_AT,
                        available_at=CREATED_AT,
                        attempt_count=0,
                        last_attempt_at=None,
                        lease_token=None,
                        lease_expires_at=None,
                        published_at=None,
                    )
                )

        yield _DatabaseCase(
            engine=engine,
            session_factory=session_factory,
            conversation=conversation,
            actor=ActorContext(
                tenant_id=tenant_id,
                actor_id=actor_id,
            ),
        )
    finally:
        cleanup_engine = _create_local_migration_engine()
        try:
            async with cleanup_engine.begin() as cleanup_connection:
                await cleanup_connection.execute(
                    delete(OutboxEventRecord).where(
                        OutboxEventRecord.tenant_id == tenant_id.value,
                        OutboxEventRecord.aggregate_id == conversation.id.value,
                    )
                )
                await cleanup_connection.execute(
                    delete(IdempotencyReceiptRecord).where(
                        IdempotencyReceiptRecord.tenant_id == tenant_id.value,
                        IdempotencyReceiptRecord.conversation_id == conversation.id.value,
                    )
                )
                await cleanup_connection.execute(
                    delete(AgentRunRecord).where(
                        AgentRunRecord.tenant_id == tenant_id.value,
                        AgentRunRecord.conversation_id == conversation.id.value,
                    )
                )
                await cleanup_connection.execute(
                    delete(MessageRecord).where(
                        MessageRecord.tenant_id == tenant_id.value,
                        MessageRecord.conversation_id == conversation.id.value,
                    )
                )
                await cleanup_connection.execute(
                    delete(ConversationRecord).where(
                        ConversationRecord.tenant_id == tenant_id.value,
                        ConversationRecord.id == conversation.id.value,
                    )
                )
                await cleanup_connection.execute(
                    delete(TenantAssistantProfileAssignmentRecord).where(
                        TenantAssistantProfileAssignmentRecord.tenant_id == tenant_id.value,
                    )
                )
                await cleanup_connection.execute(
                    delete(AssistantProfileVersionRecord).where(
                        AssistantProfileVersionRecord.tenant_id == tenant_id.value,
                    )
                )
        finally:
            try:
                await cleanup_engine.dispose()
            finally:
                await engine.dispose()


def _handler(
    case: _DatabaseCase,
    *,
    authorizer: ConversationWriteAuthorizer | None = None,
    ids: TurnIds | None = None,
    clock: Clock | None = None,
) -> AcceptUserTurnHandler:
    def unit_of_work_factory() -> SqlAlchemyAcceptUserTurnUnitOfWork:
        return SqlAlchemyAcceptUserTurnUnitOfWork(case.session_factory)

    return AcceptUserTurnHandler(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock or _FixedClock(ACCEPTED_AT),
        ids=ids or UuidTurnIds(),
        authorizer=authorizer or OwnerOnlyConversationWriteAuthorizer(),
    )


def _command(
    case: _DatabaseCase,
    *,
    key: str = "accept-turn-integration",
    content: str = "Explain atomic database transactions.",
    conversation_id: ConversationId | None = None,
) -> AcceptUserTurnCommand:
    return AcceptUserTurnCommand(
        conversation_id=conversation_id or case.conversation.id,
        content=content,
        idempotency_key=IdempotencyKey(key),
    )


async def _load_graph(case: _DatabaseCase) -> _StoredGraph:
    tenant_id = case.conversation.tenant_id.value
    conversation_id = case.conversation.id.value

    async with case.session_factory() as session:
        conversation = (
            await session.scalars(
                select(ConversationRecord).where(
                    ConversationRecord.tenant_id == tenant_id,
                    ConversationRecord.id == conversation_id,
                )
            )
        ).one()
        messages = tuple(
            (
                await session.scalars(
                    select(MessageRecord)
                    .where(
                        MessageRecord.tenant_id == tenant_id,
                        MessageRecord.conversation_id == conversation_id,
                    )
                    .order_by(MessageRecord.sequence)
                )
            ).all()
        )
        agent_runs = tuple(
            (
                await session.scalars(
                    select(AgentRunRecord).where(
                        AgentRunRecord.tenant_id == tenant_id,
                        AgentRunRecord.conversation_id == conversation_id,
                    )
                )
            ).all()
        )
        receipts = tuple(
            (
                await session.scalars(
                    select(IdempotencyReceiptRecord).where(
                        IdempotencyReceiptRecord.tenant_id == tenant_id,
                        IdempotencyReceiptRecord.conversation_id == conversation_id,
                    )
                )
            ).all()
        )
        outbox_events = tuple(
            (
                await session.scalars(
                    select(OutboxEventRecord).where(
                        OutboxEventRecord.tenant_id == tenant_id,
                        OutboxEventRecord.aggregate_id == conversation_id,
                    )
                )
            ).all()
        )

    return _StoredGraph(
        conversation=conversation,
        messages=messages,
        agent_runs=agent_runs,
        receipts=receipts,
        outbox_events=outbox_events,
    )


async def _load_profile_state(
    case: _DatabaseCase,
) -> tuple[
    tuple[AssistantProfileVersionRecord, ...],
    TenantAssistantProfileAssignmentRecord | None,
]:
    tenant_id = case.conversation.tenant_id.value

    async with case.session_factory() as session:
        profiles = tuple(
            (
                await session.scalars(
                    select(AssistantProfileVersionRecord)
                    .where(AssistantProfileVersionRecord.tenant_id == tenant_id)
                    .order_by(
                        AssistantProfileVersionRecord.profile_id,
                        AssistantProfileVersionRecord.version,
                    )
                )
            ).all()
        )
        assignment = await session.scalar(
            select(TenantAssistantProfileAssignmentRecord).where(
                TenantAssistantProfileAssignmentRecord.tenant_id == tenant_id,
            )
        )

    return profiles, assignment


async def _activate_profile_v2(case: _DatabaseCase) -> None:
    profile_v1 = default_assistant_profile(
        tenant_id=case.conversation.tenant_id,
        created_at=ACCEPTED_AT,
    )
    profile_v2 = replace(
        profile_v1,
        ref=replace(profile_v1.ref, version=2),
        assistant_name="Enterprise Assistant V2",
    )

    async with case.session_factory() as session, session.begin():
        session.add(assistant_profile_to_record(profile_v2))
        await session.flush()
        await session.execute(
            update(TenantAssistantProfileAssignmentRecord)
            .where(
                TenantAssistantProfileAssignmentRecord.tenant_id
                == case.conversation.tenant_id.value,
                TenantAssistantProfileAssignmentRecord.profile_id == profile_v1.ref.id.value,
                TenantAssistantProfileAssignmentRecord.profile_version == 1,
            )
            .values(
                profile_version=2,
                revision=TenantAssistantProfileAssignmentRecord.revision + 1,
                activated_at=ACCEPTED_AT,
                activated_by=profile_v2.created_by.value,
            )
        )


def _assert_single_turn_graph(
    graph: _StoredGraph,
    *,
    result: AcceptUserTurnResult,
    expected_content: str,
    expected_key: IdempotencyKey,
) -> None:
    assert graph.conversation.version == 1
    assert graph.conversation.last_message_sequence == 1
    assert graph.conversation.updated_at == result.accepted_at

    assert len(graph.messages) == 1
    message = graph.messages[0]
    assert message.id == result.message_id.value
    assert message.tenant_id == graph.conversation.tenant_id
    assert message.conversation_id == result.conversation_id.value
    assert message.author_id == graph.conversation.created_by
    assert message.role == "user"
    assert message.content == expected_content
    assert message.sequence == result.message_sequence == 1
    assert message.idempotency_key == expected_key.value
    assert message.created_at == result.accepted_at

    assert len(graph.agent_runs) == 1
    agent_run = graph.agent_runs[0]
    assert agent_run.id == result.agent_run_id.value
    assert agent_run.tenant_id == graph.conversation.tenant_id
    assert agent_run.conversation_id == result.conversation_id.value
    assert agent_run.input_message_id == result.message_id.value
    assert agent_run.status == "queued"
    assert agent_run.created_at == result.accepted_at
    assert agent_run.started_at is None
    assert agent_run.finished_at is None
    assert agent_run.output_message_id is None
    assert agent_run.failure_code is None
    assert agent_run.failure_safe_message is None

    assert len(graph.receipts) == 1
    receipt = graph.receipts[0]
    assert receipt.tenant_id == graph.conversation.tenant_id
    assert receipt.conversation_id == result.conversation_id.value
    assert receipt.operation == "accept_user_turn.v1"
    assert receipt.idempotency_key == expected_key.value
    assert receipt.conversation_version == result.conversation_version == 1
    assert receipt.message_id == result.message_id.value
    assert receipt.message_sequence == result.message_sequence
    assert receipt.agent_run_id == result.agent_run_id.value
    assert receipt.accepted_at == result.accepted_at

    assert len(graph.outbox_events) == 1
    event = graph.outbox_events[0]
    assert event.tenant_id == graph.conversation.tenant_id
    assert event.aggregate_type == OUTBOX_AGGREGATE_TYPE
    assert event.aggregate_id == result.conversation_id.value
    assert event.aggregate_version == result.conversation_version
    assert event.event_type == "agent_run.queued.v1"
    assert event.schema_version == 1
    assert event.payload == {
        "agent_run_id": str(result.agent_run_id),
        "input_message_id": str(result.message_id),
    }
    assert event.occurred_at == result.accepted_at
    assert event.available_at == result.accepted_at
    assert event.attempt_count == 0
    assert event.last_attempt_at is None
    assert event.lease_token is None
    assert event.lease_expires_at is None
    assert event.published_at is None


def _assert_no_turn_writes(graph: _StoredGraph) -> None:
    assert graph.conversation.version == 0
    assert graph.conversation.last_message_sequence == 0
    assert graph.messages == ()
    assert graph.agent_runs == ()
    assert graph.receipts == ()
    assert graph.outbox_events == ()


def _graph_signature(graph: _StoredGraph) -> tuple[object, ...]:
    """Capture durable identities and aggregate state without ORM identity semantics."""
    return (
        graph.conversation.version,
        graph.conversation.last_message_sequence,
        graph.conversation.updated_at,
        tuple(message.id for message in graph.messages),
        tuple(agent_run.id for agent_run in graph.agent_runs),
        tuple(
            (
                receipt.operation,
                receipt.idempotency_key,
                receipt.message_id,
                receipt.agent_run_id,
            )
            for receipt in graph.receipts
        ),
        tuple(event.id for event in graph.outbox_events),
    )


async def _attempt(
    *,
    label: str,
    handler: AcceptUserTurnHandler,
    actor: ActorContext,
    command: AcceptUserTurnCommand,
) -> tuple[str, _AttemptOutcome]:
    try:
        return label, await handler.handle(actor=actor, command=command)
    except Exception as exc:
        return label, exc


@pytest.mark.integration
@pytest.mark.asyncio
async def test_success_commits_complete_atomic_graph() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        command = _command(case)
        result = await _handler(case).handle(
            actor=case.actor,
            command=command,
        )
        graph = await _load_graph(case)

        assert result.replayed is False
        _assert_single_turn_graph(
            graph,
            result=result,
            expected_content=command.content,
            expected_key=command.idempotency_key,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exact_replay_returns_original_result_without_extra_side_effects() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        handler = _handler(case)
        command = _command(case)

        original = await handler.handle(actor=case.actor, command=command)
        before_replay = await _load_graph(case)
        replayed = await handler.handle(actor=case.actor, command=command)
        after_replay = await _load_graph(case)

        assert original.replayed is False
        assert replayed == replace(original, replayed=True)
        assert _graph_signature(after_replay) == _graph_signature(before_replay)
        _assert_single_turn_graph(
            after_replay,
            result=original,
            expected_content=command.content,
            expected_key=command.idempotency_key,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exact_replay_keeps_original_profile_pin_after_v2_activation() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        handler = _handler(case)
        command = _command(case, key="profile-v1-replay")

        original = await handler.handle(actor=case.actor, command=command)
        await _activate_profile_v2(case)
        replayed = await handler.handle(actor=case.actor, command=command)
        graph = await _load_graph(case)
        profiles, assignment = await _load_profile_state(case)

        assert replayed == replace(original, replayed=True)
        assert len(graph.agent_runs) == 1
        assert graph.agent_runs[0].assistant_profile_version == 1
        assert {profile.version for profile in profiles} == {1, 2}
        assert assignment is not None
        assert assignment.profile_version == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_new_idempotency_key_pins_newly_activated_profile_version() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        handler = _handler(case)

        first = await handler.handle(
            actor=case.actor,
            command=_command(case, key="profile-pin-v1"),
        )
        await _activate_profile_v2(case)
        second = await handler.handle(
            actor=case.actor,
            command=_command(
                case,
                key="profile-pin-v2",
                content="Explain immutable profile version pins.",
            ),
        )
        graph = await _load_graph(case)

        runs_by_id = {record.id: record for record in graph.agent_runs}
        assert runs_by_id[first.agent_run_id.value].assistant_profile_version == 1
        assert runs_by_id[second.agent_run_id.value].assistant_profile_version == 2
        assert second.conversation_version == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_identical_same_scope_creates_one_original_and_one_replay() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        authorizer = _BarrierOwnerAuthorizer(asyncio.Barrier(2))
        handler = _handler(case, authorizer=authorizer)
        command = _command(case, key="same-scope-identical")

        outcomes = await asyncio.gather(
            _attempt(
                label="first",
                handler=handler,
                actor=case.actor,
                command=command,
            ),
            _attempt(
                label="second",
                handler=handler,
                actor=case.actor,
                command=command,
            ),
        )
        results = [outcome for _, outcome in outcomes if isinstance(outcome, AcceptUserTurnResult)]

        assert len(results) == 2
        assert sum(not result.replayed for result in results) == 1
        assert sum(result.replayed for result in results) == 1

        original = next(result for result in results if not result.replayed)
        replayed = next(result for result in results if result.replayed)
        assert replayed == replace(original, replayed=True)

        graph = await _load_graph(case)
        _assert_single_turn_graph(
            graph,
            result=original,
            expected_content=command.content,
            expected_key=command.idempotency_key,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_different_payload_same_scope_has_one_success_and_one_conflict() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        authorizer = _BarrierOwnerAuthorizer(asyncio.Barrier(2))
        handler = _handler(case, authorizer=authorizer)
        first = _command(
            case,
            key="same-scope-conflict",
            content="First competing payload.",
        )
        second = _command(
            case,
            key="same-scope-conflict",
            content="Second competing payload.",
        )

        outcomes = await asyncio.gather(
            _attempt(
                label="first",
                handler=handler,
                actor=case.actor,
                command=first,
            ),
            _attempt(
                label="second",
                handler=handler,
                actor=case.actor,
                command=second,
            ),
        )
        successes = [
            (label, outcome)
            for label, outcome in outcomes
            if isinstance(outcome, AcceptUserTurnResult)
        ]
        conflicts = [
            outcome for _, outcome in outcomes if isinstance(outcome, IdempotencyConflictError)
        ]

        assert len(successes) == 1
        assert len(conflicts) == 1

        winner_label, winner = successes[0]
        winning_command = first if winner_label == "first" else second
        assert winner.replayed is False

        graph = await _load_graph(case)
        _assert_single_turn_graph(
            graph,
            result=winner,
            expected_content=winning_command.content,
            expected_key=winning_command.idempotency_key,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_different_keys_cas_one_winner_without_partial_loser() -> None:
    async with _database_case() as case, asyncio.timeout(10):
        authorizer = _BarrierOwnerAuthorizer(asyncio.Barrier(2))
        handler = _handler(case, authorizer=authorizer)
        first = _command(
            case,
            key="different-key-a",
            content="First independently keyed payload.",
        )
        second = _command(
            case,
            key="different-key-b",
            content="Second independently keyed payload.",
        )

        outcomes = await asyncio.gather(
            _attempt(
                label="first",
                handler=handler,
                actor=case.actor,
                command=first,
            ),
            _attempt(
                label="second",
                handler=handler,
                actor=case.actor,
                command=second,
            ),
        )
        successes = [
            (label, outcome)
            for label, outcome in outcomes
            if isinstance(outcome, AcceptUserTurnResult)
        ]
        concurrency_errors = [
            outcome for _, outcome in outcomes if isinstance(outcome, ConversationConcurrencyError)
        ]

        assert len(successes) == 1
        assert len(concurrency_errors) == 1

        winner_label, winner = successes[0]
        winning_command = first if winner_label == "first" else second

        graph = await _load_graph(case)
        _assert_single_turn_graph(
            graph,
            result=winner,
            expected_content=winning_command.content,
            expected_key=winning_command.idempotency_key,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_staged_constraint_failure_rolls_back_profile_graph_and_turn() -> None:
    sentinel_event_id = EventId.new()

    async with (
        _database_case(sentinel_event_id=sentinel_event_id) as case,
        asyncio.timeout(10),
    ):
        ids = _FixedTurnIds(
            message_id=MessageId.new(),
            agent_run_id=AgentRunId.new(),
            event_id=sentinel_event_id,
        )

        with pytest.raises(IntegrityError):
            await _handler(case, ids=ids).handle(
                actor=case.actor,
                command=_command(case, key="forced-flush-failure"),
            )

        graph = await _load_graph(case)
        profiles, assignment = await _load_profile_state(case)

        assert graph.conversation.version == 0
        assert graph.conversation.last_message_sequence == 0
        assert graph.messages == ()
        assert graph.agent_runs == ()
        assert graph.receipts == ()
        assert len(graph.outbox_events) == 1
        assert graph.outbox_events[0].id == sentinel_event_id.value
        assert graph.outbox_events[0].aggregate_version == 99
        assert profiles == ()
        assert assignment is None


@pytest.mark.parametrize("case_name", ["unauthorized", "missing"])
@pytest.mark.integration
@pytest.mark.asyncio
async def test_unavailable_conversation_writes_nothing(case_name: str) -> None:
    async with _database_case() as case, asyncio.timeout(10):
        actor = case.actor
        conversation_id = case.conversation.id

        if case_name == "unauthorized":
            actor = replace(actor, actor_id=UserId.new())
        else:
            conversation_id = ConversationId.new()

        with pytest.raises(ConversationNotFoundError, match="Conversation was not found"):
            await _handler(case).handle(
                actor=actor,
                command=_command(
                    case,
                    key=f"{case_name}-turn",
                    conversation_id=conversation_id,
                ),
            )

        graph = await _load_graph(case)
        _assert_no_turn_writes(graph)
