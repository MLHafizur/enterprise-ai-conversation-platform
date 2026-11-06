"""Domain-specific exception hierarchy."""


class DomainError(Exception):
    """Base class for expected domain failures."""


class DomainValidationError(DomainError):
    """Raised when a value violates a domain invariant."""


class TenantMismatchError(DomainError):
    """Raised when related entities belong to different tenants."""


class InvalidStateTransitionError(DomainError):
    """Raised when an entity cannot enter the requested state."""
