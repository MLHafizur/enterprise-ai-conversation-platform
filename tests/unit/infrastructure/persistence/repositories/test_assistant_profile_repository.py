from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
from enterprise_chat_platform.domain.identifiers import TenantId
from enterprise_chat_platform.infrastructure.persistence.mappers.assistant_profile import (
    assistant_profile_to_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    TenantAssistantProfileAssignmentRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.assistant_profile import (
    SqlAlchemyAssistantProfileRepository,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = TenantId.parse("99999999-9999-4999-8999-999999999999")
CREATED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
POSTGRES_DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


def _result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.in_transaction.return_value = True
    return session


def _assignment() -> TenantAssistantProfileAssignmentRecord:
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=CREATED_AT)
    return TenantAssistantProfileAssignmentRecord(
        tenant_id=TENANT_ID.value,
        profile_id=profile.ref.id.value,
        profile_version=profile.ref.version,
        revision=0,
        activated_at=profile.created_at,
        activated_by=profile.created_by.value,
    )


@pytest.mark.asyncio
async def test_existing_assignment_is_share_locked_and_resolved_exactly() -> None:
    session = _session()
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=CREATED_AT)
    session.execute.side_effect = [
        _result(_assignment()),
        _result(assistant_profile_to_record(profile)),
    ]
    repository = SqlAlchemyAssistantProfileRepository(cast(AsyncSession, session))

    loaded = await repository.get_or_create_active(
        tenant_id=TENANT_ID,
        default_profile=profile,
    )

    assert loaded == profile
    assert session.execute.await_count == 2
    lock_statement = session.execute.await_args_list[0].args[0]
    compiled = str(lock_statement.compile(dialect=POSTGRES_DIALECT))
    assert "FOR SHARE OF tenant_assistant_profile_assignments" in compiled


@pytest.mark.asyncio
async def test_missing_assignment_bootstraps_profile_and_pointer_once() -> None:
    session = _session()
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=CREATED_AT)
    session.execute.side_effect = [
        _result(None),
        MagicMock(),
        MagicMock(),
        _result(_assignment()),
        _result(assistant_profile_to_record(profile)),
    ]
    repository = SqlAlchemyAssistantProfileRepository(cast(AsyncSession, session))

    loaded = await repository.get_or_create_active(
        tenant_id=TENANT_ID,
        default_profile=profile,
    )

    assert loaded == profile
    assert session.execute.await_count == 5
    profile_insert = session.execute.await_args_list[1].args[0]
    assignment_insert = session.execute.await_args_list[2].args[0]
    assert "ON CONFLICT (tenant_id, profile_id, version) DO NOTHING" in str(
        profile_insert.compile(dialect=POSTGRES_DIALECT)
    )
    assert "ON CONFLICT (tenant_id) DO NOTHING" in str(
        assignment_insert.compile(dialect=POSTGRES_DIALECT)
    )


@pytest.mark.asyncio
async def test_bootstrap_rejects_cross_tenant_default_before_database_access() -> None:
    session = _session()
    profile = default_assistant_profile(tenant_id=OTHER_TENANT_ID, created_at=CREATED_AT)
    repository = SqlAlchemyAssistantProfileRepository(cast(AsyncSession, session))

    with pytest.raises(ValueError, match="tenant must match"):
        await repository.get_or_create_active(
            tenant_id=TENANT_ID,
            default_profile=profile,
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_requires_caller_owned_transaction() -> None:
    session = _session()
    session.in_transaction.return_value = False
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=CREATED_AT)
    repository = SqlAlchemyAssistantProfileRepository(cast(AsyncSession, session))

    with pytest.raises(RuntimeError, match="active transaction"):
        await repository.get_or_create_active(
            tenant_id=TENANT_ID,
            default_profile=profile,
        )
