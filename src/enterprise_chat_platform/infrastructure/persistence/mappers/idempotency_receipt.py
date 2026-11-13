"""Map idempotency receipts between application and persistence forms."""

import re

from enterprise_chat_platform.application.accept_user_turn import (
    ACCEPT_USER_TURN_OPERATION,
    AcceptUserTurnResult,
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.domain.primitives import (
    IdempotencyKey,
    normalize_utc,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    IdempotencyReceiptRecord,
)


def _validate_request_fingerprint(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(
            "Request fingerprint must contain exactly 64 lowercase hexadecimal characters"
        )


def _validate_result_numbers(
    *,
    conversation_version: int,
    message_sequence: int,
) -> None:
    if conversation_version <= 0:
        raise ValueError("Conversation version must be positive")

    if message_sequence <= 0:
        raise ValueError("Message sequence must be positive")


def idempotency_receipt_to_new_record(
    receipt: IdempotencyReceipt,
) -> IdempotencyReceiptRecord:
    """Create a transient database record from an original receipt."""
    _validate_request_fingerprint(receipt.request_fingerprint)
    _validate_result_numbers(
        conversation_version=receipt.result.conversation_version,
        message_sequence=receipt.result.message_sequence,
    )
    if receipt.scope.conversation_id != receipt.result.conversation_id:
        raise ValueError(
            "Idempotency receipt scope and result must reference the same conversation"
        )
    if receipt.result.replayed:
        raise ValueError("A replayed result cannot be persisted as an original receipt")
    accepted_at = normalize_utc(
        receipt.result.accepted_at,
        field_name="idempotency_receipt.accepted_at",
    )
    return IdempotencyReceiptRecord(
        tenant_id=receipt.scope.tenant_id.value,
        conversation_id=receipt.scope.conversation_id.value,
        operation=receipt.scope.operation,
        idempotency_key=receipt.scope.key.value,
        request_fingerprint=receipt.request_fingerprint,
        conversation_version=receipt.result.conversation_version,
        message_id=receipt.result.message_id.value,
        message_sequence=receipt.result.message_sequence,
        agent_run_id=receipt.result.agent_run_id.value,
        accepted_at=accepted_at,
    )


def idempotency_receipt_from_record(
    record: IdempotencyReceiptRecord,
) -> IdempotencyReceipt:
    """Rebuild the original typed receipt from a database record."""
    if record.operation != ACCEPT_USER_TURN_OPERATION:
        raise ValueError(f"Unsupported idempotency operation: {record.operation}")
    _validate_request_fingerprint(record.request_fingerprint)
    _validate_result_numbers(
        conversation_version=record.conversation_version,
        message_sequence=record.message_sequence,
    )
    accepted_at = normalize_utc(
        record.accepted_at,
        field_name="idempotency_receipt.accepted_at",
    )
    conversation_id = ConversationId(record.conversation_id)

    scope = IdempotencyScope(
        tenant_id=TenantId(record.tenant_id),
        conversation_id=conversation_id,
        key=IdempotencyKey(record.idempotency_key),
    )
    result = AcceptUserTurnResult(
        conversation_id=conversation_id,
        conversation_version=record.conversation_version,
        message_id=MessageId(record.message_id),
        message_sequence=record.message_sequence,
        agent_run_id=AgentRunId(record.agent_run_id),
        accepted_at=accepted_at,
        replayed=False,
    )

    return IdempotencyReceipt(
        scope=scope,
        request_fingerprint=record.request_fingerprint,
        result=result,
    )
