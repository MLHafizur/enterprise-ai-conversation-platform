"""Registry for every SQLAlchemy persistence model."""

from sqlalchemy import MetaData

from enterprise_chat_platform.infrastructure.persistence.metadata import Base
from enterprise_chat_platform.infrastructure.persistence.models.conversation import (
    ConversationRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.message import (
    MessageRecord,
)


def registered_model_metadata() -> MetaData:
    """Return metadata after importing every persistence model."""
    return Base.metadata


__all__ = [
    "ConversationRecord",
    "MessageRecord",
    "registered_model_metadata",
]
