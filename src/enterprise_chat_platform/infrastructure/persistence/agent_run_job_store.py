"""PostgreSQL-backed short transactions for recoverable agent execution."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from enterprise_chat_platform.application.accept_user_turn import (
    AGENT_RUN_QUEUED_EVENT_TYPE,
)
from enterprise_chat_platform.application.errors import ConversationConcurrencyError
from enterprise_chat_platform.application.process_agent_run import (
    INPUT_POLICY_BLOCKED_FAILURE,
    INPUT_POLICY_ESCALATED_FAILURE,
    ClaimedAgentRun,
)
from enterprise_chat_platform.application.realtime_events import (
    AGENT_RUN_TERMINAL_EVENT_TYPE,
    AGENT_RUN_TERMINAL_SCHEMA_VERSION,
    AgentRunTerminalEvent,
    agent_run_terminal_payload,
)
from enterprise_chat_platform.domain.agent_run import (
    AgentRun,
    AgentRunFailure,
    AgentRunStatus,
)
from enterprise_chat_platform.domain.chat import MessageRole
from enterprise_chat_platform.domain.governance import PolicyAction, PolicyAssessment
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey, normalize_utc
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.agent_run import (
    agent_run_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    ConversationRecord,
    MessageRecord,
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    OUTBOX_AGGREGATE_TYPE,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.input_policy_assessment import (
    SqlAlchemyInputPolicyAssessmentRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.message import (
    SqlAlchemyMessageRepository,
)


class SqlAlchemyAgentRunJobStore:
    """Fence worker attempts with leases and commit terminal changes atomically."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
    ) -> None:
        self._session_factory = session_factory

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedAgentRun | None:
        """Lease one due row using SKIP LOCKED and mark its run as running."""
        claimed_at = normalize_utc(now, field_name="outbox_claim.claimed_at")
        if lease_duration <= timedelta(0):
            raise ValueError("Lease duration must be positive")

        async with self._session_factory() as session:
            async with session.begin():
                record = await _select_due_event_for_update(
                    session=session,
                    now=claimed_at,
                )
                if record is None:
                    return None

                lease_token = uuid4()
                record.attempt_count += 1
                record.last_attempt_at = claimed_at
                record.lease_token = lease_token
                record.lease_expires_at = claimed_at + lease_duration

                job = _job_from_record(
                    record=record,
                    lease_token=lease_token,
                    input_content="",
                )
                run_record = await _select_run_for_update(
                    session=session,
                    job=job,
                )
                run = agent_run_from_record(run_record)

                if run.status in _TERMINAL_RUN_STATUSES:
                    _publish_event(record, at=claimed_at)
                    return None

                if run.status is AgentRunStatus.QUEUED:
                    run = run.start(at=claimed_at)
                    _apply_run_snapshot(record=run_record, run=run)
                elif run.status is not AgentRunStatus.RUNNING:
                    raise RuntimeError("Unsupported agent-run state while claiming work")

                input_record = await _select_input_message(
                    session=session,
                    job=job,
                )
                if input_record.role != MessageRole.USER.value:
                    raise RuntimeError("Agent run input must reference a user message")

                return _job_from_record(
                    record=record,
                    lease_token=lease_token,
                    input_content=input_record.content,
                )

    async def complete(
        self,
        *,
        job: ClaimedAgentRun,
        output_message_id: MessageId,
        service_author_id: UserId,
        content: str,
        completed_at: datetime,
    ) -> bool:
        """Atomically append output, advance conversation, finish run, and publish."""
        completion_time = normalize_utc(
            completed_at,
            field_name="agent_run_completion.completed_at",
        )

        async with self._session_factory() as session:
            async with session.begin():
                event_record = await _lock_owned_event(
                    session=session,
                    job=job,
                    at=completion_time,
                )
                if event_record is None:
                    return False

                run_record = await _select_run_for_update(
                    session=session,
                    job=job,
                )
                run = agent_run_from_record(run_record)
                if run.status in _TERMINAL_RUN_STATUSES:
                    _publish_event(event_record, at=completion_time)
                    return True
                if run.status is not AgentRunStatus.RUNNING:
                    raise RuntimeError("Only a running agent run can complete")

                conversation_record = await _select_conversation_for_update(
                    session=session,
                    job=job,
                )
                conversation = conversation_from_record(conversation_record)
                effective_time = max(completion_time, conversation.updated_at)
                assistant_message = conversation.append_assistant_message(
                    trusted_tenant_id=job.tenant_id,
                    service_author_id=service_author_id,
                    message_id=output_message_id,
                    content=content,
                    idempotency_key=IdempotencyKey(f"agent-run:{job.agent_run_id}"),
                    at=effective_time,
                )

                message_repository = SqlAlchemyMessageRepository(session)
                message_repository.add(message=assistant_message.message)
                # No ORM relationship joins MessageRecord to AgentRunRecord.
                # Flush the FK target before assigning run.output_message_id;
                # both writes still belong to this same transaction.
                await session.flush()

                conversation_repository = SqlAlchemyConversationRepository(session)
                updated = await conversation_repository.update_if_version_matches(
                    expected_version=conversation.version,
                    updated_conversation=assistant_message.updated_conversation,
                )
                if not updated:
                    raise ConversationConcurrencyError(
                        "Conversation changed while completing the agent run"
                    )

                succeeded = run.succeed(
                    output_message_id=output_message_id,
                    at=max(effective_time, run.started_at or effective_time),
                )
                _apply_run_snapshot(record=run_record, run=succeeded)
                session.add(
                    _terminal_notification_record(
                        event=AgentRunTerminalEvent(
                            id=EventId.new(),
                            tenant_id=job.tenant_id,
                            conversation_id=job.conversation_id,
                            agent_run_id=job.agent_run_id,
                            status=succeeded.status,
                            output_message_id=output_message_id,
                            conversation_version=(assistant_message.updated_conversation.version),
                            message_sequence=assistant_message.message.sequence,
                            occurred_at=effective_time,
                        ),
                        aggregate_version=event_record.aggregate_version,
                    )
                )
                _publish_event(event_record, at=effective_time)
                await session.flush()
                return True

    async def apply_input_policy(
        self,
        *,
        job: ClaimedAgentRun,
        assessment: PolicyAssessment,
        assessed_at: datetime,
    ) -> PolicyAssessment | None:
        """Persist the first decision and apply it while the lease is still owned.

        An allow decision leaves the original queued event leased so the same
        worker may call the provider. Blocking and escalation decisions finish
        the run and publish that queued event in this same transaction.
        """
        assessment_time = normalize_utc(
            assessed_at,
            field_name="input_policy_application.assessed_at",
        )

        async with self._session_factory() as session:
            async with session.begin():
                event_record = await _lock_owned_event(
                    session=session,
                    job=job,
                    at=assessment_time,
                )
                if event_record is None:
                    return None

                run_record = await _select_run_for_update(
                    session=session,
                    job=job,
                )
                run = agent_run_from_record(run_record)
                if run.status is not AgentRunStatus.RUNNING:
                    raise RuntimeError("Only a running agent run can apply input policy")

                repository = SqlAlchemyInputPolicyAssessmentRepository(session)
                stored_assessment = await repository.insert_or_get(
                    tenant_id=job.tenant_id,
                    agent_run_id=job.agent_run_id,
                    assessment=assessment,
                    assessed_at=assessment_time,
                )

                if stored_assessment.action is PolicyAction.ALLOW:
                    # Do not clear the lease or publish the queued event: the
                    # caller still owns the provider-call phase of this job.
                    return stored_assessment

                failure = _INPUT_POLICY_FAILURES[stored_assessment.action]
                effective_time = max(
                    assessment_time,
                    run.started_at or run.created_at,
                )
                failed_run = run.fail(
                    failure=failure,
                    at=effective_time,
                )
                _apply_run_snapshot(record=run_record, run=failed_run)
                session.add(
                    _terminal_notification_record(
                        event=AgentRunTerminalEvent(
                            id=EventId.new(),
                            tenant_id=job.tenant_id,
                            conversation_id=job.conversation_id,
                            agent_run_id=job.agent_run_id,
                            status=failed_run.status,
                            output_message_id=None,
                            conversation_version=event_record.aggregate_version,
                            message_sequence=None,
                            occurred_at=effective_time,
                        ),
                        aggregate_version=event_record.aggregate_version,
                    )
                )
                _publish_event(event_record, at=effective_time)
                return stored_assessment

    async def reschedule(
        self,
        *,
        job: ClaimedAgentRun,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool:
        """Release an owned lease and make the event available after backoff."""
        attempt_time = normalize_utc(
            attempted_at,
            field_name="outbox_retry.attempted_at",
        )
        next_available_at = normalize_utc(
            available_at,
            field_name="outbox_retry.available_at",
        )
        if next_available_at < attempt_time:
            raise ValueError("Retry availability cannot precede the attempt")

        async with self._session_factory() as session:
            async with session.begin():
                record = await _lock_owned_event(
                    session=session,
                    job=job,
                    at=attempt_time,
                )
                if record is None:
                    return False

                record.available_at = max(next_available_at, record.occurred_at)
                _clear_lease(record)
                return True

    async def fail(
        self,
        *,
        job: ClaimedAgentRun,
        failure: AgentRunFailure,
        failed_at: datetime,
    ) -> bool:
        """Atomically make the run failed and finish its outbox event."""
        failure_time = normalize_utc(
            failed_at,
            field_name="agent_run_failure.failed_at",
        )

        async with self._session_factory() as session:
            async with session.begin():
                event_record = await _lock_owned_event(
                    session=session,
                    job=job,
                    at=failure_time,
                )
                if event_record is None:
                    return False

                run_record = await _select_run_for_update(
                    session=session,
                    job=job,
                )
                run = agent_run_from_record(run_record)
                if run.status not in _TERMINAL_RUN_STATUSES:
                    effective_time = max(
                        failure_time,
                        run.started_at or run.created_at,
                    )
                    run = run.fail(
                        failure=failure,
                        at=effective_time,
                    )
                    _apply_run_snapshot(record=run_record, run=run)
                    session.add(
                        _terminal_notification_record(
                            event=AgentRunTerminalEvent(
                                id=EventId.new(),
                                tenant_id=job.tenant_id,
                                conversation_id=job.conversation_id,
                                agent_run_id=job.agent_run_id,
                                status=run.status,
                                output_message_id=None,
                                conversation_version=event_record.aggregate_version,
                                message_sequence=None,
                                occurred_at=effective_time,
                            ),
                            aggregate_version=event_record.aggregate_version,
                        )
                    )

                _publish_event(
                    event_record,
                    at=max(failure_time, run.finished_at or failure_time),
                )
                return True


_TERMINAL_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)

_INPUT_POLICY_FAILURES = {
    PolicyAction.BLOCK: INPUT_POLICY_BLOCKED_FAILURE,
    PolicyAction.ESCALATE: INPUT_POLICY_ESCALATED_FAILURE,
}


def _pending_claim_statement(
    *,
    now: datetime,
) -> Select[OutboxEventRecord]:
    """Build the PostgreSQL locking query separately for contract testing."""
    return (
        select(OutboxEventRecord)
        .where(
            OutboxEventRecord.event_type == AGENT_RUN_QUEUED_EVENT_TYPE,
            OutboxEventRecord.published_at.is_(None),
            OutboxEventRecord.available_at <= now,
            or_(
                OutboxEventRecord.lease_token.is_(None),
                OutboxEventRecord.lease_expires_at <= now,
            ),
        )
        .order_by(
            OutboxEventRecord.available_at,
            OutboxEventRecord.occurred_at,
            OutboxEventRecord.tenant_id,
            OutboxEventRecord.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def _select_due_event_for_update(
    *,
    session: AsyncSession,
    now: datetime,
) -> OutboxEventRecord | None:
    result = await session.execute(_pending_claim_statement(now=now))
    return result.scalar_one_or_none()


async def _lock_owned_event(
    *,
    session: AsyncSession,
    job: ClaimedAgentRun,
    at: datetime,
) -> OutboxEventRecord | None:
    statement = (
        select(OutboxEventRecord)
        .where(
            OutboxEventRecord.tenant_id == job.tenant_id.value,
            OutboxEventRecord.id == job.event_id.value,
        )
        .with_for_update()
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if record is None or record.published_at is not None:
        return None
    if record.lease_token != job.lease_token:
        return None
    if record.lease_expires_at is None or record.lease_expires_at <= at:
        return None
    return record


async def _select_run_for_update(
    *,
    session: AsyncSession,
    job: ClaimedAgentRun,
) -> AgentRunRecord:
    statement = (
        select(AgentRunRecord)
        .where(
            AgentRunRecord.tenant_id == job.tenant_id.value,
            AgentRunRecord.id == job.agent_run_id.value,
            AgentRunRecord.conversation_id == job.conversation_id.value,
            AgentRunRecord.input_message_id == job.input_message_id.value,
        )
        .with_for_update()
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()
    if record is None:
        raise RuntimeError("Outbox event references a missing or mismatched agent run")
    return record


async def _select_input_message(
    *,
    session: AsyncSession,
    job: ClaimedAgentRun,
) -> MessageRecord:
    statement = select(MessageRecord).where(
        MessageRecord.tenant_id == job.tenant_id.value,
        MessageRecord.id == job.input_message_id.value,
        MessageRecord.conversation_id == job.conversation_id.value,
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()
    if record is None:
        raise RuntimeError("Agent run references a missing or mismatched input message")
    return record


async def _select_conversation_for_update(
    *,
    session: AsyncSession,
    job: ClaimedAgentRun,
) -> ConversationRecord:
    statement = (
        select(ConversationRecord)
        .where(
            ConversationRecord.tenant_id == job.tenant_id.value,
            ConversationRecord.id == job.conversation_id.value,
        )
        .with_for_update()
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()
    if record is None:
        raise RuntimeError("Agent run references a missing conversation")
    return record


def _job_from_record(
    *,
    record: OutboxEventRecord,
    lease_token: UUID,
    input_content: str,
) -> ClaimedAgentRun:
    payload = record.payload
    agent_run_id = _payload_uuid(payload=payload, key="agent_run_id")
    input_message_id = _payload_uuid(payload=payload, key="input_message_id")

    if record.schema_version != 1:
        raise RuntimeError("Unsupported queued-event schema version")

    return ClaimedAgentRun(
        event_id=EventId(record.id),
        tenant_id=TenantId(record.tenant_id),
        conversation_id=ConversationId(record.aggregate_id),
        agent_run_id=AgentRunId(agent_run_id),
        input_message_id=MessageId(input_message_id),
        input_content=input_content,
        lease_token=lease_token,
        attempt_count=record.attempt_count,
    )


def _payload_uuid(
    *,
    payload: dict[str, object],
    key: str,
) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f"Outbox payload field {key} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"Outbox payload field {key} must be a UUID string") from exc


def _apply_run_snapshot(
    *,
    record: AgentRunRecord,
    run: AgentRun,
) -> None:
    record.status = run.status.value
    record.started_at = run.started_at
    record.finished_at = run.finished_at
    record.output_message_id = (
        run.output_message_id.value if run.output_message_id is not None else None
    )
    record.failure_code = run.failure.code if run.failure is not None else None
    record.failure_safe_message = run.failure.safe_message if run.failure is not None else None


def _terminal_notification_record(
    *,
    event: AgentRunTerminalEvent,
    aggregate_version: int,
) -> OutboxEventRecord:
    """Create an initially pending realtime notification outbox row."""
    if aggregate_version <= 0:
        raise ValueError("Terminal notification aggregate version must be positive")

    return OutboxEventRecord(
        tenant_id=event.tenant_id.value,
        id=event.id.value,
        aggregate_type=OUTBOX_AGGREGATE_TYPE,
        aggregate_id=event.conversation_id.value,
        aggregate_version=aggregate_version,
        event_type=AGENT_RUN_TERMINAL_EVENT_TYPE,
        schema_version=AGENT_RUN_TERMINAL_SCHEMA_VERSION,
        payload=dict(agent_run_terminal_payload(event)),
        occurred_at=event.occurred_at,
        available_at=event.occurred_at,
        attempt_count=0,
        last_attempt_at=None,
        lease_token=None,
        lease_expires_at=None,
        published_at=None,
    )


def _publish_event(
    record: OutboxEventRecord,
    *,
    at: datetime,
) -> None:
    published_at = max(
        at,
        record.last_attempt_at or record.occurred_at,
    )
    record.published_at = published_at
    _clear_lease(record)


def _clear_lease(record: OutboxEventRecord) -> None:
    record.lease_token = None
    record.lease_expires_at = None
