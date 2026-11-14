"""Map application events to transactional outbox records."""

from enterprise_chat_platform.application.accept_user_turn import (
    AgentRunQueuedEvent,
)
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.persistence.models import (
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    OUTBOX_AGGREGATE_TYPE,
)


def agent_run_queued_event_to_new_record(
    event: AgentRunQueuedEvent,
) -> OutboxEventRecord:
    """Create an initially pending outbox record for one queued run event."""
    if event.conversation_version <= 0:
        raise ValueError("Conversation version must be positive")

    occurred_at = normalize_utc(
        event.occurred_at,
        field_name="agent_run_queued_event.occurred_at",
    )

    return OutboxEventRecord(
        tenant_id=event.tenant_id.value,
        id=event.id.value,
        aggregate_type=OUTBOX_AGGREGATE_TYPE,
        aggregate_id=event.conversation_id.value,
        aggregate_version=event.conversation_version,
        event_type=event.event_type,
        schema_version=event.schema_version,
        payload={
            "agent_run_id": str(event.agent_run_id),
            "input_message_id": str(event.input_message_id),
        },
        occurred_at=occurred_at,
        available_at=occurred_at,
        attempt_count=0,
        last_attempt_at=None,
        lease_token=None,
        lease_expires_at=None,
        published_at=None,
    )
