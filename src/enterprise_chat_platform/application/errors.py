"""Errors raised while coordinating application use cases."""


class ApplicationError(Exception):
    """Base class for expected application failures."""


class ConversationNotFoundError(ApplicationError):
    """Raised when a conversation is missing or unavailable to the actor."""


class IdempotencyConflictError(ApplicationError):
    """Raised when an idempotency key is reused for another request."""


class ConversationConcurrencyError(ApplicationError):
    """Raised when another writer changes a conversation first."""


class HumanReviewNotFoundError(ApplicationError):
    """Raised when a review case is missing from the actor's tenant."""


class HumanReviewConflictError(ApplicationError):
    """Raised when a review case changed before a requested transition."""


class AssistantProfileNotFoundError(ApplicationError):
    """Raised when a tenant-scoped assistant profile cannot be resolved."""


class AssistantProfileConflictError(ApplicationError):
    """Raised when assistant profile state changed before an admin write."""
