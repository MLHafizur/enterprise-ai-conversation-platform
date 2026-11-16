from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.api.policy_notices import (
    SqlAlchemyInputPolicyNoticeQueries,
)
from enterprise_chat_platform.application.accept_user_turn import ActorContext
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
from enterprise_chat_platform.infrastructure.persistence.mappers.input_policy_assessment import (
    input_policy_assessment_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.message import (
    message_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    ConversationRecord,
    InputPolicyAssessmentRecord,
    MessageRecord,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 11, 16, 21, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _SeededConversation:
    actor: ActorContext
    conversation_id: ConversationId
    message_ids: tuple[MessageId, ...]
    run_ids: tuple[AgentRunId, ...]


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


def _assessment(action: PolicyAction) -> PolicyAssessment:
    if action is PolicyAction.ALLOW:
        return PolicyAssessment(
            action=action,
            risk_score=0.0,
            policy_version="conversation-input.integration",
        )
    return PolicyAssessment(
        action=action,
        risk_score=0.9 if action is PolicyAction.BLOCK else 0.6,
        policy_version="conversation-input.integration",
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
                safe_message=(
                    "This request cannot be processed automatically."
                    if action is PolicyAction.BLOCK
                    else "This request requires human review."
                ),
            ),
        ),
    )


async def _seed(
    session_factory: AsyncSessionFactory,
) -> _SeededConversation:
    tenant_id = TenantId.new()
    owner_id = UserId.new()
    conversation_id = ConversationId.new()
    conversation = Conversation.create(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=owner_id,
        at=BASE_TIME,
    )
    turns = []
    actions = (PolicyAction.ALLOW, PolicyAction.BLOCK, PolicyAction.ESCALATE)
    for index, action in enumerate(actions, start=1):
        accepted = conversation.accept_user_turn(
            trusted_tenant_id=tenant_id,
            actor_id=owner_id,
            message_id=MessageId.new(),
            agent_run_id=AgentRunId.new(),
            content=f"Integration policy input {index}",
            idempotency_key=IdempotencyKey(f"notice-integration-{index}-{conversation_id}"),
            at=BASE_TIME + timedelta(seconds=index),
        )
        conversation = accepted.updated_conversation
        turns.append((accepted, action))

    async with session_factory() as session, session.begin():
        session.add(conversation_to_new_record(conversation))
        await session.flush()
        for index, (accepted, action) in enumerate(turns, start=1):
            session.add(message_to_new_record(accepted.message))
            await session.flush()
            session.add(agent_run_to_new_record(accepted.agent_run))
            await session.flush()
            session.add(
                input_policy_assessment_to_new_record(
                    tenant_id=tenant_id,
                    agent_run_id=accepted.agent_run.id,
                    assessment=_assessment(action),
                    assessed_at=BASE_TIME + timedelta(minutes=index),
                )
            )

    return _SeededConversation(
        actor=ActorContext(tenant_id=tenant_id, actor_id=owner_id),
        conversation_id=conversation_id,
        message_ids=tuple(accepted.message.id for accepted, _ in turns),
        run_ids=tuple(accepted.agent_run.id for accepted, _ in turns),
    )


async def _cleanup(
    session_factory: AsyncSessionFactory,
    seeded: _SeededConversation,
) -> None:
    tenant_id = seeded.actor.tenant_id.value
    conversation_id = seeded.conversation_id.value
    async with session_factory() as session, session.begin():
        await session.execute(
            delete(InputPolicyAssessmentRecord).where(
                InputPolicyAssessmentRecord.tenant_id == tenant_id,
                InputPolicyAssessmentRecord.agent_run_id.in_(
                    tuple(run_id.value for run_id in seeded.run_ids)
                ),
            )
        )
        await session.execute(
            delete(AgentRunRecord).where(
                AgentRunRecord.tenant_id == tenant_id,
                AgentRunRecord.conversation_id == conversation_id,
            )
        )
        await session.execute(
            delete(MessageRecord).where(
                MessageRecord.tenant_id == tenant_id,
                MessageRecord.conversation_id == conversation_id,
            )
        )
        await session.execute(
            delete(ConversationRecord).where(
                ConversationRecord.tenant_id == tenant_id,
                ConversationRecord.id == conversation_id,
            )
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_reads_only_durable_block_and_escalation_notices() -> None:
    runtime_engine, runtime_factory, migration_engine, migration_factory = _database_resources()
    seeded = await _seed(runtime_factory)
    queries = SqlAlchemyInputPolicyNoticeQueries(runtime_factory)

    try:
        async with asyncio.timeout(10):
            response = await queries.list_for_conversation(
                actor=seeded.actor,
                conversation_id=seeded.conversation_id.value,
            )
            hidden = await queries.list_for_conversation(
                actor=ActorContext(
                    tenant_id=seeded.actor.tenant_id,
                    actor_id=UserId.new(),
                ),
                conversation_id=seeded.conversation_id.value,
            )

        assert response is not None
        assert tuple(item.action for item in response.items) == ("block", "escalate")
        assert tuple(item.input_message_id for item in response.items) == (
            seeded.message_ids[1].value,
            seeded.message_ids[2].value,
        )
        assert tuple(item.findings[0].safe_message for item in response.items) == (
            "This request cannot be processed automatically.",
            "This request requires human review.",
        )
        assert hidden is None
    finally:
        await _cleanup(migration_factory, seeded)
        await runtime_engine.dispose()
        await migration_engine.dispose()
