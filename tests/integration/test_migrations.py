import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.settings import MigrationSettings, Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALL_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN"


def _single_migration_head() -> str:
    """Return the only valid migration head in the repository."""
    config = Config(PROJECT_ROOT / "alembic.ini")
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1
    return heads[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_history_is_at_single_protected_head() -> None:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())

    # Safety guard: this test must only inspect the local Compose database.
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
    assert runtime_url.username is not None
    assert migration_url.username is not None

    expected_head = _single_migration_head()
    engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                applied_head = await connection.scalar(
                    text("SELECT version_num FROM chat.alembic_version")
                )

                version_schemas = tuple(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT table_schema
                                FROM information_schema.tables
                                WHERE table_name = 'alembic_version'
                                ORDER BY table_schema
                                """
                            )
                        )
                    ).scalars()
                )

                owner = await connection.scalar(
                    text(
                        """
                        SELECT tableowner
                        FROM pg_catalog.pg_tables
                        WHERE schemaname = 'chat'
                        AND tablename = 'alembic_version'
                        """
                    )
                )

                runtime_has_any_privilege = await connection.scalar(
                    text(
                        """
                        SELECT has_table_privilege(
                            :runtime_role,
                            'chat.alembic_version',
                            :table_privileges
                        )
                        """
                    ),
                    {
                        "runtime_role": runtime_url.username,
                        "table_privileges": ALL_TABLE_PRIVILEGES,
                    },
                )
    finally:
        await engine.dispose()

    assert applied_head == expected_head
    assert version_schemas == ("chat",)
    assert owner == migration_url.username
    assert runtime_has_any_privilege is False


@pytest.mark.integration
def test_migration_autogenerate_has_no_schema_drift() -> None:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()

    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())

    # Safety guard: never run this integration test remotely.
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

    config = Config(PROJECT_ROOT / "alembic.ini")

    command.check(config)
