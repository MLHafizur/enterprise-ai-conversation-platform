from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.application.errors import (
    AssistantProfileConflictError,
    AssistantProfileNotFoundError,
)
from enterprise_chat_platform.application.manage_assistant_profiles import (
    ActivateAssistantProfileVersionCommand,
)
from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.identifiers import (
    AssistantProfileId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AssistantProfileVersionRecord,
    TenantAssistantProfileAssignmentRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.assistant_profile import (
    SqlAlchemyAssistantProfileAdminStore,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
NOW = datetime(2026, 11, 16, 23, 0, tzinfo=UTC)
ADMIN_ID = UserId.parse("77777777-7777-4777-8777-777777777777")


@asynccontextmanager
async def _database_case(
    *tenant_ids: TenantId,
) -> AsyncIterator[tuple[AsyncSessionFactory, SqlAlchemyAssistantProfileAdminStore]]:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())
    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
    assert (runtime_url.host, runtime_url.port, runtime_url.database) == (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    )

    runtime_engine = create_database_engine(runtime_settings)
    migration_engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    session_factory = create_session_factory(runtime_engine)
    await _cleanup(migration_engine, tenant_ids)
    try:
        yield session_factory, SqlAlchemyAssistantProfileAdminStore(session_factory)
    finally:
        await _cleanup(migration_engine, tenant_ids)
        await runtime_engine.dispose()
        await migration_engine.dispose()


async def _cleanup(engine: AsyncEngine, tenant_ids: tuple[TenantId, ...]) -> None:
    values = tuple(tenant_id.value for tenant_id in tenant_ids)
    async with engine.begin() as connection:
        await connection.execute(
            delete(TenantAssistantProfileAssignmentRecord).where(
                TenantAssistantProfileAssignmentRecord.tenant_id.in_(values)
            )
        )
        await connection.execute(
            delete(AssistantProfileVersionRecord).where(
                AssistantProfileVersionRecord.tenant_id.in_(values)
            )
        )


def _tenant_id() -> TenantId:
    return TenantId(uuid4())


def _version_two(
    *,
    tenant_id: TenantId,
    assistant_name: str = "Enterprise Guide",
) -> AssistantProfile:
    baseline = default_assistant_profile(tenant_id=tenant_id, created_at=NOW)
    return replace(
        baseline,
        ref=replace(baseline.ref, version=2),
        assistant_name=assistant_name,
        response_tone=PromptResponseTone.FRIENDLY,
        created_at=NOW + timedelta(minutes=1),
        created_by=ADMIN_ID,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_is_immutable_and_activation_uses_optimistic_revisions() -> None:
    tenant_id = _tenant_id()
    default = default_assistant_profile(tenant_id=tenant_id, created_at=NOW)

    async with _database_case(tenant_id) as (_, store), asyncio.timeout(10):
        initial = await store.get_catalog(tenant_id=tenant_id, default_profile=default)
        original_v1 = initial.versions[0]
        created = await store.create_next_version(
            tenant_id=tenant_id,
            default_profile=default,
            expected_latest_version=1,
            profile=_version_two(tenant_id=tenant_id),
        )
        after_create = await store.get_catalog(tenant_id=tenant_id, default_profile=default)

        assert created.ref.version == 2
        assert after_create.latest_version == 2
        assert after_create.active.profile_ref.version == 1
        assert after_create.active.revision == 0
        assert after_create.versions[-1] == original_v1

        with pytest.raises(AssistantProfileConflictError):
            await store.create_next_version(
                tenant_id=tenant_id,
                default_profile=default,
                expected_latest_version=1,
                profile=_version_two(tenant_id=tenant_id, assistant_name="Stale Writer"),
            )

        activated = await store.activate(
            tenant_id=tenant_id,
            actor_id=ADMIN_ID,
            command=ActivateAssistantProfileVersionCommand(
                profile_ref=created.ref,
                expected_revision=0,
            ),
            activated_at=NOW + timedelta(minutes=2),
            default_profile=default,
        )
        assert activated.active.profile_ref == created.ref
        assert activated.active.revision == 1
        assert activated.active.activated_by == ADMIN_ID
        assert activated.active.activated_at == NOW + timedelta(minutes=2)

        with pytest.raises(AssistantProfileConflictError):
            await store.activate(
                tenant_id=tenant_id,
                actor_id=ADMIN_ID,
                command=ActivateAssistantProfileVersionCommand(
                    profile_ref=default.ref,
                    expected_revision=0,
                ),
                activated_at=NOW + timedelta(minutes=3),
                default_profile=default,
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_other_tenant_version_is_invisible_to_activation() -> None:
    tenant_a = _tenant_id()
    tenant_b = _tenant_id()
    default_a = default_assistant_profile(tenant_id=tenant_a, created_at=NOW)
    default_b = default_assistant_profile(tenant_id=tenant_b, created_at=NOW)

    async with _database_case(tenant_a, tenant_b) as (_, store), asyncio.timeout(10):
        await store.get_catalog(tenant_id=tenant_a, default_profile=default_a)
        await store.get_catalog(tenant_id=tenant_b, default_profile=default_b)
        tenant_b_v2 = await store.create_next_version(
            tenant_id=tenant_b,
            default_profile=default_b,
            expected_latest_version=1,
            profile=_version_two(tenant_id=tenant_b),
        )

        with pytest.raises(AssistantProfileNotFoundError):
            await store.activate(
                tenant_id=tenant_a,
                actor_id=ADMIN_ID,
                command=ActivateAssistantProfileVersionCommand(
                    profile_ref=tenant_b_v2.ref,
                    expected_revision=0,
                ),
                activated_at=NOW + timedelta(minutes=2),
                default_profile=default_a,
            )

        tenant_a_catalog = await store.get_catalog(
            tenant_id=tenant_a,
            default_profile=default_a,
        )
        assert tenant_a_catalog.latest_version == 1
        assert tenant_a_catalog.active.profile_ref == default_a.ref


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_next_version_writers_create_exactly_one_version() -> None:
    tenant_id = _tenant_id()
    default = default_assistant_profile(tenant_id=tenant_id, created_at=NOW)

    async with _database_case(tenant_id) as (_, store), asyncio.timeout(10):
        await store.get_catalog(tenant_id=tenant_id, default_profile=default)
        results = await asyncio.gather(
            store.create_next_version(
                tenant_id=tenant_id,
                default_profile=default,
                expected_latest_version=1,
                profile=_version_two(tenant_id=tenant_id, assistant_name="Writer One"),
            ),
            store.create_next_version(
                tenant_id=tenant_id,
                default_profile=default,
                expected_latest_version=1,
                profile=_version_two(tenant_id=tenant_id, assistant_name="Writer Two"),
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(result, AssistantProfile) for result in results) == 1
        assert sum(isinstance(result, AssistantProfileConflictError) for result in results) == 1
        catalog = await store.get_catalog(tenant_id=tenant_id, default_profile=default)
        assert tuple(profile.ref.version for profile in catalog.versions) == (2, 1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_tenant_profile_id_cannot_be_used_to_create_a_version() -> None:
    tenant_a = _tenant_id()
    tenant_b = _tenant_id()
    default_a = default_assistant_profile(tenant_id=tenant_a, created_at=NOW)
    default_b = default_assistant_profile(tenant_id=tenant_b, created_at=NOW)
    foreign_id = AssistantProfileId.new()

    async with _database_case(tenant_a, tenant_b) as (session_factory, store), asyncio.timeout(10):
        await store.get_catalog(tenant_id=tenant_a, default_profile=default_a)
        await store.get_catalog(tenant_id=tenant_b, default_profile=default_b)
        async with session_factory() as session, session.begin():
            session.add(
                AssistantProfileVersionRecord(
                    tenant_id=tenant_b.value,
                    profile_id=foreign_id.value,
                    version=1,
                    prompt_template_version=default_b.prompt_template_version,
                    assistant_name=default_b.assistant_name,
                    organization_name=default_b.organization_name,
                    response_tone=default_b.response_tone.value,
                    expertise=default_b.expertise.value,
                    communication_style=default_b.communication_style.value,
                    context_character_limit=default_b.context_character_limit,
                    created_at=NOW,
                    created_by=ADMIN_ID.value,
                )
            )

        foreign_candidate = replace(
            _version_two(tenant_id=tenant_a),
            ref=AssistantProfileRef(id=foreign_id, version=2),
        )
        with pytest.raises(AssistantProfileNotFoundError):
            await store.create_next_version(
                tenant_id=tenant_a,
                default_profile=default_a,
                expected_latest_version=1,
                profile=foreign_candidate,
            )
