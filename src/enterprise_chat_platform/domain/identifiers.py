"""Strongly typed identifiers used by the domain."""

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid4

from enterprise_chat_platform.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class _UUIDIdentifier:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise DomainValidationError(f"{type(self).__name__} must contain a UUID")

        if self.value.int == 0:
            raise DomainValidationError(f"{type(self).__name__} cannot contain the nil UUID")

    @classmethod
    def new(cls) -> Self:
        """Create an identifier using a server-generated UUID4."""
        return cls(uuid4())

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Parse an external string into this specific identifier type."""
        try:
            value = UUID(raw)
        except ValueError as exc:
            raise DomainValidationError(f"Invalid {cls.__name__}") from exc

        return cls(value)

    def __str__(self) -> str:
        return str(self.value)


class TenantId(_UUIDIdentifier):
    """Uniquely identifies a tenant."""

    __slots__ = ()


class UserId(_UUIDIdentifier):
    """Uniquely identifies an authenticated user."""

    __slots__ = ()


class ConversationId(_UUIDIdentifier):
    """Uniquely identifies a conversation."""

    __slots__ = ()


class MessageId(_UUIDIdentifier):
    """Uniquely identifies an immutable message."""

    __slots__ = ()


class AgentRunId(_UUIDIdentifier):
    """Uniquely identifies an agent execution attempt."""

    __slots__ = ()


class AssistantProfileId(_UUIDIdentifier):
    """Uniquely identifies one tenant-scoped assistant profile."""

    __slots__ = ()


class EventId(_UUIDIdentifier):
    """Uniquely identifiers an application or integration event."""

    __slots__ = ()
