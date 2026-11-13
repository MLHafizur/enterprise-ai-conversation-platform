"""SQLAlchemy repository for durable idempotency receipts."""

import hashlib

from sqlalchemy import BigInteger, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from enterprise_chat_platform.application.accept_user_turn import (
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.idempotency_receipt import (
    idempotency_receipt_from_record,
    idempotency_receipt_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    IdempotencyReceiptRecord,
)

_LOCK_NAMESPACE = b"enterprise-chat:idempotency-scope:v1"

_ACQUIRE_SCOPE_LOCK: TextClause = text("SELECT pg_advisory_xact_lock(:lock_key)").bindparams(
    bindparam(
        "lock_key",
        type_=BigInteger(),
    )
)


def idempotency_scope_advisory_key(
    scope: IdempotencyScope,
) -> int:
    """Derive a stable signed PostgreSQL advisory-lock key."""
    parts = (
        _LOCK_NAMESPACE,
        scope.tenant_id.value.bytes,
        scope.conversation_id.value.bytes,
        scope.operation.encode("utf-8"),
        scope.key.value.encode("utf-8"),
    )

    digest = hashlib.sha256()

    for part in parts:
        digest.update(
            len(part).to_bytes(
                4,
                byteorder="big",
                signed=False,
            )
        )
        digest.update(part)

    return int.from_bytes(
        digest.digest()[:8],
        byteorder="big",
        signed=True,
    )


class SqlAlchemyIdempotencyReceiptRepository:
    """Persist receipts using a caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_scope_lock(
        self,
        *,
        scope: IdempotencyScope,
    ) -> None:
        """Serialize work for one exact idempotency scope."""
        self._require_active_transaction(
            "Idempotency scope locking",
        )

        await self._session.execute(
            _ACQUIRE_SCOPE_LOCK,
            {
                "lock_key": idempotency_scope_advisory_key(scope),
            },
        )

    async def get(
        self,
        *,
        scope: IdempotencyScope,
    ) -> IdempotencyReceipt | None:
        """Return the durable receipt for one exact scope."""
        self._require_active_transaction(
            "Idempotency receipt lookup",
        )

        statement = select(IdempotencyReceiptRecord).where(
            IdempotencyReceiptRecord.tenant_id == scope.tenant_id.value,
            IdempotencyReceiptRecord.conversation_id == scope.conversation_id.value,
            IdempotencyReceiptRecord.operation == scope.operation,
            IdempotencyReceiptRecord.idempotency_key == scope.key.value,
        )

        result = await self._session.execute(statement)
        record = result.scalar_one_or_none()

        if record is None:
            return None

        return idempotency_receipt_from_record(record)

    def add(
        self,
        *,
        receipt: IdempotencyReceipt,
    ) -> None:
        """Stage an immutable receipt without flushing or committing."""
        self._require_active_transaction(
            "Idempotency receipt persistence",
        )

        self._session.add(idempotency_receipt_to_new_record(receipt))

    def _require_active_transaction(
        self,
        action: str,
    ) -> None:
        if not self._session.in_transaction():
            raise RuntimeError(f"{action} requires an active transaction")
