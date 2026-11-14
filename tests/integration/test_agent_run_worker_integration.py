from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from enterprise_chat_platform.application.accept_user_turn import AgentRunQueuedEvent
from enterprise_chat_platform.application.process_agent_run import (
    AgentRunWorkerConfig,
    AgentRunWorkResult,
    ChatCompletion,
    ChatRequest,
    ProcessAgentRunWorker,
    TransientChatProviderError,
)
from enterprise_chat_platform.application.realtime_events import (
    AGENT_RUN_TERMINAL_EVENT_TYPE,
    AGENT_RUN_TERMINAL_PAYLOAD_FIELDS,
)
from enterprise_chat_platform.domain.agent_run import AgentRunFailure, AgentRunStatus
from enterprise_chat_platform.domain.chat import Conversation, MessageRole
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.agent_run_job_store import (
    SqlAlchemyAgentRunJobStore,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.agent_run import (
    agent_run_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.message import (
    message_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.outbox_event import (
    agent_run_queued_event_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    ConversationRecord,
    MessageRecord,
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.notification_relay_store import (
    SqlAlchemyNotificationRelayStore,
)
from enterprise_chat_platform.settings import Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ACCEPTED_AT = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, *values: datetime) -> None:
        self._values = deque(values)

    def now(self) -> datetime:
        return self._values.popleft()


class FixedIds:
    def __init__(self, message_id: MessageId) -> None:
        self._message_id = message_id

    def new_message_id(self) -> MessageId:
        return self._message_id


class StaticProvider:
    def __init__(self, content: str) -> None:
        self._content = content
        self.call_count = 0

    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        self.call_count += 1
        assert request.input_content == "Explain recoverable workers."
        return ChatCompletion(content=self._content)


class TransientThenStaticProvider:
    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        del request
        self.call_count += 1
        if self.call_count == 1:
            raise TransientChatProviderError("private provider failure")
        return ChatCompletion(content="Recovered response")


class NeverProvider:
    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        del request
        raise AssertionError("A terminal run must not call the provider again")


def _database_resources() -> tuple[AsyncEngine, AsyncSessionFactory]:
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    assert settings.environment in {"local", "test"}
    assert url.host in LOOPBACK_HOSTS
    engine = create_database_engine(settings)
    return engine, create_session_factory(engine)


async def _seed_job(
    *,
    session_factory: AsyncSessionFactory,
) -> tuple[AgentRunQueuedEvent, UserId]:
    tenant_id = TenantId.new()
    conversation_id = ConversationId.new()
    actor_id = UserId.new()
    service_author_id = UserId.new()
    conversation = Conversation.create(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=actor_id,
        at=ACCEPTED_AT,
    )
    accepted = conversation.accept_user_turn(
        trusted_tenant_id=tenant_id,
        actor_id=actor_id,
        message_id=MessageId.new(),
        agent_run_id=AgentRunId.new(),
        content="Explain recoverable workers.",
        idempotency_key=IdempotencyKey("worker-integration-request"),
        at=ACCEPTED_AT,
    )
    event = AgentRunQueuedEvent(
        id=EventId.new(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        agent_run_id=accepted.agent_run.id,
        input_message_id=accepted.message.id,
        conversation_version=accepted.updated_conversation.version,
        occurred_at=ACCEPTED_AT,
    )

    async with session_factory() as session, session.begin():
        session.add(conversation_to_new_record(accepted.updated_conversation))
        await session.flush()
        session.add(message_to_new_record(accepted.message))
        await session.flush()
        session.add(agent_run_to_new_record(accepted.agent_run))
        await session.flush()
        session.add(agent_run_queued_event_to_new_record(event))

    return event, service_author_id


async def _cleanup_job(
    *,
    session_factory: AsyncSessionFactory,
    event: AgentRunQueuedEvent,
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            delete(OutboxEventRecord).where(
                OutboxEventRecord.tenant_id == event.tenant_id.value,
                OutboxEventRecord.aggregate_id == event.conversation_id.value,
            )
        )
        await session.execute(
            delete(AgentRunRecord).where(
                AgentRunRecord.tenant_id == event.tenant_id.value,
                AgentRunRecord.conversation_id == event.conversation_id.value,
            )
        )
        await session.execute(
            delete(MessageRecord).where(
                MessageRecord.tenant_id == event.tenant_id.value,
                MessageRecord.conversation_id == event.conversation_id.value,
            )
        )
        await session.execute(
            delete(ConversationRecord).where(
                ConversationRecord.tenant_id == event.tenant_id.value,
                ConversationRecord.id == event.conversation_id.value,
            )
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_success_commits_one_assistant_message_and_terminal_state() -> None:
    engine, session_factory = _database_resources()
    event, service_author_id = await _seed_job(session_factory=session_factory)
    output_message_id = MessageId.new()
    provider = StaticProvider("A lease fences late workers.")
    worker = ProcessAgentRunWorker(
        store=SqlAlchemyAgentRunJobStore(session_factory),
        provider=provider,
        clock=FixedClock(
            ACCEPTED_AT + timedelta(seconds=1),
            ACCEPTED_AT + timedelta(seconds=2),
            ACCEPTED_AT + timedelta(seconds=3),
        ),
        ids=FixedIds(output_message_id),
        service_author_id=service_author_id,
        config=AgentRunWorkerConfig(),
    )

    try:
        async with asyncio.timeout(10):
            assert await worker.process_next() is AgentRunWorkResult.SUCCEEDED
            assert await worker.process_next() is AgentRunWorkResult.NO_WORK

            async with session_factory() as session:
                conversation = await session.get(
                    ConversationRecord,
                    (event.tenant_id.value, event.conversation_id.value),
                )
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )
                outbox = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                terminal_notification = (
                    await session.execute(
                        select(OutboxEventRecord).where(
                            OutboxEventRecord.tenant_id == event.tenant_id.value,
                            OutboxEventRecord.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE,
                        )
                    )
                ).scalar_one()
                messages = (
                    (
                        await session.execute(
                            select(MessageRecord)
                            .where(
                                MessageRecord.tenant_id == event.tenant_id.value,
                                MessageRecord.conversation_id == event.conversation_id.value,
                            )
                            .order_by(MessageRecord.sequence)
                        )
                    )
                    .scalars()
                    .all()
                )

            assert conversation is not None
            assert conversation.version == 2
            assert conversation.last_message_sequence == 2
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            assert run.output_message_id == output_message_id.value
            assert outbox is not None
            assert outbox.published_at is not None
            assert outbox.lease_token is None
            assert terminal_notification.aggregate_version == event.conversation_version
            assert terminal_notification.published_at is None
            assert terminal_notification.attempt_count == 0
            assert set(terminal_notification.payload) == AGENT_RUN_TERMINAL_PAYLOAD_FIELDS
            assert terminal_notification.payload == {
                "agent_run_id": str(event.agent_run_id),
                "status": AgentRunStatus.SUCCEEDED.value,
                "output_message_id": str(output_message_id),
                "conversation_version": 2,
                "message_sequence": 2,
            }
            assert "content" not in terminal_notification.payload
            assert len(messages) == 2
            assert messages[1].role == MessageRole.ASSISTANT.value
            assert messages[1].author_id == service_author_id.value
            assert provider.call_count == 1
    finally:
        await _cleanup_job(session_factory=session_factory, event=event)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_failure_reschedules_then_reclaims_running_job() -> None:
    engine, session_factory = _database_resources()
    event, service_author_id = await _seed_job(session_factory=session_factory)
    provider = TransientThenStaticProvider()
    store = SqlAlchemyAgentRunJobStore(session_factory)
    first_worker = ProcessAgentRunWorker(
        store=store,
        provider=provider,
        clock=FixedClock(
            ACCEPTED_AT + timedelta(seconds=1),
            ACCEPTED_AT + timedelta(seconds=2),
        ),
        ids=FixedIds(MessageId.new()),
        service_author_id=service_author_id,
        config=AgentRunWorkerConfig(
            initial_backoff=timedelta(seconds=2),
            jitter_fraction=0,
        ),
    )
    second_worker = ProcessAgentRunWorker(
        store=store,
        provider=provider,
        clock=FixedClock(
            ACCEPTED_AT + timedelta(seconds=4),
            ACCEPTED_AT + timedelta(seconds=5),
        ),
        ids=FixedIds(MessageId.new()),
        service_author_id=service_author_id,
        config=AgentRunWorkerConfig(
            initial_backoff=timedelta(seconds=2),
            jitter_fraction=0,
        ),
    )

    try:
        async with asyncio.timeout(10):
            assert await first_worker.process_next() is AgentRunWorkResult.RESCHEDULED
            assert await second_worker.process_next() is AgentRunWorkResult.SUCCEEDED

            async with session_factory() as session:
                outbox = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )

            assert outbox is not None
            assert outbox.attempt_count == 2
            assert outbox.published_at is not None
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            assert provider.call_count == 2
    finally:
        await _cleanup_job(session_factory=session_factory, event=event)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable_without_resetting_running_run() -> None:
    engine, session_factory = _database_resources()
    event, _ = await _seed_job(session_factory=session_factory)
    store = SqlAlchemyAgentRunJobStore(session_factory)

    try:
        async with asyncio.timeout(10):
            first = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=1),
                lease_duration=timedelta(seconds=5),
            )
            before_expiry = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=5),
                lease_duration=timedelta(seconds=5),
            )
            reclaimed = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=6),
                lease_duration=timedelta(seconds=5),
            )

            assert first is not None
            assert before_expiry is None
            assert reclaimed is not None
            assert reclaimed.lease_token != first.lease_token
            assert reclaimed.attempt_count == 2

            async with session_factory() as session:
                running_count = await session.scalar(
                    select(func.count())
                    .select_from(AgentRunRecord)
                    .where(
                        AgentRunRecord.tenant_id == event.tenant_id.value,
                        AgentRunRecord.id == event.agent_run_id.value,
                        AgentRunRecord.status == AgentRunStatus.RUNNING.value,
                    )
                )
            assert running_count == 1
    finally:
        await _cleanup_job(session_factory=session_factory, event=event)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_terminal_run_makes_duplicate_outbox_delivery_idempotent() -> None:
    engine, session_factory = _database_resources()
    event, service_author_id = await _seed_job(session_factory=session_factory)
    store = SqlAlchemyAgentRunJobStore(session_factory)
    first_worker = ProcessAgentRunWorker(
        store=store,
        provider=StaticProvider("Only one response"),
        clock=FixedClock(
            ACCEPTED_AT + timedelta(seconds=1),
            ACCEPTED_AT + timedelta(seconds=2),
        ),
        ids=FixedIds(MessageId.new()),
        service_author_id=service_author_id,
        config=AgentRunWorkerConfig(),
    )

    try:
        async with asyncio.timeout(10):
            assert await first_worker.process_next() is AgentRunWorkResult.SUCCEEDED

            # Simulate duplicate delivery by making the already completed
            # logical event pending again. The terminal run is the idempotency
            # fence, so a provider must not be called a second time.
            async with session_factory() as session, session.begin():
                outbox = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                    with_for_update=True,
                )
                assert outbox is not None
                outbox.published_at = None
                outbox.available_at = ACCEPTED_AT + timedelta(seconds=3)

            duplicate_worker = ProcessAgentRunWorker(
                store=store,
                provider=NeverProvider(),
                clock=FixedClock(ACCEPTED_AT + timedelta(seconds=4)),
                ids=FixedIds(MessageId.new()),
                service_author_id=service_author_id,
                config=AgentRunWorkerConfig(),
            )

            assert await duplicate_worker.process_next() is AgentRunWorkResult.NO_WORK

            async with session_factory() as session:
                outbox = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                message_count = await session.scalar(
                    select(func.count())
                    .select_from(MessageRecord)
                    .where(
                        MessageRecord.tenant_id == event.tenant_id.value,
                        MessageRecord.conversation_id == event.conversation_id.value,
                    )
                )

            assert outbox is not None
            assert outbox.attempt_count == 2
            assert outbox.published_at is not None
            assert message_count == 2
    finally:
        await _cleanup_job(session_factory=session_factory, event=event)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_failure_commits_one_pending_terminal_notification() -> None:
    engine, session_factory = _database_resources()
    event, _ = await _seed_job(session_factory=session_factory)
    store = SqlAlchemyAgentRunJobStore(session_factory)

    try:
        async with asyncio.timeout(10):
            job = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=1),
                lease_duration=timedelta(seconds=30),
            )
            assert job is not None

            applied = await store.fail(
                job=job,
                failure=AgentRunFailure(
                    code="provider_unavailable",
                    safe_message="The assistant is temporarily unavailable.",
                ),
                failed_at=ACCEPTED_AT + timedelta(seconds=2),
            )
            assert applied is True

            async with session_factory() as session:
                queued = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )
                terminal = (
                    await session.execute(
                        select(OutboxEventRecord).where(
                            OutboxEventRecord.tenant_id == event.tenant_id.value,
                            OutboxEventRecord.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE,
                        )
                    )
                ).scalar_one()

            assert queued is not None
            assert queued.published_at is not None
            assert run is not None
            assert run.status == AgentRunStatus.FAILED.value
            assert terminal.aggregate_version == event.conversation_version
            assert terminal.payload == {
                "agent_run_id": str(event.agent_run_id),
                "status": AgentRunStatus.FAILED.value,
                "output_message_id": None,
                "conversation_version": event.conversation_version,
                "message_sequence": None,
            }
            assert terminal.published_at is None
    finally:
        await _cleanup_job(session_factory=session_factory, event=event)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_notification_relay_retries_with_lease_fencing_then_publishes() -> None:
    engine, session_factory = _database_resources()
    event, service_author_id = await _seed_job(session_factory=session_factory)
    worker = ProcessAgentRunWorker(
        store=SqlAlchemyAgentRunJobStore(session_factory),
        provider=StaticProvider("Relay this terminal state."),
        clock=FixedClock(
            ACCEPTED_AT + timedelta(seconds=1),
            ACCEPTED_AT + timedelta(seconds=2),
        ),
        ids=FixedIds(MessageId.new()),
        service_author_id=service_author_id,
        config=AgentRunWorkerConfig(),
    )
    relay = SqlAlchemyNotificationRelayStore(session_factory)

    try:
        async with asyncio.timeout(10):
            assert await worker.process_next() is AgentRunWorkResult.SUCCEEDED

            first = await relay.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=3),
                lease_duration=timedelta(seconds=5),
            )
            assert first is not None
            assert first.event.agent_run_id == event.agent_run_id
            assert first.attempt_count == 1

            assert await relay.reschedule(
                notification=first,
                attempted_at=ACCEPTED_AT + timedelta(seconds=4),
                available_at=ACCEPTED_AT + timedelta(seconds=6),
            )
            assert (
                await relay.claim_next(
                    now=ACCEPTED_AT + timedelta(seconds=5),
                    lease_duration=timedelta(seconds=5),
                )
                is None
            )

            second = await relay.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=6),
                lease_duration=timedelta(seconds=5),
            )
            assert second is not None
            assert second.event.id == first.event.id
            assert second.lease_token != first.lease_token
            assert second.attempt_count == 2

            assert not await relay.mark_published(
                notification=first,
                published_at=ACCEPTED_AT + timedelta(seconds=7),
            )
            assert await relay.mark_published(
                notification=second,
                published_at=ACCEPTED_AT + timedelta(seconds=7),
            )
            assert not await relay.mark_published(
                notification=second,
                published_at=ACCEPTED_AT + timedelta(seconds=7),
            )

            async with session_factory() as session:
                terminal = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, second.event.id.value),
                )

            assert terminal is not None
            assert terminal.attempt_count == 2
            assert terminal.published_at == ACCEPTED_AT + timedelta(seconds=7)
            assert terminal.lease_token is None
            assert terminal.lease_expires_at is None
    finally:
        await _cleanup_job(session_factory=session_factory, event=event)
        await engine.dispose()
