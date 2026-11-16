from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

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
    SqlAlchemyAssistantProfileAdminStore,
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


@pytest.mark.asyncio
async def test_admin_lock_is_exclusive_instead_of_share_locked() -> None:
    session = _session()
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=CREATED_AT)
    session.execute.side_effect = [
        _result(_assignment()),
        _result(assistant_profile_to_record(profile)),
    ]
    repository = SqlAlchemyAssistantProfileRepository(cast(AsyncSession, session))

    await repository.get_or_create_active(
        tenant_id=TENANT_ID,
        default_profile=profile,
        exclusive=True,
    )

    lock_statement = session.execute.await_args_list[0].args[0]
    compiled = str(lock_statement.compile(dialect=POSTGRES_DIALECT))
    assert "FOR UPDATE OF tenant_assistant_profile_assignments" in compiled
    assert "FOR SHARE" not in compiled


@pytest.mark.asyncio
async def test_catalog_is_bounded_and_keeps_an_old_active_version_visible() -> None:
    base = default_assistant_profile(tenant_id=TENANT_ID, created_at=CREATED_AT)
    assignment = _assignment()
    newest_records = tuple(
        assistant_profile_to_record(
            replace(
                base,
                ref=replace(base.ref, version=version),
            )
        )
        for version in range(101, 1, -1)
    )
    active_record = assistant_profile_to_record(base)

    session = MagicMock(spec=AsyncSession)
    session.scalar = AsyncMock(side_effect=[assignment, active_record])
    scalars_result = MagicMock()
    scalars_result.all.return_value = newest_records
    session.scalars = AsyncMock(return_value=scalars_result)

    catalog = await SqlAlchemyAssistantProfileAdminStore._load_catalog(
        session=cast(AsyncSession, session),
        tenant_id=TENANT_ID,
    )

    assert catalog.latest_version == 101
    assert len(catalog.versions) == 100
    assert catalog.versions[0].ref.version == 101
    assert catalog.versions[-1].ref.version == 1
    assert catalog.active.profile_ref.version == 1
