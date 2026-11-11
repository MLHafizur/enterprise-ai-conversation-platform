from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)


def test_new_identifier_uses_uuid4() -> None:
    identifier = TenantId.new()

    assert isinstance(identifier.value, UUID)
    assert identifier.value.version == 4


def test_identifier_round_trip_preserves_value() -> None:
    original = ConversationId.new()

    parsed = ConversationId.parse(str(original))

    assert parsed == original
    assert str(parsed) == str(original.value)


def test_identifier_wrappers_remain_distinct_types() -> None:
    value = uuid4()

    identifiers = (
        TenantId(value),
        UserId(value),
        ConversationId(value),
        MessageId(value),
        AgentRunId(value),
        EventId(value),
    )

    assert len(set(identifiers)) == len(identifiers)


def test_identifier_is_immutable() -> None:
    identifier = MessageId.new()

    with pytest.raises(FrozenInstanceError):
        # Intentionally exercise the runtime immutability guard.
        setattr(identifier, "value", uuid4())  # noqa: B010


def test_identifier_rejects_malformed_uuid() -> None:
    with pytest.raises(
        DomainValidationError,
        match="^Invalid TenantId$",
    ):
        TenantId.parse("not-a-uuid")


def test_identifier_rejects_nil_uuid() -> None:
    with pytest.raises(
        DomainValidationError,
        match="TenantId cannot contain the nil UUID",
    ):
        TenantId(UUID(int=0))
