from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.application.accept_user_turn import AgentRunQueuedEvent
from enterprise_chat_platform.application.realtime_events import (
    AGENT_RUN_TERMINAL_EVENT_TYPE,
)
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
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
    InputPolicyAssessmentRecord,
    MessageRecord,
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.input_policy_assessment import (
    SqlAlchemyInputPolicyAssessmentRepository,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)


def _database_resources() -> tuple[
    AsyncEngine,
    AsyncSessionFactory,
    AsyncEngine,
    AsyncSessionFactory,
]:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())
    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
    assert (runtime_url.host, runtime_url.port, runtime_url.database) == (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    )

    runtime_engine = create_database_engine(runtime_settings)
    migration_engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    return (
        runtime_engine,
        create_session_factory(runtime_engine),
        migration_engine,
        create_session_factory(migration_engine),
    )


async def _seed_job(
    *,
    session_factory: AsyncSessionFactory,
) -> AgentRunQueuedEvent:
    tenant_id = TenantId.new()
    conversation_id = ConversationId.new()
    actor_id = UserId.new()
    conversation = Conversation.create(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=actor_id,
        at=BASE_TIME,
    )
    accepted = conversation.accept_user_turn(
        trusted_tenant_id=tenant_id,
        actor_id=actor_id,
        message_id=MessageId.new(),
        agent_run_id=AgentRunId.new(),
        content="Review this request before calling the model.",
        idempotency_key=IdempotencyKey(f"policy-worker-{conversation_id}"),
        at=BASE_TIME,
    )
    event = AgentRunQueuedEvent(
        id=EventId.new(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        agent_run_id=accepted.agent_run.id,
        input_message_id=accepted.message.id,
        conversation_version=accepted.updated_conversation.version,
        occurred_at=BASE_TIME,
    )

    async with session_factory() as session, session.begin():
        session.add(conversation_to_new_record(accepted.updated_conversation))
        await session.flush()
        session.add(message_to_new_record(accepted.message))
        await session.flush()
        session.add(agent_run_to_new_record(accepted.agent_run))
        await session.flush()
        session.add(agent_run_queued_event_to_new_record(event))

    return event


async def _cleanup_job(
    *,
    session_factory: AsyncSessionFactory,
    event: AgentRunQueuedEvent,
) -> None:
    """Use the local migrator because runtime cannot delete audit records."""
    async with session_factory() as session, session.begin():
        await session.execute(
            delete(InputPolicyAssessmentRecord).where(
                InputPolicyAssessmentRecord.tenant_id == event.tenant_id.value,
                InputPolicyAssessmentRecord.agent_run_id == event.agent_run_id.value,
            )
        )
        await session.execute(
            delete(OutboxEventRecord).where(
                OutboxEventRecord.tenant_id == event.tenant_id.value,
                OutboxEventRecord.aggregate_id == event.conversation_id.value,
            )
        )
        await session.execute(
            delete(AgentRunRecord).where(
                AgentRunRecord.tenant_id == event.tenant_id.value,
                AgentRunRecord.id == event.agent_run_id.value,
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


def _assessment(action: PolicyAction, *, version: str) -> PolicyAssessment:
    if action is PolicyAction.ALLOW:
        return PolicyAssessment(
            action=action,
            risk_score=0.0,
            policy_version=version,
        )

    return PolicyAssessment(
        action=action,
        risk_score=0.9 if action is PolicyAction.BLOCK else 0.6,
        policy_version=version,
        findings=(
            PolicyFinding(
                code=(
                    "security.prompt_injection"
                    if action is PolicyAction.BLOCK
                    else "review.human_requested"
                ),
                severity=(
                    PolicySeverity.HIGH if action is PolicyAction.BLOCK else PolicySeverity.MEDIUM
                ),
                safe_message="The request cannot be processed automatically.",
            ),
        ),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_allow_persists_assessment_and_retains_owned_lease() -> None:
    runtime_engine, runtime_factory, migration_engine, migration_factory = _database_resources()
    event = await _seed_job(session_factory=runtime_factory)
    store = SqlAlchemyAgentRunJobStore(runtime_factory)

    try:
        async with asyncio.timeout(10):
            job = await store.claim_next(
                now=BASE_TIME + timedelta(seconds=1),
                lease_duration=timedelta(seconds=30),
            )
            assert job is not None
            stored = await store.apply_input_policy(
                job=job,
                assessment=_assessment(
                    PolicyAction.ALLOW,
                    version="conversation-input.allow",
                ),
                assessed_at=BASE_TIME + timedelta(seconds=2),
            )

            async with runtime_factory() as session:
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )
                queued = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                assessment = await session.get(
                    InputPolicyAssessmentRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )
                terminal_count = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRecord)
                    .where(
                        OutboxEventRecord.tenant_id == event.tenant_id.value,
                        OutboxEventRecord.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE,
                    )
                )

            assert stored is not None
            assert stored.action is PolicyAction.ALLOW
            assert assessment is not None
            assert run is not None
            assert run.status == AgentRunStatus.RUNNING.value
            assert queued is not None
            assert queued.published_at is None
            assert queued.lease_token == job.lease_token
            assert queued.lease_expires_at == BASE_TIME + timedelta(seconds=31)
            assert terminal_count == 0
    finally:
        await _cleanup_job(session_factory=migration_factory, event=event)
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_code", "expected_message"),
    [
        (
            PolicyAction.BLOCK,
            "input_policy_blocked",
            "The request was blocked by conversation policy.",
        ),
        (
            PolicyAction.ESCALATE,
            "input_policy_escalated",
            "The request requires human review and was not sent to the assistant.",
        ),
    ],
)
async def test_non_allow_decision_atomically_finishes_job(
    action: PolicyAction,
    expected_code: str,
    expected_message: str,
) -> None:
    runtime_engine, runtime_factory, migration_engine, migration_factory = _database_resources()
    event = await _seed_job(session_factory=runtime_factory)
    store = SqlAlchemyAgentRunJobStore(runtime_factory)

    try:
        async with asyncio.timeout(10):
            job = await store.claim_next(
                now=BASE_TIME + timedelta(seconds=1),
                lease_duration=timedelta(seconds=30),
            )
            assert job is not None
            stored = await store.apply_input_policy(
                job=job,
                assessment=_assessment(
                    action,
                    version=f"conversation-input.{action.value}",
                ),
                assessed_at=BASE_TIME + timedelta(seconds=2),
            )

            async with runtime_factory() as session:
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )
                queued = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                terminal = (
                    await session.execute(
                        select(OutboxEventRecord).where(
                            OutboxEventRecord.tenant_id == event.tenant_id.value,
                            OutboxEventRecord.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE,
                        )
                    )
                ).scalar_one()

            assert stored is not None
            assert stored.action is action
            assert run is not None
            assert run.status == AgentRunStatus.FAILED.value
            assert run.failure_code == expected_code
            assert run.failure_safe_message == expected_message
            assert queued is not None
            assert queued.published_at == BASE_TIME + timedelta(seconds=2)
            assert queued.lease_token is None
            assert queued.lease_expires_at is None
            assert terminal.published_at is None
            assert terminal.payload == {
                "agent_run_id": str(event.agent_run_id),
                "status": AgentRunStatus.FAILED.value,
                "output_message_id": None,
                "conversation_version": event.conversation_version,
                "message_sequence": None,
            }
    finally:
        await _cleanup_job(session_factory=migration_factory, event=event)
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_first_stored_decision_controls_terminal_action() -> None:
    runtime_engine, runtime_factory, migration_engine, migration_factory = _database_resources()
    event = await _seed_job(session_factory=runtime_factory)
    store = SqlAlchemyAgentRunJobStore(runtime_factory)
    first = _assessment(
        PolicyAction.BLOCK,
        version="conversation-input.first",
    )

    try:
        async with asyncio.timeout(10):
            job = await store.claim_next(
                now=BASE_TIME + timedelta(seconds=1),
                lease_duration=timedelta(seconds=30),
            )
            assert job is not None

            async with runtime_factory() as session, session.begin():
                repository = SqlAlchemyInputPolicyAssessmentRepository(session)
                assert (
                    await repository.insert_or_get(
                        tenant_id=job.tenant_id,
                        agent_run_id=job.agent_run_id,
                        assessment=first,
                        assessed_at=BASE_TIME + timedelta(seconds=1),
                    )
                    == first
                )

            stored = await store.apply_input_policy(
                job=job,
                assessment=_assessment(
                    PolicyAction.ALLOW,
                    version="conversation-input.later",
                ),
                assessed_at=BASE_TIME + timedelta(seconds=2),
            )

            async with runtime_factory() as session:
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )

            assert stored == first
            assert run is not None
            assert run.status == AgentRunStatus.FAILED.value
            assert run.failure_code == "input_policy_blocked"
    finally:
        await _cleanup_job(session_factory=migration_factory, event=event)
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_returns_none_without_persisting_decision() -> None:
    runtime_engine, runtime_factory, migration_engine, migration_factory = _database_resources()
    event = await _seed_job(session_factory=runtime_factory)
    store = SqlAlchemyAgentRunJobStore(runtime_factory)

    try:
        async with asyncio.timeout(10):
            job = await store.claim_next(
                now=BASE_TIME + timedelta(seconds=1),
                lease_duration=timedelta(seconds=5),
            )
            assert job is not None
            stored = await store.apply_input_policy(
                job=job,
                assessment=_assessment(
                    PolicyAction.BLOCK,
                    version="conversation-input.stale",
                ),
                assessed_at=BASE_TIME + timedelta(seconds=6),
            )

            async with runtime_factory() as session:
                run = await session.get(
                    AgentRunRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )
                queued = await session.get(
                    OutboxEventRecord,
                    (event.tenant_id.value, event.id.value),
                )
                assessment = await session.get(
                    InputPolicyAssessmentRecord,
                    (event.tenant_id.value, event.agent_run_id.value),
                )

            assert stored is None
            assert assessment is None
            assert run is not None
            assert run.status == AgentRunStatus.RUNNING.value
            assert queued is not None
            assert queued.published_at is None
            assert queued.lease_token == job.lease_token
    finally:
        await _cleanup_job(session_factory=migration_factory, event=event)
        await runtime_engine.dispose()
        await migration_engine.dispose()
