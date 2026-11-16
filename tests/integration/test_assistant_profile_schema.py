from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _engines() -> tuple[AsyncEngine, AsyncEngine]:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())

    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
    assert (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    ) == (
        runtime_url.host,
        runtime_url.port,
        runtime_url.database,
    )

    return (
        create_database_engine(runtime_settings),
        create_async_engine(
            migration_url,
            poolclass=NullPool,
            hide_parameters=True,
        ),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_assistant_profile_schema_has_exact_version_and_pin_relationships() -> None:
    runtime_engine, migration_engine = _engines()
    try:
        async with asyncio.timeout(10), migration_engine.connect() as connection:
            owner_rows = await connection.execute(
                text(
                    """
                    SELECT tablename, tableowner
                    FROM pg_catalog.pg_tables
                    WHERE schemaname = 'chat'
                    AND tablename IN (
                        'assistant_profile_versions',
                        'tenant_assistant_profile_assignments'
                    )
                    ORDER BY tablename
                    """
                )
            )
            owners = {str(row.tablename): str(row.tableowner) for row in owner_rows}
            constraints = set(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT conname
                            FROM pg_catalog.pg_constraint
                            WHERE conrelid IN (
                                'chat.assistant_profile_versions'::regclass,
                                'chat.tenant_assistant_profile_assignments'::regclass,
                                'chat.agent_runs'::regclass
                            )
                            """
                        )
                    )
                ).scalars()
            )
            pin_column_rows = await connection.execute(
                text(
                    """
                    SELECT column_name, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'chat'
                    AND table_name = 'agent_runs'
                    AND column_name IN (
                        'assistant_profile_id',
                        'assistant_profile_version'
                    )
                    ORDER BY column_name
                    """
                )
            )
            pin_columns = tuple(
                (str(row.column_name), str(row.is_nullable)) for row in pin_column_rows
            )
            pin_index = await connection.scalar(
                text(
                    """
                    SELECT to_regclass(
                        'chat.ix_agent_runs_tenant_assistant_profile'
                    ) IS NOT NULL
                    """
                )
            )

        assert owners == {
            "assistant_profile_versions": "enterprise_chat_migrator",
            "tenant_assistant_profile_assignments": "enterprise_chat_migrator",
        }
        assert {
            "pk_assistant_profile_versions",
            "pk_tenant_assistant_profile_assignments",
            "fk_tenant_assistant_profile_assignments_active_profile",
            "fk_agent_runs_assistant_profile",
            "ck_agent_runs_assistant_profile_version_positive",
        } <= constraints
        assert pin_columns == (
            ("assistant_profile_id", "NO"),
            ("assistant_profile_version", "NO"),
        )
        assert pin_index is True
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_privileges_keep_profiles_and_agent_run_pins_immutable() -> None:
    runtime_engine, migration_engine = _engines()
    try:
        async with asyncio.timeout(10), runtime_engine.connect() as connection:
            privileges = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            has_table_privilege(
                                current_user,
                                'chat.assistant_profile_versions',
                                'SELECT'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.assistant_profile_versions',
                                'INSERT'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.assistant_profile_versions',
                                'UPDATE'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.assistant_profile_versions',
                                'DELETE'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.tenant_assistant_profile_assignments',
                                'SELECT'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.tenant_assistant_profile_assignments',
                                'INSERT'
                            ),
                            has_column_privilege(
                                current_user,
                                'chat.tenant_assistant_profile_assignments',
                                'profile_version',
                                'UPDATE'
                            ),
                            has_column_privilege(
                                current_user,
                                'chat.tenant_assistant_profile_assignments',
                                'tenant_id',
                                'UPDATE'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.tenant_assistant_profile_assignments',
                                'DELETE'
                            ),
                            has_column_privilege(
                                current_user,
                                'chat.agent_runs',
                                'status',
                                'UPDATE'
                            ),
                            has_column_privilege(
                                current_user,
                                'chat.agent_runs',
                                'assistant_profile_id',
                                'UPDATE'
                            ),
                            has_table_privilege(
                                current_user,
                                'chat.agent_runs',
                                'DELETE'
                            )
                        """
                    )
                )
            ).one()

        assert tuple(privileges) == (
            True,
            True,
            False,
            False,
            True,
            True,
            True,
            False,
            False,
            True,
            False,
            False,
        )
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_backfill_leaves_no_unresolved_agent_run_profile_pin() -> None:
    runtime_engine, migration_engine = _engines()
    try:
        async with asyncio.timeout(10), migration_engine.connect() as connection:
            unresolved_pins = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM chat.agent_runs AS run
                    LEFT JOIN chat.assistant_profile_versions AS profile
                      ON profile.tenant_id = run.tenant_id
                     AND profile.profile_id = run.assistant_profile_id
                     AND profile.version = run.assistant_profile_version
                    WHERE profile.tenant_id IS NULL
                    """
                )
            )

        assert unresolved_pins == 0
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()
