from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.primitives import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    IdempotencyKey,
    normalize_utc,
)


class _MissingOffsetTimezone(tzinfo):
    """A timezone object that does not provide an actual UTC offset."""

    def utcoffset(self, _dt: datetime | None) -> None:
        return None

    def dst(self, _dt: datetime | None) -> None:
        return None

    def tzname(self, _dt: datetime | None) -> None:
        return None


def test_idempotency_key_is_opaque_and_case_sensitive() -> None:
    uppercase = IdempotencyKey("Request-AbC")
    lowercase = IdempotencyKey("request-abc")

    assert uppercase != lowercase
    assert str(uppercase) == "Request-AbC"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " leading-space",
        "trailing-space ",
        "embedded\ncontrol",
    ],
)
def test_idempotency_key_rejects_invalid_value(raw: str) -> None:
    with pytest.raises(DomainValidationError):
        IdempotencyKey(raw)


def test_idempotency_key_accepts_maximum_length() -> None:
    raw = "a" * MAX_IDEMPOTENCY_KEY_LENGTH

    key = IdempotencyKey(raw)

    assert key.value == raw


def test_idempotency_key_rejects_oversized_value() -> None:
    with pytest.raises(
        DomainValidationError,
        match=f"cannot exceed {MAX_IDEMPOTENCY_KEY_LENGTH} characters",
    ):
        IdempotencyKey("a" * (MAX_IDEMPOTENCY_KEY_LENGTH + 1))


def test_idempotency_key_is_immutable() -> None:
    key = IdempotencyKey("request-123")

    with pytest.raises(FrozenInstanceError):
        # Intentionally exercise the runtime immutability guard.
        setattr(key, "value", "changed")  # noqa: B010


def test_normalize_utc_rejects_naive_datetime() -> None:
    naive = datetime(2026, 11, 6, 12, 0)

    with pytest.raises(
        DomainValidationError,
        match="created_at must be timezone-aware",
    ):
        normalize_utc(naive, field_name="created_at")


def test_normalize_utc_rejects_timezone_without_offset() -> None:
    timestamp = datetime(
        2026,
        11,
        6,
        12,
        0,
        tzinfo=_MissingOffsetTimezone(),
    )

    with pytest.raises(
        DomainValidationError,
        match="timestamp must be timezone-aware",
    ):
        normalize_utc(timestamp)


def test_normalize_utc_converts_aware_datetime() -> None:
    eastern = timezone(timedelta(hours=-5))
    local_time = datetime(2026, 11, 6, 12, 0, tzinfo=eastern)

    normalized = normalize_utc(local_time)

    assert normalized == datetime(2026, 11, 6, 17, 0, tzinfo=UTC)
    assert normalized.tzinfo is UTC


def test_normalize_utc_preserves_utc_instant() -> None:
    timestamp = datetime(2026, 11, 6, 17, 0, tzinfo=UTC)

    normalized = normalize_utc(timestamp)

    assert normalized == timestamp
    assert normalized.tzinfo is UTC
