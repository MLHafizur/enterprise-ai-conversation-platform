from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import inspect

from enterprise_chat_platform.application.accept_user_turn import (
    ACCEPT_USER_TURN_OPERATION,
    AcceptUserTurnResult,
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.mappers.idempotency_receipt import (
    idempotency_receipt_from_record,
    idempotency_receipt_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    IdempotencyReceiptRecord,
)

TENANT_UUID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_UUID = UUID("22222222-2222-4222-8222-222222222222")
MESSAGE_UUID = UUID("33333333-3333-4333-8333-333333333333")
AGENT_RUN_UUID = UUID("44444444-4444-4444-8444-444444444444")
OTHER_CONVERSATION_UUID = UUID("55555555-5555-4555-8555-555555555555")
IDEMPOTENCY_KEY = "Request-AbC-001"
REQUEST_FINGERPRINT = "a" * 64
ACCEPTED_AT = datetime(2026, 11, 13, 16, 0, tzinfo=UTC)
OFFSET_ACCEPTED_AT = datetime(
    2026,
    11,
    13,
    11,
    0,
    tzinfo=timezone(timedelta(hours=-5)),
)


def _receipt() -> IdempotencyReceipt:
    scope = IdempotencyScope(
        tenant_id=TenantId(TENANT_UUID),
        conversation_id=ConversationId(CONVERSATION_UUID),
        key=IdempotencyKey(IDEMPOTENCY_KEY),
    )
    result = AcceptUserTurnResult(
        conversation_id=ConversationId(CONVERSATION_UUID),
        conversation_version=7,
        message_id=MessageId(MESSAGE_UUID),
        message_sequence=7,
        agent_run_id=AgentRunId(AGENT_RUN_UUID),
        accepted_at=ACCEPTED_AT,
        replayed=False,
    )

    return IdempotencyReceipt(
        scope=scope,
        request_fingerprint=REQUEST_FINGERPRINT,
        result=result,
    )


def _record(
    *,
    operation: str = ACCEPT_USER_TURN_OPERATION,
    request_fingerprint: str = REQUEST_FINGERPRINT,
    conversation_version: int = 7,
    message_sequence: int = 7,
    accepted_at: datetime = ACCEPTED_AT,
) -> IdempotencyReceiptRecord:
    return IdempotencyReceiptRecord(
        tenant_id=TENANT_UUID,
        conversation_id=CONVERSATION_UUID,
        operation=operation,
        idempotency_key=IDEMPOTENCY_KEY,
        request_fingerprint=request_fingerprint,
        conversation_version=conversation_version,
        message_id=MESSAGE_UUID,
        message_sequence=message_sequence,
        agent_run_id=AGENT_RUN_UUID,
        accepted_at=accepted_at,
    )


def test_receipt_maps_to_new_record_without_losing_information() -> None:
    record = idempotency_receipt_to_new_record(_receipt())

    assert isinstance(record, IdempotencyReceiptRecord)
    assert record.tenant_id == TENANT_UUID
    assert record.conversation_id == CONVERSATION_UUID
    assert record.operation == ACCEPT_USER_TURN_OPERATION
    assert record.idempotency_key == IDEMPOTENCY_KEY
    assert record.request_fingerprint == REQUEST_FINGERPRINT
    assert record.conversation_version == 7
    assert record.message_id == MESSAGE_UUID
    assert record.message_sequence == 7
    assert record.agent_run_id == AGENT_RUN_UUID
    assert record.accepted_at == ACCEPTED_AT
    assert not hasattr(record, "replayed")


def test_record_maps_to_typed_original_receipt() -> None:
    receipt = idempotency_receipt_from_record(_record())

    assert receipt == _receipt()
    assert isinstance(receipt.scope.tenant_id, TenantId)
    assert isinstance(receipt.scope.conversation_id, ConversationId)
    assert isinstance(receipt.scope.key, IdempotencyKey)
    assert isinstance(receipt.result.message_id, MessageId)
    assert isinstance(receipt.result.agent_run_id, AgentRunId)
    assert receipt.result.replayed is False


def test_receipt_mapping_round_trip_preserves_original_result() -> None:
    original = _receipt()

    restored = idempotency_receipt_from_record(idempotency_receipt_to_new_record(original))

    assert restored == original


def test_receipt_mapper_returns_unsaved_transient_record() -> None:
    record = idempotency_receipt_to_new_record(_receipt())

    state = inspect(record)

    assert state.transient is True
    assert state.session is None


def test_forward_mapping_rejects_conversation_mismatch() -> None:
    receipt = _receipt()
    mismatched_result = replace(
        receipt.result,
        conversation_id=ConversationId(OTHER_CONVERSATION_UUID),
    )
    mismatched_receipt = replace(
        receipt,
        result=mismatched_result,
    )

    with pytest.raises(
        ValueError,
        match="conversation",
    ):
        idempotency_receipt_to_new_record(mismatched_receipt)


def test_forward_mapping_rejects_replayed_result() -> None:
    receipt = _receipt()
    replayed_result = replace(
        receipt.result,
        replayed=True,
    )
    replayed_receipt = replace(
        receipt,
        result=replayed_result,
    )

    with pytest.raises(
        ValueError,
        match="replayed",
    ):
        idempotency_receipt_to_new_record(replayed_receipt)


def test_reverse_mapping_rejects_unsupported_operation() -> None:
    record = _record(
        operation="delete_conversation.v1",
    )

    with pytest.raises(
        ValueError,
        match="Unsupported idempotency operation",
    ):
        idempotency_receipt_from_record(record)


@pytest.mark.parametrize(
    "request_fingerprint",
    [
        "a" * 63,
        "A" * 64,
        "g" * 64,
    ],
    ids=[
        "wrong-length",
        "uppercase",
        "non-hexadecimal",
    ],
)
def test_forward_mapping_rejects_invalid_request_fingerprint(
    request_fingerprint: str,
) -> None:
    receipt = replace(
        _receipt(),
        request_fingerprint=request_fingerprint,
    )

    with pytest.raises(
        ValueError,
        match="fingerprint",
    ):
        idempotency_receipt_to_new_record(receipt)


def test_reverse_mapping_rejects_invalid_request_fingerprint() -> None:
    record = _record(
        request_fingerprint="g" * 64,
    )

    with pytest.raises(
        ValueError,
        match="fingerprint",
    ):
        idempotency_receipt_from_record(record)


@pytest.mark.parametrize(
    ("conversation_version", "message_sequence"),
    [
        (0, 7),
        (-1, 7),
        (7, 0),
        (7, -1),
    ],
)
def test_forward_mapping_rejects_nonpositive_result_numbers(
    conversation_version: int,
    message_sequence: int,
) -> None:
    receipt = _receipt()
    invalid_result = replace(
        receipt.result,
        conversation_version=conversation_version,
        message_sequence=message_sequence,
    )
    invalid_receipt = replace(
        receipt,
        result=invalid_result,
    )

    with pytest.raises(
        ValueError,
        match="positive",
    ):
        idempotency_receipt_to_new_record(invalid_receipt)


@pytest.mark.parametrize(
    ("conversation_version", "message_sequence"),
    [
        (0, 7),
        (-1, 7),
        (7, 0),
        (7, -1),
    ],
)
def test_reverse_mapping_rejects_nonpositive_result_numbers(
    conversation_version: int,
    message_sequence: int,
) -> None:
    record = _record(
        conversation_version=conversation_version,
        message_sequence=message_sequence,
    )

    with pytest.raises(
        ValueError,
        match="positive",
    ):
        idempotency_receipt_from_record(record)


def test_forward_mapping_normalizes_accepted_at_to_utc() -> None:
    receipt = _receipt()
    offset_result = replace(
        receipt.result,
        accepted_at=OFFSET_ACCEPTED_AT,
    )
    offset_receipt = replace(
        receipt,
        result=offset_result,
    )

    record = idempotency_receipt_to_new_record(offset_receipt)

    assert record.accepted_at == ACCEPTED_AT
    assert record.accepted_at.tzinfo is UTC


def test_reverse_mapping_normalizes_accepted_at_to_utc() -> None:
    receipt = idempotency_receipt_from_record(_record(accepted_at=OFFSET_ACCEPTED_AT))

    assert receipt.result.accepted_at == ACCEPTED_AT
    assert receipt.result.accepted_at.tzinfo is UTC


def test_forward_mapping_rejects_naive_accepted_at() -> None:
    receipt = _receipt()
    naive_result = replace(
        receipt.result,
        accepted_at=datetime(2026, 11, 13, 16, 0),
    )
    naive_receipt = replace(
        receipt,
        result=naive_result,
    )

    with pytest.raises(
        DomainValidationError,
        match="idempotency_receipt.accepted_at must be timezone-aware",
    ):
        idempotency_receipt_to_new_record(naive_receipt)


def test_reverse_mapping_rejects_naive_accepted_at() -> None:
    record = _record(
        accepted_at=datetime(2026, 11, 13, 16, 0),
    )

    with pytest.raises(
        DomainValidationError,
        match="idempotency_receipt.accepted_at must be timezone-aware",
    ):
        idempotency_receipt_from_record(record)
