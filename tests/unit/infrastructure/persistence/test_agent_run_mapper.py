from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import inspect

from enterprise_chat_platform.domain.agent_run import (
    AgentRun,
    AgentRunFailure,
    AgentRunStatus,
)
from enterprise_chat_platform.domain.assistant_profile import AssistantProfileRef
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    AssistantProfileId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.agent_run import (
    agent_run_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
)

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_UUID = UUID("22222222-2222-4222-8222-222222222222")
INPUT_MESSAGE_UUID = UUID("33333333-3333-4333-8333-333333333333")
OUTPUT_MESSAGE_UUID = UUID("44444444-4444-4444-8444-444444444444")
AGENT_RUN_UUID = UUID("55555555-5555-4555-8555-555555555555")
ASSISTANT_PROFILE_UUID = UUID("66666666-6666-4666-8666-666666666666")

CREATED_AT = datetime(2026, 11, 13, 15, 0, tzinfo=UTC)
STARTED_AT = CREATED_AT + timedelta(seconds=1)
FINISHED_AT = CREATED_AT + timedelta(seconds=2)


def _queued_run() -> AgentRun:
    return AgentRun(
        id=AgentRunId(AGENT_RUN_UUID),
        tenant_id=TenantId(TENANT_UUID),
        conversation_id=ConversationId(CONVERSATION_UUID),
        input_message_id=MessageId(INPUT_MESSAGE_UUID),
        assistant_profile_ref=AssistantProfileRef(
            id=AssistantProfileId(ASSISTANT_PROFILE_UUID),
            version=3,
        ),
        created_at=CREATED_AT,
    )


def test_queued_agent_run_maps_every_base_field() -> None:
    record = agent_run_to_new_record(_queued_run())

    assert isinstance(record, AgentRunRecord)
    assert record.tenant_id == TENANT_UUID
    assert record.id == AGENT_RUN_UUID
    assert record.conversation_id == CONVERSATION_UUID
    assert record.input_message_id == INPUT_MESSAGE_UUID
    assert record.assistant_profile_id == ASSISTANT_PROFILE_UUID
    assert record.assistant_profile_version == 3
    assert record.status == AgentRunStatus.QUEUED.value
    assert record.created_at == CREATED_AT

    assert record.started_at is None
    assert record.finished_at is None
    assert record.output_message_id is None
    assert record.failure_code is None
    assert record.failure_safe_message is None


def test_succeeded_agent_run_maps_output_and_timestamps() -> None:
    succeeded = (
        _queued_run()
        .start(at=STARTED_AT)
        .succeed(
            output_message_id=MessageId(OUTPUT_MESSAGE_UUID),
            at=FINISHED_AT,
        )
    )

    record = agent_run_to_new_record(succeeded)

    assert record.status == AgentRunStatus.SUCCEEDED.value
    assert record.started_at == STARTED_AT
    assert record.finished_at == FINISHED_AT
    assert record.output_message_id == OUTPUT_MESSAGE_UUID
    assert record.failure_code is None
    assert record.failure_safe_message is None


def test_failed_agent_run_flattens_failure_information() -> None:
    failed = _queued_run().fail(
        failure=AgentRunFailure(
            code="provider_timeout",
            safe_message="The AI provider timed out.",
        ),
        at=FINISHED_AT,
    )

    record = agent_run_to_new_record(failed)

    assert record.status == AgentRunStatus.FAILED.value
    assert record.started_at is None
    assert record.finished_at == FINISHED_AT
    assert record.output_message_id is None
    assert record.failure_code == "provider_timeout"
    assert record.failure_safe_message == "The AI provider timed out."


def test_agent_run_mapper_returns_unsaved_transient_record() -> None:
    record = agent_run_to_new_record(_queued_run())

    state = inspect(record)

    assert state.transient is True
    assert state.session is None
