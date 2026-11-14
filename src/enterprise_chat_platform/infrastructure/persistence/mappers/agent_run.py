"""Map agent runs from domain objects to persistence records."""

from enterprise_chat_platform.domain.agent_run import (
    AgentRun,
    AgentRunFailure,
    AgentRunStatus,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
)


def agent_run_to_new_record(
    agent_run: AgentRun,
) -> AgentRunRecord:
    """Create a new transient database record from a domain agent run."""
    output_message_id = (
        agent_run.output_message_id.value if agent_run.output_message_id is not None else None
    )
    failure_code = agent_run.failure.code if agent_run.failure is not None else None
    failure_safe_message = agent_run.failure.safe_message if agent_run.failure is not None else None

    return AgentRunRecord(
        tenant_id=agent_run.tenant_id.value,
        id=agent_run.id.value,
        conversation_id=agent_run.conversation_id.value,
        input_message_id=agent_run.input_message_id.value,
        status=agent_run.status.value,
        created_at=agent_run.created_at,
        started_at=agent_run.started_at,
        finished_at=agent_run.finished_at,
        output_message_id=output_message_id,
        failure_code=failure_code,
        failure_safe_message=failure_safe_message,
    )


def agent_run_from_record(record: AgentRunRecord) -> AgentRun:
    """Rebuild a validated agent-run snapshot from a database record."""
    failure = None
    if record.failure_code is not None and record.failure_safe_message is not None:
        failure = AgentRunFailure(
            code=record.failure_code,
            safe_message=record.failure_safe_message,
        )

    output_message_id = (
        MessageId(record.output_message_id) if record.output_message_id is not None else None
    )

    return AgentRun(
        id=AgentRunId(record.id),
        tenant_id=TenantId(record.tenant_id),
        conversation_id=ConversationId(record.conversation_id),
        input_message_id=MessageId(record.input_message_id),
        status=AgentRunStatus(record.status),
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        output_message_id=output_message_id,
        failure=failure,
    )
