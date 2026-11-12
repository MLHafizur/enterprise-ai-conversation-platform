from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from enterprise_chat_platform.domain.chat import Conversation, ConversationStatus
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.conversation import (
    conversation_from_record,
    conversation_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
)

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_UUID = UUID("22222222-2222-4222-8222-222222222222")
CREATOR_UUID = UUID("33333333-3333-4333-8333-333333333333")

CREATED_AT = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 11, 12, 14, 5, tzinfo=UTC)


def _conversation() -> Conversation:
    return Conversation(
        id=ConversationId(CONVERSATION_UUID),
        tenant_id=TenantId(TENANT_UUID),
        created_by=UserId(CREATOR_UUID),
        status=ConversationStatus.ARCHIVED,
        version=7,
        last_message_sequence=6,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )


def _record(
    *,
    tenant_id: UUID = TENANT_UUID,
    status: str = "archived",
    created_at: datetime = CREATED_AT,
    updated_at: datetime = UPDATED_AT,
) -> ConversationRecord:
    return ConversationRecord(
        tenant_id=tenant_id,
        id=CONVERSATION_UUID,
        created_by=CREATOR_UUID,
        status=status,
        version=7,
        last_message_sequence=6,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_conversation_maps_to_new_record() -> None:
    record = conversation_to_new_record(_conversation())

    assert isinstance(record, ConversationRecord)
    assert record.tenant_id == TENANT_UUID
    assert record.id == CONVERSATION_UUID
    assert record.created_by == CREATOR_UUID
    assert record.status == "archived"
    assert record.version == 7
    assert record.last_message_sequence == 6
    assert record.created_at == CREATED_AT
    assert record.updated_at == UPDATED_AT


def test_record_maps_to_conversation() -> None:
    conversation = conversation_from_record(_record())

    assert conversation == _conversation()
    assert isinstance(conversation.tenant_id, TenantId)
    assert isinstance(conversation.id, ConversationId)
    assert isinstance(conversation.created_by, UserId)
    assert conversation.status is ConversationStatus.ARCHIVED


def test_conversation_mapping_round_trip_preserves_every_field() -> None:
    original = _conversation()

    restored = conversation_from_record(
        conversation_to_new_record(original),
    )

    assert restored == original


def test_record_mapping_normalizes_aware_timestamp_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    record = _record(
        created_at=datetime(2026, 11, 12, 19, 30, tzinfo=offset),
        updated_at=datetime(2026, 11, 12, 19, 35, tzinfo=offset),
    )

    conversation = conversation_from_record(record)

    assert conversation.created_at == CREATED_AT
    assert conversation.updated_at == UPDATED_AT
    assert conversation.created_at.tzinfo is UTC
    assert conversation.updated_at.tzinfo is UTC


def test_record_mapping_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        conversation_from_record(_record(status="deleted"))


def test_record_mapping_rejects_naive_timestamp() -> None:
    naive_timestamp = datetime(2026, 11, 12, 14, 0)

    with pytest.raises(
        DomainValidationError,
        match="must be timezone-aware",
    ):
        conversation_from_record(
            _record(
                created_at=naive_timestamp,
                updated_at=naive_timestamp,
            )
        )


def test_record_mapping_rejects_nil_tenant_id() -> None:
    with pytest.raises(
        DomainValidationError,
        match="TenantId cannot contain the nil UUID",
    ):
        conversation_from_record(
            _record(tenant_id=UUID(int=0)),
        )
