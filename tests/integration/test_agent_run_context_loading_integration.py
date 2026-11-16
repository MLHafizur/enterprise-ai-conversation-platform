from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.application.accept_user_turn import AgentRunQueuedEvent
from enterprise_chat_platform.domain.agent_run import AgentRun, AgentRunFailure
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.chat import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    AssistantProfileId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.domain.prompting import ENTERPRISE_CHAT_PROMPT_VERSION, PromptRole
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
from enterprise_chat_platform.infrastructure.persistence.mappers.assistant_profile import (
    assistant_profile_to_record,
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
from enterprise_chat_platform.infrastructure.persistence.mappers.outbox_event import (
    agent_run_queued_event_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    AssistantProfileVersionRecord,
    ConversationRecord,
    InputPolicyAssessmentRecord,
    MessageRecord,
    OutboxEventRecord,
    TenantAssistantProfileAssignmentRecord,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ACCEPTED_AT = datetime(2026, 11, 16, 19, 0, tzinfo=UTC)


def _database_resources() -> tuple[AsyncEngine, AsyncSessionFactory]:
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    assert settings.environment in {"local", "test"}
    assert url.host in LOOPBACK_HOSTS
    engine = create_database_engine(settings)
    return engine, create_session_factory(engine)


def _migration_cleanup_engine() -> AsyncEngine:
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


async def _seed_job(
    *,
    session_factory: AsyncSessionFactory,
    history: Sequence[tuple[MessageRole, str]],
    future_message: tuple[MessageRole, str] | None = None,
    add_isolation_rows: bool = False,
    history_user_actions: dict[int, PolicyAction | None] | None = None,
    successful_assistant_sequences: frozenset[int] | None = None,
    history_assessed_at: dict[int, datetime] | None = None,
) -> tuple[
    AgentRunQueuedEvent,
    tuple[tuple[TenantId, ConversationId], ...],
]:
    tenant_id = TenantId.new()
    conversation_id = ConversationId.new()
    actor_id = UserId.new()
    assistant_id = UserId.new()
    profile_id = AssistantProfileId.new()
    profile = AssistantProfile(
        tenant_id=tenant_id,
        ref=AssistantProfileRef(id=profile_id, version=1),
        prompt_template_version=ENTERPRISE_CHAT_PROMPT_VERSION,
        assistant_name="Context Assistant",
        organization_name="Context Test",
        response_tone=PromptResponseTone.PROFESSIONAL,
        expertise=PromptExpertiseLevel.SENIOR_ENGINEER,
        communication_style=PromptCommunicationStyle.DIRECT_HELPFUL,
        context_character_limit=4_096,
        created_at=ACCEPTED_AT,
        created_by=actor_id,
    )
    next_profile = AssistantProfile(
        tenant_id=tenant_id,
        ref=AssistantProfileRef(id=profile_id, version=2),
        prompt_template_version=ENTERPRISE_CHAT_PROMPT_VERSION,
        assistant_name="New Context Assistant",
        organization_name="Context Test",
        response_tone=PromptResponseTone.FRIENDLY,
        expertise=PromptExpertiseLevel.PRODUCT_SPECIALIST,
        communication_style=PromptCommunicationStyle.CONCISE_SUPPORTIVE,
        context_character_limit=2_048,
        created_at=ACCEPTED_AT + timedelta(seconds=1),
        created_by=actor_id,
    )
    input_sequence = len(history) + 1

    message_specs = [*history, (MessageRole.USER, "current-input")]
    if future_message is not None:
        message_specs.append(future_message)

    target_messages = tuple(
        Message(
            id=MessageId.new(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            author_id=actor_id if role is MessageRole.USER else assistant_id,
            content=content,
            sequence=sequence,
            idempotency_key=IdempotencyKey(f"target-{sequence}"),
            created_at=ACCEPTED_AT + timedelta(seconds=sequence),
            role=role,
        )
        for sequence, (role, content) in enumerate(message_specs, start=1)
    )
    input_message = target_messages[input_sequence - 1]
    history_messages = target_messages[: input_sequence - 1]
    conversation = Conversation(
        id=conversation_id,
        tenant_id=tenant_id,
        created_by=actor_id,
        status=ConversationStatus.ACTIVE,
        version=len(target_messages),
        last_message_sequence=len(target_messages),
        created_at=ACCEPTED_AT,
        updated_at=target_messages[-1].created_at,
    )
    run = AgentRun(
        id=AgentRunId.new(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        input_message_id=input_message.id,
        assistant_profile_ref=profile.ref,
        created_at=input_message.created_at,
    )
    event = AgentRunQueuedEvent(
        id=EventId.new(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        agent_run_id=run.id,
        input_message_id=input_message.id,
        conversation_version=input_sequence,
        occurred_at=input_message.created_at,
    )

    configured_actions = history_user_actions or {
        message.sequence: PolicyAction.ALLOW
        for message in history_messages
        if message.role is MessageRole.USER
    }
    if successful_assistant_sequences is None:
        successful_assistant_sequences = frozenset(
            message.sequence
            for message in history_messages
            if message.role is MessageRole.ASSISTANT
            and message.sequence > 1
            and history_messages[message.sequence - 2].role is MessageRole.USER
            and configured_actions.get(message.sequence - 1) is PolicyAction.ALLOW
        )

    historical_runs: list[AgentRun] = []
    assessment_times = history_assessed_at or {}
    historical_assessments: list[tuple[AgentRun, PolicyAssessment, datetime]] = []
    messages_by_sequence = {message.sequence: message for message in history_messages}
    for message in history_messages:
        if message.role is not MessageRole.USER:
            continue

        historical_run = AgentRun(
            id=AgentRunId.new(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            input_message_id=message.id,
            assistant_profile_ref=profile.ref,
            created_at=message.created_at,
        )
        action = configured_actions.get(message.sequence)
        next_message = messages_by_sequence.get(message.sequence + 1)
        if (
            next_message is not None
            and next_message.role is MessageRole.ASSISTANT
            and next_message.sequence in successful_assistant_sequences
        ):
            historical_run = historical_run.start(at=message.created_at).succeed(
                output_message_id=next_message.id,
                at=next_message.created_at,
            )
        elif action in {PolicyAction.BLOCK, PolicyAction.ESCALATE}:
            historical_run = historical_run.start(at=message.created_at).fail(
                failure=AgentRunFailure(
                    code=f"input_policy_{action.value}",
                    safe_message="The request was not sent to the assistant.",
                ),
                at=message.created_at,
            )

        historical_runs.append(historical_run)
        if action is not None:
            historical_assessments.append(
                (
                    historical_run,
                    _assessment(action, sequence=message.sequence),
                    assessment_times.get(message.sequence, historical_run.created_at),
                )
            )

    cleanup_targets: list[tuple[TenantId, ConversationId]] = [(tenant_id, conversation_id)]
    distractor_conversations: list[Conversation] = []
    distractor_messages: list[Message] = []
    if add_isolation_rows:
        other_conversation_id = ConversationId.new()
        other_tenant_id = TenantId.new()
        for distractor_tenant_id, distractor_conversation_id, content in (
            (tenant_id, other_conversation_id, "other-conversation-secret"),
            (other_tenant_id, conversation_id, "other-tenant-secret"),
        ):
            distractor_conversation = Conversation(
                id=distractor_conversation_id,
                tenant_id=distractor_tenant_id,
                created_by=actor_id,
                status=ConversationStatus.ACTIVE,
                version=1,
                last_message_sequence=1,
                created_at=ACCEPTED_AT,
                updated_at=ACCEPTED_AT + timedelta(seconds=1),
            )
            distractor_conversations.append(distractor_conversation)
            distractor_messages.append(
                Message(
                    id=MessageId.new(),
                    tenant_id=distractor_tenant_id,
                    conversation_id=distractor_conversation_id,
                    author_id=actor_id,
                    content=content,
                    sequence=1,
                    idempotency_key=IdempotencyKey(f"distractor-{content}"),
                    created_at=ACCEPTED_AT + timedelta(seconds=1),
                )
            )
            cleanup_targets.append((distractor_tenant_id, distractor_conversation_id))

    async with session_factory() as session, session.begin():
        session.add(assistant_profile_to_record(profile))
        session.add(assistant_profile_to_record(next_profile))
        session.add(
            TenantAssistantProfileAssignmentRecord(
                tenant_id=tenant_id.value,
                profile_id=profile.ref.id.value,
                profile_version=profile.ref.version,
                revision=0,
                activated_at=ACCEPTED_AT,
                activated_by=actor_id.value,
            )
        )
        await session.flush()

        session.add(conversation_to_new_record(conversation))
        for distractor_conversation in distractor_conversations:
            session.add(conversation_to_new_record(distractor_conversation))
        await session.flush()

        for message in (*target_messages, *distractor_messages):
            session.add(message_to_new_record(message))
        await session.flush()

        for historical_run in historical_runs:
            session.add(agent_run_to_new_record(historical_run))
        session.add(agent_run_to_new_record(run))
        await session.flush()
        for historical_run, assessment, assessed_at in historical_assessments:
            session.add(
                input_policy_assessment_to_new_record(
                    tenant_id=tenant_id,
                    agent_run_id=historical_run.id,
                    assessment=assessment,
                    assessed_at=assessed_at,
                )
            )
        await session.flush()
        session.add(agent_run_queued_event_to_new_record(event))

    return event, tuple(cleanup_targets)


def _assessment(action: PolicyAction, *, sequence: int) -> PolicyAssessment:
    if action is PolicyAction.ALLOW:
        return PolicyAssessment(
            action=action,
            risk_score=0,
            policy_version="context-history.v1",
        )

    return PolicyAssessment(
        action=action,
        risk_score=0.9,
        policy_version="context-history.v1",
        findings=(
            PolicyFinding(
                code=f"history.{action.value}.{sequence}",
                severity=PolicySeverity.HIGH,
                safe_message="This message is not eligible for model context.",
            ),
        ),
    )


async def _cleanup(
    *,
    event: AgentRunQueuedEvent,
    targets: tuple[tuple[TenantId, ConversationId], ...],
) -> None:
    engine = _migration_cleanup_engine()
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session, session.begin():
            await session.execute(
                delete(OutboxEventRecord).where(
                    OutboxEventRecord.tenant_id == event.tenant_id.value,
                    OutboxEventRecord.id == event.id.value,
                )
            )
            await session.execute(
                delete(AgentRunRecord).where(
                    AgentRunRecord.tenant_id == event.tenant_id.value,
                    AgentRunRecord.id == event.agent_run_id.value,
                )
            )
            for tenant_id, conversation_id in targets:
                run_ids = select(AgentRunRecord.id).where(
                    AgentRunRecord.tenant_id == tenant_id.value,
                    AgentRunRecord.conversation_id == conversation_id.value,
                )
                await session.execute(
                    delete(InputPolicyAssessmentRecord).where(
                        InputPolicyAssessmentRecord.tenant_id == tenant_id.value,
                        InputPolicyAssessmentRecord.agent_run_id.in_(run_ids),
                    )
                )
                await session.execute(
                    delete(AgentRunRecord).where(
                        AgentRunRecord.tenant_id == tenant_id.value,
                        AgentRunRecord.conversation_id == conversation_id.value,
                    )
                )
                await session.execute(
                    delete(MessageRecord).where(
                        MessageRecord.tenant_id == tenant_id.value,
                        MessageRecord.conversation_id == conversation_id.value,
                    )
                )
                await session.execute(
                    delete(ConversationRecord).where(
                        ConversationRecord.tenant_id == tenant_id.value,
                        ConversationRecord.id == conversation_id.value,
                    )
                )
            await session.execute(
                delete(TenantAssistantProfileAssignmentRecord).where(
                    TenantAssistantProfileAssignmentRecord.tenant_id == event.tenant_id.value,
                )
            )
            await session.execute(
                delete(AssistantProfileVersionRecord).where(
                    AssistantProfileVersionRecord.tenant_id == event.tenant_id.value,
                )
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_loads_bounded_prior_context_in_chronological_order() -> None:
    engine, session_factory = _database_resources()
    event, cleanup_targets = await _seed_job(
        session_factory=session_factory,
        history=(
            (MessageRole.USER, "history-1"),
            (MessageRole.ASSISTANT, "history-2"),
            (MessageRole.USER, "history-3"),
            (MessageRole.ASSISTANT, "history-4"),
        ),
        future_message=(MessageRole.ASSISTANT, "future-6"),
        add_isolation_rows=True,
    )
    store = SqlAlchemyAgentRunJobStore(session_factory, history_row_cap=3)

    try:
        async with asyncio.timeout(10):
            first = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=20),
                lease_duration=timedelta(seconds=5),
            )
            assert first is not None
            async with session_factory() as session, session.begin():
                await session.execute(
                    update(TenantAssistantProfileAssignmentRecord)
                    .where(
                        TenantAssistantProfileAssignmentRecord.tenant_id == first.tenant_id.value,
                        TenantAssistantProfileAssignmentRecord.profile_id
                        == first.assistant_profile.ref.id.value,
                    )
                    .values(
                        profile_version=2,
                        revision=1,
                        activated_at=ACCEPTED_AT + timedelta(seconds=21),
                    )
                )
            reclaimed = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=26),
                lease_duration=timedelta(seconds=5),
            )

        assert first.input_content == "current-input"
        assert [(turn.sequence, turn.role, turn.content) for turn in first.context_messages] == [
            (2, PromptRole.ASSISTANT, "history-2"),
            (3, PromptRole.USER, "history-3"),
            (4, PromptRole.ASSISTANT, "history-4"),
        ]
        context_contents = {turn.content for turn in first.context_messages}
        assert "history-1" not in context_contents
        assert "current-input" not in context_contents
        assert "future-6" not in context_contents
        assert "other-conversation-secret" not in context_contents
        assert "other-tenant-secret" not in context_contents

        assert reclaimed is not None
        assert reclaimed.attempt_count == 2
        assert reclaimed.context_messages == first.context_messages
        assert reclaimed.assistant_profile == first.assistant_profile
        assert reclaimed.assistant_profile.ref.version == 1
    finally:
        await _cleanup(event=event, targets=cleanup_targets)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_excludes_history_without_positive_security_proofs() -> None:
    engine, session_factory = _database_resources()
    event, cleanup_targets = await _seed_job(
        session_factory=session_factory,
        history=(
            (MessageRole.USER, "allowed-user"),
            (MessageRole.ASSISTANT, "successful-assistant"),
            (MessageRole.USER, "blocked-user-secret"),
            (MessageRole.ASSISTANT, "orphan-after-block"),
            (MessageRole.USER, "escalated-user-secret"),
            (MessageRole.USER, "unassessed-user-secret"),
            (MessageRole.ASSISTANT, "legacy-unassessed-assistant"),
            (MessageRole.USER, "late-allowed-user"),
            (MessageRole.ASSISTANT, "late-assessed-assistant"),
        ),
        future_message=(MessageRole.ASSISTANT, "future-assistant"),
        add_isolation_rows=True,
        history_user_actions={
            1: PolicyAction.ALLOW,
            3: PolicyAction.BLOCK,
            5: PolicyAction.ESCALATE,
            6: None,
            8: PolicyAction.ALLOW,
        },
        successful_assistant_sequences=frozenset({2, 7, 9}),
        history_assessed_at={8: ACCEPTED_AT + timedelta(seconds=11)},
    )
    store = SqlAlchemyAgentRunJobStore(session_factory)

    try:
        async with asyncio.timeout(10):
            first = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=20),
                lease_duration=timedelta(seconds=5),
            )
            reclaimed = await store.claim_next(
                now=ACCEPTED_AT + timedelta(seconds=26),
                lease_duration=timedelta(seconds=5),
            )

        assert first is not None
        assert [(turn.sequence, turn.role, turn.content) for turn in first.context_messages] == [
            (1, PromptRole.USER, "allowed-user"),
            (2, PromptRole.ASSISTANT, "successful-assistant"),
        ]
        context_contents = {turn.content for turn in first.context_messages}
        assert context_contents.isdisjoint(
            {
                "blocked-user-secret",
                "orphan-after-block",
                "escalated-user-secret",
                "unassessed-user-secret",
                "legacy-unassessed-assistant",
                "late-allowed-user",
                "late-assessed-assistant",
                "current-input",
                "future-assistant",
                "other-conversation-secret",
                "other-tenant-secret",
            }
        )

        assert reclaimed is not None
        assert reclaimed.attempt_count == 2
        assert reclaimed.context_messages == first.context_messages
        assert reclaimed.assistant_profile == first.assistant_profile
    finally:
        await _cleanup(event=event, targets=cleanup_targets)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_uses_empty_context_for_first_message() -> None:
    engine, session_factory = _database_resources()
    event, cleanup_targets = await _seed_job(
        session_factory=session_factory,
        history=(),
    )

    try:
        async with asyncio.timeout(10):
            job = await SqlAlchemyAgentRunJobStore(session_factory).claim_next(
                now=ACCEPTED_AT + timedelta(seconds=2),
                lease_duration=timedelta(seconds=5),
            )

        assert job is not None
        assert job.input_content == "current-input"
        assert job.context_messages == ()
        assert job.assistant_profile.ref.version == 1
    finally:
        await _cleanup(event=event, targets=cleanup_targets)
        await engine.dispose()
