from datetime import UTC, datetime, timedelta

import pytest

from enterprise_chat_platform.domain.agent_run import (
    AgentRun,
    AgentRunFailure,
    AgentRunStatus,
)
from enterprise_chat_platform.domain.assistant_profile import AssistantProfileRef
from enterprise_chat_platform.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    AssistantProfileId,
    ConversationId,
    MessageId,
    TenantId,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
CONVERSATION_ID = ConversationId.parse("22222222-2222-4222-8222-222222222222")
INPUT_MESSAGE_ID = MessageId.parse("33333333-3333-4333-8333-333333333333")
OUTPUT_MESSAGE_ID = MessageId.parse("44444444-4444-4444-8444-444444444444")
RUN_ID = AgentRunId.parse("55555555-5555-4555-8555-555555555555")
PROFILE_REF = AssistantProfileRef(
    id=AssistantProfileId.parse("66666666-6666-4666-8666-666666666666"),
    version=3,
)

CREATED_AT = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
STARTED_AT = CREATED_AT + timedelta(seconds=1)
FINISHED_AT = CREATED_AT + timedelta(seconds=2)


def _queued_run() -> AgentRun:
    return AgentRun(
        id=RUN_ID,
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        input_message_id=INPUT_MESSAGE_ID,
        assistant_profile_ref=PROFILE_REF,
        created_at=CREATED_AT,
    )


def _failure() -> AgentRunFailure:
    return AgentRunFailure(
        code="provider_timeout",
        safe_message="The AI provider timed out",
    )


def test_new_run_starts_queued() -> None:
    run = _queued_run()

    assert run.status is AgentRunStatus.QUEUED
    assert run.started_at is None
    assert run.finished_at is None
    assert run.output_message_id is None
    assert run.failure is None


def test_run_requires_typed_assistant_profile_reference() -> None:
    with pytest.raises(DomainValidationError, match="assistant profile reference"):
        AgentRun(
            id=RUN_ID,
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
            input_message_id=INPUT_MESSAGE_ID,
            assistant_profile_ref="profile-v1",  # type: ignore[arg-type]
            created_at=CREATED_AT,
        )


def test_start_returns_running_copy() -> None:
    queued = _queued_run()

    running = queued.start(at=STARTED_AT)

    assert queued.status is AgentRunStatus.QUEUED
    assert running.status is AgentRunStatus.RUNNING
    assert running.started_at == STARTED_AT
    assert running.finished_at is None
    assert running.id == queued.id
    assert running.tenant_id == queued.tenant_id
    assert running.conversation_id == queued.conversation_id
    assert running.input_message_id == queued.input_message_id
    assert running.assistant_profile_ref == PROFILE_REF


def test_running_run_can_succeed() -> None:
    running = _queued_run().start(at=STARTED_AT)

    succeeded = running.succeed(
        output_message_id=OUTPUT_MESSAGE_ID,
        at=FINISHED_AT,
    )

    assert succeeded.status is AgentRunStatus.SUCCEEDED
    assert succeeded.started_at == STARTED_AT
    assert succeeded.finished_at == FINISHED_AT
    assert succeeded.output_message_id == OUTPUT_MESSAGE_ID
    assert succeeded.failure is None
    assert succeeded.assistant_profile_ref == PROFILE_REF


def test_queued_run_can_fail_before_starting() -> None:
    queued = _queued_run()

    failed = queued.fail(
        failure=_failure(),
        at=FINISHED_AT,
    )

    assert failed.status is AgentRunStatus.FAILED
    assert failed.started_at is None
    assert failed.finished_at == FINISHED_AT
    assert failed.output_message_id is None
    assert failed.failure == _failure()
    assert failed.assistant_profile_ref == PROFILE_REF


def test_running_run_can_fail() -> None:
    running = _queued_run().start(at=STARTED_AT)

    failed = running.fail(
        failure=_failure(),
        at=FINISHED_AT,
    )

    assert failed.status is AgentRunStatus.FAILED
    assert failed.started_at == STARTED_AT
    assert failed.finished_at == FINISHED_AT
    assert failed.output_message_id is None
    assert failed.failure == _failure()
    assert failed.assistant_profile_ref == PROFILE_REF


def test_queued_run_can_be_cancelled() -> None:
    cancelled = _queued_run().cancel(at=FINISHED_AT)

    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.started_at is None
    assert cancelled.finished_at == FINISHED_AT
    assert cancelled.assistant_profile_ref == PROFILE_REF


def test_running_run_can_be_cancelled_after_worker_confirmation() -> None:
    running = _queued_run().start(at=STARTED_AT)

    cancelled = running.cancel(at=FINISHED_AT)

    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.started_at == STARTED_AT
    assert cancelled.finished_at == FINISHED_AT
    assert cancelled.assistant_profile_ref == PROFILE_REF


def test_queued_run_cannot_succeed() -> None:
    with pytest.raises(
        InvalidStateTransitionError,
        match="Cannot succeed agent run from queued",
    ):
        _queued_run().succeed(
            output_message_id=OUTPUT_MESSAGE_ID,
            at=FINISHED_AT,
        )


def test_terminal_run_rejects_all_further_transitions() -> None:
    succeeded = (
        _queued_run()
        .start(at=STARTED_AT)
        .succeed(
            output_message_id=OUTPUT_MESSAGE_ID,
            at=FINISHED_AT,
        )
    )

    with pytest.raises(
        InvalidStateTransitionError,
        match="Cannot start agent run from succeeded",
    ):
        succeeded.start(at=FINISHED_AT)

    with pytest.raises(
        InvalidStateTransitionError,
        match="Cannot succeed agent run from succeeded",
    ):
        succeeded.succeed(
            output_message_id=OUTPUT_MESSAGE_ID,
            at=FINISHED_AT,
        )

    with pytest.raises(
        InvalidStateTransitionError,
        match="Cannot fail agent run from succeeded",
    ):
        succeeded.fail(
            failure=_failure(),
            at=FINISHED_AT,
        )

    with pytest.raises(
        InvalidStateTransitionError,
        match="Cannot cancel agent run from succeeded",
    ):
        succeeded.cancel(at=FINISHED_AT)


def test_every_terminal_status_rejects_restart() -> None:
    running = _queued_run().start(at=STARTED_AT)

    terminal_runs = (
        running.succeed(
            output_message_id=OUTPUT_MESSAGE_ID,
            at=FINISHED_AT,
        ),
        running.fail(
            failure=_failure(),
            at=FINISHED_AT,
        ),
        running.cancel(at=FINISHED_AT),
    )

    for run in terminal_runs:
        with pytest.raises(InvalidStateTransitionError):
            run.start(at=FINISHED_AT)


def test_start_rejects_naive_timestamp() -> None:
    naive = datetime(2026, 11, 6, 15, 1)

    with pytest.raises(
        DomainValidationError,
        match="started_at must be timezone-aware",
    ):
        _queued_run().start(at=naive)


def test_start_rejects_timestamp_before_creation() -> None:
    earlier = CREATED_AT - timedelta(microseconds=1)

    with pytest.raises(
        DomainValidationError,
        match="started_at cannot precede created_at",
    ):
        _queued_run().start(at=earlier)


def test_completion_rejects_timestamp_before_start() -> None:
    running = _queued_run().start(at=STARTED_AT)
    earlier = STARTED_AT - timedelta(microseconds=1)

    with pytest.raises(
        DomainValidationError,
        match="finished_at cannot precede started_at",
    ):
        running.succeed(
            output_message_id=OUTPUT_MESSAGE_ID,
            at=earlier,
        )


@pytest.mark.parametrize(
    ("code", "safe_message"),
    [
        ("", "Safe message"),
        ("INVALID CODE", "Safe message"),
        ("provider_timeout", ""),
        ("provider_timeout", "unsafe\x00message"),
    ],
)
def test_failure_rejects_unsafe_values(
    code: str,
    safe_message: str,
) -> None:
    with pytest.raises(DomainValidationError):
        AgentRunFailure(
            code=code,
            safe_message=safe_message,
        )


def test_rehydration_rejects_inconsistent_succeeded_state() -> None:
    with pytest.raises(
        DomainValidationError,
        match="Succeeded agent run requires an output message",
    ):
        AgentRun(
            id=RUN_ID,
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
            input_message_id=INPUT_MESSAGE_ID,
            assistant_profile_ref=PROFILE_REF,
            status=AgentRunStatus.SUCCEEDED,
            created_at=CREATED_AT,
            started_at=STARTED_AT,
            finished_at=FINISHED_AT,
        )
