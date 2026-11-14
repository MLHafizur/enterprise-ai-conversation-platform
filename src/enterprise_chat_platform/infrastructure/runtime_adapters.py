"""Production adapters for application runtime ports."""

from datetime import UTC, datetime

from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    EventId,
    MessageId,
)


class UtcSystemClock:
    """Read the current wall-clock time as an aware UTC value."""

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)


class UuidTurnIds:
    """Generate server-controlled UUID4 identifiers for an accepted turn."""

    @staticmethod
    def new_message_id() -> MessageId:
        return MessageId.new()

    @staticmethod
    def new_agent_run_id() -> AgentRunId:
        return AgentRunId.new()

    @staticmethod
    def new_event_id() -> EventId:
        return EventId.new()


class OwnerOnlyConversationWriteAuthorizer:
    """Allow writes only by the conversation owner in the same tenant."""

    @staticmethod
    async def can_write(
        *,
        actor: ActorContext,
        conversation: Conversation,
    ) -> bool:
        return (
            actor.tenant_id == conversation.tenant_id and actor.actor_id == conversation.created_by
        )
