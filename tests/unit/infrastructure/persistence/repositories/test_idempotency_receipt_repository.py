from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.accept_user_turn import (
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
)
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.models import (
    IdempotencyReceiptRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.idempotency_receipt import (
    SqlAlchemyIdempotencyReceiptRepository,
    idempotency_scope_advisory_key,
)

TENANT_ID = TenantId(UUID("11111111-1111-4111-8111-111111111111"))
OTHER_TENANT_ID = TenantId(UUID("99999999-9999-4999-8999-999999999999"))
CONVERSATION_ID = ConversationId(UUID("22222222-2222-4222-8222-222222222222"))
OTHER_CONVERSATION_ID = ConversationId(UUID("88888888-8888-4888-8888-888888888888"))
IDEMPOTENCY_KEY = IdempotencyKey("Request-AbC-001")


def _scope(
    *,
    tenant_id: TenantId = TENANT_ID,
    conversation_id: ConversationId = CONVERSATION_ID,
    key: IdempotencyKey = IDEMPOTENCY_KEY,
) -> IdempotencyScope:
    return IdempotencyScope(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        key=key,
    )


def _session_mock(
    *,
    active_transaction: bool,
) -> tuple[AsyncSession, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.in_transaction.return_value = active_transaction

    return cast(AsyncSession, session_mock), session_mock


def test_advisory_key_matches_stable_golden_vector() -> None:
    lock_key = idempotency_scope_advisory_key(_scope())

    assert lock_key == -8888531778661181824
    assert -(2**63) <= lock_key < 2**63


@pytest.mark.parametrize(
    "changed_scope",
    [
        _scope(tenant_id=OTHER_TENANT_ID),
        _scope(conversation_id=OTHER_CONVERSATION_ID),
        _scope(
            key=IdempotencyKey("request-abc-001"),
        ),
    ],
    ids=[
        "tenant",
        "conversation",
        "case-sensitive-key",
    ],
)
def test_advisory_key_includes_entire_scope(
    changed_scope: IdempotencyScope,
) -> None:
    assert idempotency_scope_advisory_key(changed_scope) != idempotency_scope_advisory_key(_scope())


@pytest.mark.asyncio
async def test_scope_lock_requires_active_transaction() -> None:
    session, session_mock = _session_mock(
        active_transaction=False,
    )
    repository = SqlAlchemyIdempotencyReceiptRepository(session)

    with pytest.raises(
        RuntimeError,
        match="active transaction",
    ):
        await repository.acquire_scope_lock(scope=_scope())

    session_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_scope_lock_executes_with_stable_key() -> None:
    session, session_mock = _session_mock(
        active_transaction=True,
    )
    session_mock.execute = AsyncMock()
    repository = SqlAlchemyIdempotencyReceiptRepository(session)

    await repository.acquire_scope_lock(scope=_scope())

    session_mock.execute.assert_awaited_once()
    statement, parameters = session_mock.execute.await_args.args

    assert "pg_advisory_xact_lock" in str(statement)
    assert parameters == {"lock_key": idempotency_scope_advisory_key(_scope())}


@pytest.mark.asyncio
async def test_get_requires_active_transaction() -> None:
    session, session_mock = _session_mock(
        active_transaction=False,
    )
    repository = SqlAlchemyIdempotencyReceiptRepository(session)

    with pytest.raises(
        RuntimeError,
        match="active transaction",
    ):
        await repository.get(scope=_scope())

    session_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_maps_existing_record() -> None:
    session, session_mock = _session_mock(
        active_transaction=True,
    )
    result = MagicMock()
    record = cast(
        IdempotencyReceiptRecord,
        MagicMock(spec=IdempotencyReceiptRecord),
    )
    expected = cast(
        IdempotencyReceipt,
        MagicMock(spec=IdempotencyReceipt),
    )
    result.scalar_one_or_none.return_value = record
    session_mock.execute = AsyncMock(return_value=result)
    repository = SqlAlchemyIdempotencyReceiptRepository(session)

    with patch(
        "enterprise_chat_platform.infrastructure.persistence."
        "repositories.idempotency_receipt."
        "idempotency_receipt_from_record",
        return_value=expected,
    ) as mapper:
        actual = await repository.get(scope=_scope())

    assert actual is expected
    mapper.assert_called_once_with(record)


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_record() -> None:
    session, session_mock = _session_mock(
        active_transaction=True,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session_mock.execute = AsyncMock(return_value=result)
    repository = SqlAlchemyIdempotencyReceiptRepository(session)

    actual = await repository.get(scope=_scope())

    assert actual is None


def test_add_requires_active_transaction() -> None:
    session, session_mock = _session_mock(
        active_transaction=False,
    )
    repository = SqlAlchemyIdempotencyReceiptRepository(session)
    receipt = cast(
        IdempotencyReceipt,
        MagicMock(spec=IdempotencyReceipt),
    )

    with pytest.raises(
        RuntimeError,
        match="active transaction",
    ):
        repository.add(receipt=receipt)

    session_mock.add.assert_not_called()


def test_add_stages_mapped_record_without_database_io() -> None:
    session, session_mock = _session_mock(
        active_transaction=True,
    )
    repository = SqlAlchemyIdempotencyReceiptRepository(session)
    receipt = cast(
        IdempotencyReceipt,
        MagicMock(spec=IdempotencyReceipt),
    )
    record = cast(
        IdempotencyReceiptRecord,
        MagicMock(spec=IdempotencyReceiptRecord),
    )

    with patch(
        "enterprise_chat_platform.infrastructure.persistence."
        "repositories.idempotency_receipt."
        "idempotency_receipt_to_new_record",
        return_value=record,
    ) as mapper:
        repository.add(receipt=receipt)

    mapper.assert_called_once_with(receipt)
    session_mock.add.assert_called_once_with(record)
    session_mock.execute.assert_not_called()
    session_mock.flush.assert_not_called()
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()
