"""Registry for every SQLAlchemy persistence model."""

from sqlalchemy import MetaData

from enterprise_chat_platform.infrastructure.persistence.metadata import Base
from enterprise_chat_platform.infrastructure.persistence.models.agent_run import (
    AgentRunRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.conversation import (
    ConversationRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.human_review import (
    HumanReviewRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.idempotency_receipt import (
    IdempotencyReceiptRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.input_policy_assessment import (
    InputPolicyAssessmentRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.message import (
    MessageRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    OutboxEventRecord,
)


def registered_model_metadata() -> MetaData:
    """Return metadata after importing every persistence model."""
    return Base.metadata


__all__ = [
    "AgentRunRecord",
    "ConversationRecord",
    "IdempotencyReceiptRecord",
    "HumanReviewRecord",
    "MessageRecord",
    "OutboxEventRecord",
    "registered_model_metadata",
    "InputPolicyAssessmentRecord",
]
