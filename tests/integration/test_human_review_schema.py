from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.settings import MigrationSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRE_HUMAN_REVIEW_REVISION = "a72989b2dcec"


def _engines() -> tuple[AsyncEngine, AsyncEngine]:
    runtime_settings = Settings()
    migration_settings = MigrationSettings()
    runtime_url = make_url(runtime_settings.database_url.get_secret_value())
    migration_url = make_url(migration_settings.migration_database_url.get_secret_value())
    assert runtime_settings.environment in {"local", "test"}
    assert runtime_url.host in LOOPBACK_HOSTS
    assert migration_url.host in LOOPBACK_HOSTS
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
async def test_human_review_schema_has_exact_lifecycle_constraints() -> None:
    runtime_engine, migration_engine = _engines()
    try:
        async with asyncio.timeout(10), migration_engine.connect() as connection:
            owner = await connection.scalar(
                text(
                    """
                    SELECT tableowner
                    FROM pg_tables
                    WHERE schemaname = 'chat' AND tablename = 'human_reviews'
                    """
                )
            )
            constraints = set(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT conname
                            FROM pg_constraint
                            WHERE conrelid = 'chat.human_reviews'::regclass
                            """
                        )
                    )
                ).scalars()
            )

        assert owner == "enterprise_chat_migrator"
        assert {
            "pk_human_reviews",
            "fk_human_reviews_input_policy_assessment",
            "ck_human_reviews_policy_action_escalate",
            "ck_human_reviews_status_valid",
            "ck_human_reviews_resolution_valid",
            "ck_human_reviews_version_nonnegative",
            "ck_human_reviews_timestamps_ordered",
            "ck_human_reviews_lifecycle_state_valid",
        } <= constraints
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_can_only_update_review_workflow_columns() -> None:
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
                                'chat.human_reviews',
                                'SELECT'
                            ) AS can_select,
                            has_table_privilege(
                                current_user,
                                'chat.human_reviews',
                                'INSERT'
                            ) AS can_insert,
                            has_table_privilege(
                                current_user,
                                'chat.human_reviews',
                                'DELETE'
                            ) AS can_delete,
                            has_column_privilege(
                                current_user,
                                'chat.human_reviews',
                                'status',
                                'UPDATE'
                            ) AS can_update_status,
                            has_column_privilege(
                                current_user,
                                'chat.human_reviews',
                                'tenant_id',
                                'UPDATE'
                            ) AS can_update_tenant,
                            has_column_privilege(
                                current_user,
                                'chat.human_reviews',
                                'created_at',
                                'UPDATE'
                            ) AS can_update_created_at
                        """
                    )
                )
            ).one()

        assert tuple(privileges) == (True, True, False, True, False, False)
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_backfills_preexisting_escalation_as_pending_review() -> None:
    runtime_engine, migration_engine = _engines()
    config = Config(PROJECT_ROOT / "alembic.ini")
    tenant_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    assessed_at = datetime.now(UTC)

    try:
        # Recreate the deployment boundary: the escalation exists before the
        # human_reviews table is introduced.
        await asyncio.to_thread(command.upgrade, config, "head")
        await asyncio.to_thread(
            command.downgrade,
            config,
            PRE_HUMAN_REVIEW_REVISION,
        )

        async with asyncio.timeout(10), migration_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO chat.conversations (
                        tenant_id,
                        id,
                        created_by,
                        status,
                        version,
                        last_message_sequence,
                        created_at,
                        updated_at
                    ) VALUES (
                        :tenant_id,
                        :conversation_id,
                        :user_id,
                        'active',
                        1,
                        1,
                        :created_at,
                        :created_at
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "created_at": assessed_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO chat.messages (
                        tenant_id,
                        id,
                        conversation_id,
                        author_id,
                        role,
                        content,
                        sequence,
                        idempotency_key,
                        created_at
                    ) VALUES (
                        :tenant_id,
                        :message_id,
                        :conversation_id,
                        :user_id,
                        'user',
                        'Review this pre-migration escalation.',
                        1,
                        :idempotency_key,
                        :created_at
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "idempotency_key": f"backfill-{agent_run_id}",
                    "created_at": assessed_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO chat.agent_runs (
                        tenant_id,
                        id,
                        conversation_id,
                        input_message_id,
                        status,
                        created_at,
                        started_at,
                        finished_at,
                        output_message_id,
                        failure_code,
                        failure_safe_message
                    ) VALUES (
                        :tenant_id,
                        :agent_run_id,
                        :conversation_id,
                        :message_id,
                        'failed',
                        :created_at,
                        :created_at,
                        :created_at,
                        NULL,
                        'policy.escalated',
                        'This request requires human review.'
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "agent_run_id": agent_run_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "created_at": assessed_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO chat.input_policy_assessments (
                        tenant_id,
                        agent_run_id,
                        policy_version,
                        action,
                        risk_score,
                        findings,
                        assessed_at
                    ) VALUES (
                        :tenant_id,
                        :agent_run_id,
                        'conversation-input-v1',
                        'escalate',
                        0.9,
                        CAST(:findings AS jsonb),
                        :assessed_at
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "agent_run_id": agent_run_id,
                    "findings": '[{"code":"sensitive_request"}]',
                    "assessed_at": assessed_at,
                },
            )

        await asyncio.to_thread(command.upgrade, config, "head")

        async with asyncio.timeout(10), migration_engine.connect() as connection:
            review = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            policy_action,
                            status,
                            version,
                            created_at,
                            resolved_by,
                            resolved_at,
                            resolution
                        FROM chat.human_reviews
                        WHERE tenant_id = :tenant_id
                        AND agent_run_id = :agent_run_id
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "agent_run_id": agent_run_id,
                    },
                )
            ).one()

        assert tuple(review) == (
            "escalate",
            "pending",
            0,
            assessed_at,
            None,
            None,
            None,
        )
    finally:
        # Leave the shared local integration database at the repository head,
        # even when a seed or assertion fails.
        await asyncio.to_thread(command.upgrade, config, "head")
        async with asyncio.timeout(10), migration_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    DELETE FROM chat.human_reviews
                    WHERE tenant_id = :tenant_id
                    AND agent_run_id = :agent_run_id
                    """
                ),
                {"tenant_id": tenant_id, "agent_run_id": agent_run_id},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM chat.input_policy_assessments
                    WHERE tenant_id = :tenant_id
                    AND agent_run_id = :agent_run_id
                    """
                ),
                {"tenant_id": tenant_id, "agent_run_id": agent_run_id},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM chat.agent_runs
                    WHERE tenant_id = :tenant_id
                    AND id = :agent_run_id
                    """
                ),
                {"tenant_id": tenant_id, "agent_run_id": agent_run_id},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM chat.messages
                    WHERE tenant_id = :tenant_id
                    AND id = :message_id
                    """
                ),
                {"tenant_id": tenant_id, "message_id": message_id},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM chat.conversations
                    WHERE tenant_id = :tenant_id
                    AND id = :conversation_id
                    """
                ),
                {"tenant_id": tenant_id, "conversation_id": conversation_id},
            )
        await runtime_engine.dispose()
        await migration_engine.dispose()
