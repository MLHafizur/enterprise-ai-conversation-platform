import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from enterprise_chat_platform.domain.governance import MAX_POLICY_FINDINGS
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    AssistantProfileVersionRecord,
    ConversationRecord,
    InputPolicyAssessmentRecord,
    MessageRecord,
)
from enterprise_chat_platform.settings import (
    MigrationSettings,
    Settings,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
ASSISTANT_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000201")
ASSISTANT_PROFILE_VERSION = 1
EXPECTED_RUNTIME_PRIVILEGES = (
    "INSERT",
    "SELECT",
)
VALID_FINDING = {
    "code": "input.prompt_injection",
    "severity": "high",
    "safe_message": "The request cannot be processed automatically.",
}


def _validated_database_urls() -> tuple[URL, URL]:
    """Return runtime and migration URLs only when both target local Compose."""
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
    assert runtime_url.username is not None
    assert migration_url.username is not None

    return runtime_url, migration_url


def _create_local_runtime_engine() -> AsyncEngine:
    """Create a runtime engine only after proving the target is local."""
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    assert settings.environment in {"local", "test"}
    assert database_url.host in LOOPBACK_HOSTS

    return create_database_engine(settings)


def _conversation_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": conversation_id,
        "created_by": uuid4(),
        "status": "active",
        "version": 1,
        "last_message_sequence": 1,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }


def _message_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": message_id,
        "conversation_id": conversation_id,
        "author_id": uuid4(),
        "role": "user",
        "content": "Explain policy-governed conversation handling.",
        "sequence": 1,
        "idempotency_key": f"policy-assessment-{uuid4()}",
        "created_at": BASE_TIME,
    }


def _running_agent_run_values(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    agent_run_id: UUID,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": agent_run_id,
        "conversation_id": conversation_id,
        "input_message_id": message_id,
        "assistant_profile_id": ASSISTANT_PROFILE_ID,
        "assistant_profile_version": ASSISTANT_PROFILE_VERSION,
        "status": "running",
        "created_at": BASE_TIME,
        "started_at": BASE_TIME,
        "finished_at": None,
        "output_message_id": None,
        "failure_code": None,
        "failure_safe_message": None,
    }


def _assessment_values(
    *,
    tenant_id: UUID,
    agent_run_id: UUID,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "agent_run_id": agent_run_id,
        "policy_version": "conversation-input.v1",
        "action": "allow",
        "risk_score": 0.0,
        "findings": [],
        "assessed_at": BASE_TIME,
    }

    if overrides is not None:
        values.update(overrides)

    return values


async def _insert_agent_run_parent(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    agent_run_id: UUID,
) -> None:
    """Insert the conversation, message, and run referenced by an assessment."""
    await connection.execute(
        insert(AssistantProfileVersionRecord).values(
            tenant_id=tenant_id,
            profile_id=ASSISTANT_PROFILE_ID,
            version=ASSISTANT_PROFILE_VERSION,
            prompt_template_version="enterprise-chat.v1",
            assistant_name="Schema Test Assistant",
            organization_name="Schema Test Organization",
            response_tone="professional",
            expertise="senior_engineer",
            communication_style="direct_helpful",
            context_character_limit=4_096,
            created_at=BASE_TIME,
            created_by=uuid4(),
        )
    )
    await connection.execute(
        insert(ConversationRecord).values(
            _conversation_values(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
        )
    )
    await connection.execute(
        insert(MessageRecord).values(
            _message_values(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )
        )
    )
    await connection.execute(
        insert(AgentRunRecord).values(
            _running_agent_run_values(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_id=message_id,
                agent_run_id=agent_run_id,
            )
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_assessment_table_has_protected_ownership_and_insert_read_access() -> None:
    runtime_url, migration_url = _validated_database_urls()
    engine = create_async_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
    )

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                table_exists = await connection.scalar(
                    text("SELECT to_regclass('chat.input_policy_assessments') IS NOT NULL")
                )

                # Avoid misleading ownership and privilege results when migration is absent.
                assert table_exists is True

                owner = await connection.scalar(
                    text(
                        """
                        SELECT tableowner
                        FROM pg_catalog.pg_tables
                        WHERE schemaname = 'chat'
                        AND tablename = 'input_policy_assessments'
                        """
                    )
                )
                public_privileges = tuple(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT privilege_type
                                FROM information_schema.table_privileges
                                WHERE table_schema = 'chat'
                                AND table_name = 'input_policy_assessments'
                                AND grantee = 'PUBLIC'
                                ORDER BY privilege_type
                                """
                            )
                        )
                    ).scalars()
                )
                runtime_privileges = tuple(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT privilege_type
                                FROM information_schema.table_privileges
                                WHERE table_schema = 'chat'
                                AND table_name = 'input_policy_assessments'
                                AND grantee = :runtime_role
                                ORDER BY privilege_type
                                """
                            ),
                            {"runtime_role": runtime_url.username},
                        )
                    ).scalars()
                )
    finally:
        await engine.dispose()

    assert owner == migration_url.username
    assert public_privileges == ()
    assert runtime_privileges == EXPECTED_RUNTIME_PRIVILEGES


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_role_can_insert_and_read_valid_assessment() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                transaction = await connection.begin()

                try:
                    await _insert_agent_run_parent(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    result = await connection.execute(
                        insert(InputPolicyAssessmentRecord).values(
                            _assessment_values(
                                tenant_id=tenant_id,
                                agent_run_id=agent_run_id,
                            )
                        )
                    )
                    stored = (
                        await connection.execute(
                            select(
                                InputPolicyAssessmentRecord.policy_version,
                                InputPolicyAssessmentRecord.action,
                                InputPolicyAssessmentRecord.risk_score,
                                InputPolicyAssessmentRecord.findings,
                                InputPolicyAssessmentRecord.assessed_at,
                            ).where(
                                InputPolicyAssessmentRecord.tenant_id == tenant_id,
                                InputPolicyAssessmentRecord.agent_run_id == agent_run_id,
                            )
                        )
                    ).one()

                    assert result.rowcount == 1
                    assert stored.policy_version == "conversation-input.v1"
                    assert stored.action == "allow"
                    assert stored.risk_score == 0.0
                    assert stored.findings == []
                    assert stored.assessed_at == BASE_TIME
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "parent_tenant_mode",
    [
        pytest.param("missing", id="missing-agent-run"),
        pytest.param("different", id="cross-tenant-agent-run"),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_missing_or_cross_tenant_agent_run(
    parent_tenant_mode: str,
) -> None:
    assessment_tenant_id = uuid4()
    parent_tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    if parent_tenant_mode == "different":
                        await _insert_agent_run_parent(
                            connection,
                            tenant_id=parent_tenant_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            agent_run_id=agent_run_id,
                        )
                    await connection.execute(
                        insert(InputPolicyAssessmentRecord).values(
                            _assessment_values(
                                tenant_id=assessment_tenant_id,
                                agent_run_id=agent_run_id,
                            )
                        )
                    )
    finally:
        await engine.dispose()

    assert "fk_input_policy_assessments_agent_run" in str(exc_info.value.orig)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_allows_only_one_assessment_per_agent_run() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    engine = _create_local_runtime_engine()

    first_assessment = _assessment_values(
        tenant_id=tenant_id,
        agent_run_id=agent_run_id,
    )
    duplicate_assessment = _assessment_values(
        tenant_id=tenant_id,
        agent_run_id=agent_run_id,
        overrides={
            "policy_version": "conversation-input.v2",
            "risk_score": 0.4,
        },
    )

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_agent_run_parent(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await connection.execute(
                        insert(InputPolicyAssessmentRecord).values(first_assessment)
                    )
                    await connection.execute(
                        insert(InputPolicyAssessmentRecord).values(duplicate_assessment)
                    )
    finally:
        await engine.dispose()

    assert "pk_input_policy_assessments" in str(exc_info.value.orig)


@pytest.mark.parametrize(
    ("overrides", "expected_constraint"),
    [
        pytest.param(
            {"policy_version": "Conversation-Input.v1"},
            "ck_input_policy_assessments_policy_version_valid",
            id="invalid-policy-version",
        ),
        pytest.param(
            {"action": "review", "findings": [VALID_FINDING]},
            "ck_input_policy_assessments_action_valid",
            id="invalid-action",
        ),
        pytest.param(
            {"risk_score": -0.01},
            "ck_input_policy_assessments_risk_score_bounded",
            id="risk-below-zero",
        ),
        pytest.param(
            {"risk_score": 1.01},
            "ck_input_policy_assessments_risk_score_bounded",
            id="risk-above-one",
        ),
        pytest.param(
            {"findings": [VALID_FINDING] * (MAX_POLICY_FINDINGS + 1)},
            "ck_input_policy_assessments_findings_bounded",
            id="too-many-findings",
        ),
        pytest.param(
            {"action": "block", "risk_score": 1.0, "findings": []},
            "ck_input_policy_assessments_non_allow_requires_finding",
            id="block-without-finding",
        ),
        pytest.param(
            {"action": "escalate", "risk_score": 0.5, "findings": []},
            "ck_input_policy_assessments_non_allow_requires_finding",
            id="escalate-without-finding",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_invalid_assessment(
    overrides: Mapping[str, object],
    expected_constraint: str,
) -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    values = _assessment_values(
        tenant_id=tenant_id,
        agent_run_id=agent_run_id,
        overrides=overrides,
    )
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(IntegrityError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_agent_run_parent(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await connection.execute(insert(InputPolicyAssessmentRecord).values(values))
    finally:
        await engine.dispose()

    assert expected_constraint in str(exc_info.value.orig)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_findings_that_are_not_json_array() -> None:
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    agent_run_id = uuid4()
    values = _assessment_values(
        tenant_id=tenant_id,
        agent_run_id=agent_run_id,
        overrides={"findings": VALID_FINDING},
    )
    engine = _create_local_runtime_engine()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(DBAPIError) as exc_info:
                async with engine.begin() as connection:
                    await _insert_agent_run_parent(
                        connection,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        agent_run_id=agent_run_id,
                    )
                    await connection.execute(insert(InputPolicyAssessmentRecord).values(values))
    finally:
        await engine.dispose()

    database_error = str(exc_info.value.orig)
    assert (
        "ck_input_policy_assessments_findings_array" in database_error
        or "cannot get array length of a non-array" in database_error
    )
