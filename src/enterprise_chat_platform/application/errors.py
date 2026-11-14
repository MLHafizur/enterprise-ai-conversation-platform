"""Errors raised while coordinating application use cases."""


class ApplicationError(Exception):
    """Base class for expected application failures."""


class ConversationNotFoundError(ApplicationError):
    """Raised when a conversation is missing or unavailable to the actor."""


class IdempotencyConflictError(ApplicationError):
    """Raised when an idempotency key is reused for another request."""


class ConversationConcurrencyError(ApplicationError):
    """Raised when another writer changes a conversation first."""
