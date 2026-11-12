"""Alembic environment for the enterprise chat database."""

import asyncio
from collections.abc import MutableMapping
from logging.config import fileConfig
from typing import Literal

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.infrastructure.persistence.metadata import APP_SCHEMA
from enterprise_chat_platform.infrastructure.persistence.models import (
    registered_model_metadata,
)
from enterprise_chat_platform.settings import MigrationSettings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = registered_model_metadata()
VERSION_TABLE = "alembic_version"

type SchemaObjectType = Literal[
    "schema",
    "table",
    "column",
    "index",
    "unique_constraint",
    "foreign_key_constraint",
    "check_constraint",
]
type ParentName = Literal[
    "schema_name",
    "table_name",
    "schema_qualified_table_name",
]


def migration_database_url() -> str:
    """Load the validated migration URL only when Alembic runs."""
    settings = MigrationSettings()
    return settings.migration_database_url.get_secret_value()


def include_name(
    name: str | None,
    type_: SchemaObjectType,
    parent_names: MutableMapping[ParentName, str | None],
) -> bool:
    """Limit reflection to application-owned database objects."""
    if type_ == "schema":
        # Alembic represents the connection's default schema as None.
        return name in {None, APP_SCHEMA}

    if type_ == "table":
        schema_name = parent_names.get("schema_name")

        return schema_name in {None, APP_SCHEMA} and name != VERSION_TABLE

    return True


def run_migrations_offline() -> None:
    """Render migration SQL without opening a database connection."""
    context.configure(
        url=migration_database_url(),
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        version_table=VERSION_TABLE,
        version_table_schema=APP_SCHEMA,
        version_table_pk=True,
        compare_type=True,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure and execute migrations on one synchronous connection view."""
    default_schema = connection.dialect.default_schema_name

    if default_schema != APP_SCHEMA:
        raise RuntimeError(
            "Migration connection must use "
            f"{APP_SCHEMA!r} as its default schema; "
            f"received {default_schema!r}"
        )
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        version_table=VERSION_TABLE,
        version_table_schema=APP_SCHEMA,
        version_table_pk=True,
        compare_type=True,
        compare_server_default=True,
        transactional_ddl=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open a short-lived asynchronous connection for the Alembic command."""
    engine = create_async_engine(
        migration_database_url(),
        poolclass=NullPool,
        hide_parameters=True,
    )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations against the configured PostgreSQL database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
