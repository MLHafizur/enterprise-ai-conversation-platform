from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    ConversationRecord,
)
from enterprise_chat_platform.settings import Settings

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _create_local_runtime_engine() -> AsyncEngine:
    """Create an engine only after proving the target is local."""
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    return create_database_engine(settings)


def _conversation_values(
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build an otherwise valid persisted conversation."""
    values: dict[str, object] = {
        "tenant_id": uuid4(),
        "id": uuid4(),
        "created_by": uuid4(),
        "status": "active",
        "version": 0,
        "last_message_sequence": 0,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }

    if overrides is not None:
        values.update(overrides)

    return values


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_role_can_insert_valid_conversation() -> None:
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    result = await connection.execute(
                        insert(ConversationRecord).values(_conversation_values())
                    )

                    assert result.rowcount == 1
                finally:
                    # Keep the shared development database free of test rows.
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    [
        pytest.param(
            {"status": "deleted"},
            "ck_conversations_status_valid",
            id="invalid-status",
        ),
        pytest.param(
            {"version": -1},
            "ck_conversations_version_nonnegative",
            id="negative-version",
        ),
        pytest.param(
            {"last_message_sequence": -1},
            "ck_conversations_last_message_sequence_nonnegative",
            id="negative-message-sequence",
        ),
        pytest.param(
            {"updated_at": BASE_TIME - timedelta(microseconds=1)},
            "ck_conversations_timestamps_ordered",
            id="updated-before-created",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_invalid_conversation(
    overrides: Mapping[str, object],
    constraint_name: str,
) -> None:
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await connection.execute(
                        insert(ConversationRecord).values(_conversation_values(overrides))
                    )
    finally:
        await engine.dispose()

    assert constraint_name in str(exc_info.value.orig)
