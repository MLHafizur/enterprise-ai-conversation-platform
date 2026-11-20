from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CODE_SECURITY_TABLES = (
    "code_repositories",
    "code_scans",
    "code_security_findings",
    "code_security_settings",
    "code_scan_reviews",
)
MUTABLE_RUNTIME_PRIVILEGES = ("DELETE", "INSERT", "SELECT", "UPDATE")
APPEND_ONLY_RUNTIME_PRIVILEGES = ("INSERT", "SELECT")


def _database_resources() -> tuple[URL, URL, AsyncEngine, AsyncEngine]:
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
    assert runtime_url.username is not None
    assert migration_url.username is not None
    return (
        runtime_url,
        migration_url,
        create_database_engine(runtime_settings),
        create_async_engine(
            migration_url,
            poolclass=NullPool,
            hide_parameters=True,
        ),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_code_security_tables_are_migrator_owned_and_public_is_revoked() -> None:
    _, migration_url, runtime_engine, migration_engine = _database_resources()
    try:
        async with asyncio.timeout(10), migration_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            tables.tablename,
                            tables.tableowner,
                            EXISTS (
                                SELECT 1
                                FROM information_schema.table_privileges AS privileges
                                WHERE privileges.table_schema = 'chat'
                                AND privileges.table_name = tables.tablename
                                AND privileges.grantee = 'PUBLIC'
                            ) AS public_has_privilege
                        FROM pg_catalog.pg_tables AS tables
                        WHERE tables.schemaname = 'chat'
                        AND tables.tablename = ANY(:table_names)
                        ORDER BY tables.tablename
                        """
                    ),
                    {"table_names": list(CODE_SECURITY_TABLES)},
                )
            ).all()

        assert tuple(row.tablename for row in rows) == tuple(sorted(CODE_SECURITY_TABLES))
        assert all(row.tableowner == migration_url.username for row in rows)
        assert not any(row.public_has_privilege for row in rows)
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_grants_keep_findings_and_reviews_append_only() -> None:
    runtime_url, _, runtime_engine, migration_engine = _database_resources()
    try:
        async with asyncio.timeout(10), migration_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT table_name, array_agg(privilege_type ORDER BY privilege_type)
                        FROM information_schema.table_privileges
                        WHERE table_schema = 'chat'
                        AND table_name = ANY(:table_names)
                        AND grantee = :runtime_role
                        GROUP BY table_name
                        ORDER BY table_name
                        """
                    ),
                    {
                        "table_names": list(CODE_SECURITY_TABLES),
                        "runtime_role": runtime_url.username,
                    },
                )
            ).all()
        privileges = {row[0]: tuple(row[1]) for row in rows}

        assert privileges == {
            "code_repositories": MUTABLE_RUNTIME_PRIVILEGES,
            "code_scan_reviews": APPEND_ONLY_RUNTIME_PRIVILEGES,
            "code_scans": MUTABLE_RUNTIME_PRIVILEGES,
            "code_security_findings": APPEND_ONLY_RUNTIME_PRIVILEGES,
            "code_security_settings": MUTABLE_RUNTIME_PRIVILEGES,
        }
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scan_summary_constraint_uses_the_product_finding_ceiling() -> None:
    _, _, runtime_engine, migration_engine = _database_resources()
    try:
        async with asyncio.timeout(10), migration_engine.connect() as connection:
            definition = await connection.scalar(
                text(
                    """
                    SELECT pg_get_constraintdef(constraint_row.oid)
                    FROM pg_catalog.pg_constraint AS constraint_row
                    JOIN pg_catalog.pg_class AS table_row
                      ON table_row.oid = constraint_row.conrelid
                    JOIN pg_catalog.pg_namespace AS namespace_row
                      ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = 'chat'
                    AND table_row.relname = 'code_scans'
                    AND constraint_row.conname = 'ck_code_scans_finding_counts_valid'
                    """
                )
            )

        assert isinstance(definition, str)
        assert "10000" in definition
        assert "100000" not in definition
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()
