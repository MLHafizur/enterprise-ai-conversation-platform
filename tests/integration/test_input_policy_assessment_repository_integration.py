from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.governance import PolicyAction, PolicyAssessment
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
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
from enterprise_chat_platform.infrastructure.persistence.mappers.agent_run import (
    agent_run_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.message import (
    message_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    ConversationRecord,
    MessageRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.input_policy_assessment import (
    SqlAlchemyInputPolicyAssessmentRepository,
)
from enterprise_chat_platform.settings import Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 11, 16, 16, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _ParentGraph:
    tenant_id: TenantId
    conversation_id: ConversationId
    message_id: MessageId
    agent_run_id: AgentRunId


def _runtime_session_factory() -> tuple[AsyncSessionFactory, AsyncEngine]:
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())
    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    engine = create_database_engine(settings)
    return create_session_factory(engine), engine


def _assessment(*, version: str, risk_score: float) -> PolicyAssessment:
    return PolicyAssessment(
        action=PolicyAction.ALLOW,
        risk_score=risk_score,
        policy_version=version,
    )


async def _stage_parent_graph(
    session: AsyncSession,
    *,
    tenant_id: TenantId | None = None,
    agent_run_id: AgentRunId | None = None,
) -> _ParentGraph:
    resolved_tenant_id = tenant_id or TenantId.new()
    resolved_agent_run_id = agent_run_id or AgentRunId.new()
    conversation_id = ConversationId.new()
    message_id = MessageId.new()
    actor_id = UserId.new()

    conversation = Conversation.create(
        id=conversation_id,
        tenant_id=resolved_tenant_id,
        created_by=actor_id,
        at=BASE_TIME,
    )
    accepted = conversation.accept_user_turn(
        trusted_tenant_id=resolved_tenant_id,
        actor_id=actor_id,
        message_id=message_id,
        agent_run_id=resolved_agent_run_id,
        content="Explain deterministic policy decisions.",
        idempotency_key=IdempotencyKey(f"policy-{conversation_id}"),
        at=BASE_TIME,
    )

    session.add(conversation_to_new_record(accepted.updated_conversation))
    await session.flush()
    session.add(message_to_new_record(accepted.message))
    await session.flush()
    session.add(agent_run_to_new_record(accepted.agent_run.start(at=BASE_TIME)))
    await session.flush()

    return _ParentGraph(
        tenant_id=resolved_tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        agent_run_id=resolved_agent_run_id,
    )


async def _delete_parent_graph(session: AsyncSession, graph: _ParentGraph) -> None:
    await session.execute(
        delete(AgentRunRecord).where(
            AgentRunRecord.tenant_id == graph.tenant_id.value,
            AgentRunRecord.id == graph.agent_run_id.value,
        )
    )
    await session.execute(
        delete(MessageRecord).where(
            MessageRecord.tenant_id == graph.tenant_id.value,
            MessageRecord.id == graph.message_id.value,
        )
    )
    await session.execute(
        delete(ConversationRecord).where(
            ConversationRecord.tenant_id == graph.tenant_id.value,
            ConversationRecord.id == graph.conversation_id.value,
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_round_trip_and_sequential_first_write_wins() -> None:
    session_factory, engine = _runtime_session_factory()
    first = _assessment(version="conversation-input.v1", risk_score=0.1)
    later = _assessment(version="conversation-input.v2", risk_score=0.2)

    try:
        async with asyncio.timeout(5), session_factory() as session:
            transaction = await session.begin()
            try:
                graph = await _stage_parent_graph(session)
                repository = SqlAlchemyInputPolicyAssessmentRepository(session)

                inserted = await repository.insert_or_get(
                    tenant_id=graph.tenant_id,
                    agent_run_id=graph.agent_run_id,
                    assessment=first,
                    assessed_at=BASE_TIME,
                )
                replayed = await repository.insert_or_get(
                    tenant_id=graph.tenant_id,
                    agent_run_id=graph.agent_run_id,
                    assessment=later,
                    assessed_at=BASE_TIME,
                )
                stored = await repository.get(
                    tenant_id=graph.tenant_id,
                    agent_run_id=graph.agent_run_id,
                )

                assert inserted == first
                assert replayed == first
                assert stored == first
                assert transaction.is_active
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_run_uuid_remains_isolated_by_tenant() -> None:
    session_factory, engine = _runtime_session_factory()
    shared_agent_run_id = AgentRunId.new()
    first = _assessment(version="conversation-input.tenant-a", risk_score=0.1)
    second = _assessment(version="conversation-input.tenant-b", risk_score=0.2)

    try:
        async with asyncio.timeout(5), session_factory() as session:
            transaction = await session.begin()
            try:
                first_graph = await _stage_parent_graph(
                    session,
                    agent_run_id=shared_agent_run_id,
                )
                second_graph = await _stage_parent_graph(
                    session,
                    agent_run_id=shared_agent_run_id,
                )
                repository = SqlAlchemyInputPolicyAssessmentRepository(session)

                await repository.insert_or_get(
                    tenant_id=first_graph.tenant_id,
                    agent_run_id=shared_agent_run_id,
                    assessment=first,
                    assessed_at=BASE_TIME,
                )
                await repository.insert_or_get(
                    tenant_id=second_graph.tenant_id,
                    agent_run_id=shared_agent_run_id,
                    assessment=second,
                    assessed_at=BASE_TIME,
                )

                assert (
                    await repository.get(
                        tenant_id=first_graph.tenant_id,
                        agent_run_id=shared_agent_run_id,
                    )
                    == first
                )
                assert (
                    await repository.get(
                        tenant_id=second_graph.tenant_id,
                        agent_run_id=shared_agent_run_id,
                    )
                    == second
                )
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_caller_rollback_leaves_no_assessment() -> None:
    session_factory, engine = _runtime_session_factory()
    graph: _ParentGraph | None = None

    try:
        async with asyncio.timeout(5), session_factory() as session:
            async with session.begin():
                graph = await _stage_parent_graph(session)

            transaction = await session.begin()
            try:
                repository = SqlAlchemyInputPolicyAssessmentRepository(session)
                inserted = await repository.insert_or_get(
                    tenant_id=graph.tenant_id,
                    agent_run_id=graph.agent_run_id,
                    assessment=_assessment(
                        version="conversation-input.rollback",
                        risk_score=0.1,
                    ),
                    assessed_at=BASE_TIME,
                )
                assert inserted.policy_version == "conversation-input.rollback"
            finally:
                if transaction.is_active:
                    await transaction.rollback()

            async with session.begin():
                repository = SqlAlchemyInputPolicyAssessmentRepository(session)
                assert (
                    await repository.get(
                        tenant_id=graph.tenant_id,
                        agent_run_id=graph.agent_run_id,
                    )
                    is None
                )

            async with session.begin():
                await _delete_parent_graph(session, graph)
            graph = None
    finally:
        # A failure before normal cleanup still cannot leave an assessment because
        # its caller-owned transaction is rolled back when the session closes.
        if graph is not None:
            async with session_factory() as cleanup_session:
                async with cleanup_session.begin():
                    await _delete_parent_graph(cleanup_session, graph)
        await engine.dispose()
