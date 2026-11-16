from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.input_policy_assessment import (
    SqlAlchemyInputPolicyAssessmentRepository,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
AGENT_RUN_ID = AgentRunId.parse("22222222-2222-4222-8222-222222222222")
ASSESSED_AT = datetime(2026, 11, 16, 16, 0, tzinfo=UTC)
ASSESSMENT = PolicyAssessment(
    action=PolicyAction.ALLOW,
    risk_score=0.0,
    policy_version="conversation-input.v1",
)


def _session_mock(*, active_transaction: bool) -> tuple[AsyncSession, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.in_transaction.return_value = active_transaction
    return cast(AsyncSession, session_mock), session_mock


def _record() -> InputPolicyAssessmentRecord:
    return InputPolicyAssessmentRecord(
        tenant_id=TENANT_ID.value,
        agent_run_id=AGENT_RUN_ID.value,
        policy_version=ASSESSMENT.policy_version,
        action=ASSESSMENT.action.value,
        risk_score=ASSESSMENT.risk_score,
        findings=[],
        assessed_at=ASSESSED_AT,
    )


@pytest.mark.parametrize("operation", ["get", "insert_or_get"])
@pytest.mark.asyncio
async def test_operations_require_active_transaction(operation: str) -> None:
    session, session_mock = _session_mock(active_transaction=False)
    repository = SqlAlchemyInputPolicyAssessmentRepository(session)

    with pytest.raises(RuntimeError, match="active transaction"):
        if operation == "get":
            await repository.get(
                tenant_id=TENANT_ID,
                agent_run_id=AGENT_RUN_ID,
            )
        else:
            await repository.insert_or_get(
                tenant_id=TENANT_ID,
                agent_run_id=AGENT_RUN_ID,
                assessment=ASSESSMENT,
                assessed_at=ASSESSED_AT,
            )

    session_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_returns_none_when_assessment_is_missing() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session_mock.execute = AsyncMock(return_value=result)
    repository = SqlAlchemyInputPolicyAssessmentRepository(session)

    actual = await repository.get(
        tenant_id=TENANT_ID,
        agent_run_id=AGENT_RUN_ID,
    )

    assert actual is None


@pytest.mark.asyncio
async def test_get_maps_exact_tenant_scoped_record() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    result = MagicMock()
    record = _record()
    result.scalar_one_or_none.return_value = record
    session_mock.execute = AsyncMock(return_value=result)
    repository = SqlAlchemyInputPolicyAssessmentRepository(session)

    actual = await repository.get(
        tenant_id=TENANT_ID,
        agent_run_id=AGENT_RUN_ID,
    )

    assert actual == ASSESSMENT
    statement = session_mock.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    normalized_sql = " ".join(str(compiled).split())
    assert "tenant_id =" in normalized_sql
    assert "agent_run_id =" in normalized_sql
    assert set(compiled.params.values()) == {TENANT_ID.value, AGENT_RUN_ID.value}


@pytest.mark.asyncio
async def test_insert_or_get_inserts_with_immutable_conflict_strategy() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    result = MagicMock()
    record = _record()
    result.scalar_one_or_none.return_value = record
    session_mock.execute = AsyncMock(return_value=result)
    repository = SqlAlchemyInputPolicyAssessmentRepository(session)

    actual = await repository.insert_or_get(
        tenant_id=TENANT_ID,
        agent_run_id=AGENT_RUN_ID,
        assessment=ASSESSMENT,
        assessed_at=ASSESSED_AT,
    )

    assert actual == ASSESSMENT
    session_mock.execute.assert_awaited_once()
    statement = session_mock.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    normalized_sql = " ".join(str(compiled).split())
    assert "ON CONFLICT (tenant_id, agent_run_id) DO NOTHING" in normalized_sql
    assert "DO UPDATE" not in normalized_sql
    assert "RETURNING" in normalized_sql
    session_mock.add.assert_not_called()
    session_mock.flush.assert_not_called()
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_insert_or_get_returns_first_stored_assessment_after_conflict() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    insert_result = MagicMock()
    insert_result.scalar_one_or_none.return_value = None
    select_result = MagicMock()
    record = _record()
    select_result.scalar_one_or_none.return_value = record
    session_mock.execute = AsyncMock(side_effect=[insert_result, select_result])
    repository = SqlAlchemyInputPolicyAssessmentRepository(session)

    candidate = PolicyAssessment(
        action=PolicyAction.ALLOW,
        risk_score=0.2,
        policy_version="conversation-input.v2",
    )
    actual = await repository.insert_or_get(
        tenant_id=TENANT_ID,
        agent_run_id=AGENT_RUN_ID,
        assessment=candidate,
        assessed_at=ASSESSED_AT,
    )

    assert actual == ASSESSMENT
    assert session_mock.execute.await_count == 2
    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_insert_or_get_fails_closed_when_conflict_is_not_visible() -> None:
    session, session_mock = _session_mock(active_transaction=True)
    insert_result = MagicMock()
    insert_result.scalar_one_or_none.return_value = None
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = None
    session_mock.execute = AsyncMock(side_effect=[insert_result, select_result])
    repository = SqlAlchemyInputPolicyAssessmentRepository(session)

    with pytest.raises(RuntimeError, match="was not visible"):
        await repository.insert_or_get(
            tenant_id=TENANT_ID,
            agent_run_id=AGENT_RUN_ID,
            assessment=ASSESSMENT,
            assessed_at=ASSESSED_AT,
        )

    session_mock.commit.assert_not_called()
    session_mock.rollback.assert_not_called()
