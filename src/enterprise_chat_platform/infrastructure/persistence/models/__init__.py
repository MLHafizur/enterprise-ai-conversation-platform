"""Registry for every SQLAlchemy persistence model."""

from sqlalchemy import MetaData

from enterprise_chat_platform.infrastructure.persistence.metadata import Base
from enterprise_chat_platform.infrastructure.persistence.models.agent_run import (
    AgentRunRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.conversation import (
    ConversationRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.idempotency_receipt import (
    IdempotencyReceiptRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.message import (
    MessageRecord,
)


def registered_model_metadata() -> MetaData:
    """Return metadata after importing every persistence model."""
    return Base.metadata


__all__ = [
    "AgentRunRecord",
    "ConversationRecord",
    "IdempotencyReceiptRecord",
    "MessageRecord",
    "registered_model_metadata",
]
