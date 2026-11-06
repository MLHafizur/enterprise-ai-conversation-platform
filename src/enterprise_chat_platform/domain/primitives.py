"""Validated values shared by domain entities."""

from dataclasses import dataclass
from datetime import UTC, datetime

from enterprise_chat_platform.domain.errors import DomainValidationError

MAX_IDEMPOTENCY_KEY_LENGTH = 128


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """Opaque, case-sensitive identifier for safely retryable commands."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise DomainValidationError("Idempotency key cannot be empty")

        if self.value != self.value.strip():
            raise DomainValidationError("Idempotency key cannot start or end with whitespace")

        if len(self.value) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise DomainValidationError(
                f"Idempotency key cannot exceed {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
            )

        if not self.value.isprintable():
            raise DomainValidationError("Idempotency key cannot contain control characters")

    def __str__(self) -> str:
        return self.value


def normalize_utc(
    value: datetime,
    *,
    field_name: str = "timestamp",
) -> datetime:
    """Validate an aware datetime and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")

    return value.astimezone(UTC)
