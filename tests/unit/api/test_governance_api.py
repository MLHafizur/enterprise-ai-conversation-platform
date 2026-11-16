from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.api.governance import (
    GovernanceInputPolicyResponse,
    GovernancePolicyAssessmentResponse,
    GovernancePolicyCountsResponse,
    GovernancePolicyFindingResponse,
    SqlAlchemyGovernanceQueries,
    get_governance_queries,
    router,
)
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicySeverity,
)
from enterprise_chat_platform.domain.identifiers import TenantId, UserId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
USER_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
CONVERSATION_ID = UUID("44444444-4444-4444-8444-444444444444")
ASSESSED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


def _principal(*roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor=ActorContext(tenant_id=TENANT_ID, actor_id=USER_ID),
        roles=frozenset(roles),
    )


SNAPSHOT = GovernanceInputPolicyResponse(
    counts=GovernancePolicyCountsResponse(
        allow=7,
        block=2,
        escalate=1,
        total=10,
    ),
    recent=(
        GovernancePolicyAssessmentResponse(
            agent_run_id=RUN_ID,
            conversation_id=CONVERSATION_ID,
            policy_version="conversation-input.v1",
            action=PolicyAction.BLOCK,
            risk_score=0.95,
            findings=(
                GovernancePolicyFindingResponse(
                    code="security.prompt_injection",
                    severity=PolicySeverity.HIGH,
                    safe_message="The request conflicts with conversation policy",
                ),
            ),
            assessed_at=ASSESSED_AT,
        ),
    ),
)


class _StubQueries:
    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedPrincipal, int]] = []

    async def get_input_policy_snapshot(
        self,
        *,
        principal: AuthenticatedPrincipal,
        limit: int,
    ) -> GovernanceInputPolicyResponse:
        self.calls.append((principal, limit))
        return SNAPSHOT


def _application(
    *,
    principal: AuthenticatedPrincipal,
    queries: _StubQueries,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_authenticated_principal] = lambda: principal
    application.dependency_overrides[get_governance_queries] = lambda: queries
    return application


@pytest.mark.parametrize("role", ["admin", "agent"])
def test_governance_reader_receives_safe_tenant_snapshot(role: str) -> None:
    principal = _principal(role)
    queries = _StubQueries()

    with TestClient(_application(principal=principal, queries=queries)) as client:
        response = client.get("/v1/governance/input-policy", params={"limit": 10})

    assert response.status_code == 200
    assert response.json() == {
        "counts": {"allow": 7, "block": 2, "escalate": 1, "total": 10},
        "recent": [
            {
                "agent_run_id": str(RUN_ID),
                "conversation_id": str(CONVERSATION_ID),
                "policy_version": "conversation-input.v1",
                "action": "block",
                "risk_score": 0.95,
                "findings": [
                    {
                        "code": "security.prompt_injection",
                        "severity": "high",
                        "safe_message": "The request conflicts with conversation policy",
                    }
                ],
                "assessed_at": "2026-11-16T20:00:00Z",
            }
        ],
    }
    assert "tenant_id" not in response.text
    assert "content" not in response.text
    assert queries.calls == [(principal, 10)]


@pytest.mark.parametrize("roles", [(), ("user",)])
def test_non_governance_roles_are_forbidden_before_query(
    roles: tuple[str, ...],
) -> None:
    queries = _StubQueries()

    with TestClient(_application(principal=_principal(*roles), queries=queries)) as client:
        response = client.get("/v1/governance/input-policy")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert queries.calls == []


@pytest.mark.parametrize("limit", [0, 101])
def test_recent_limit_is_bounded_before_query(limit: int) -> None:
    queries = _StubQueries()

    with TestClient(_application(principal=_principal("admin"), queries=queries)) as client:
        response = client.get("/v1/governance/input-policy", params={"limit": limit})

    assert response.status_code == 422
    assert queries.calls == []


def _query_harness(
    *results: MagicMock,
) -> tuple[SqlAlchemyGovernanceQueries, MagicMock]:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=results)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context_manager)
    return SqlAlchemyGovernanceQueries(cast(AsyncSessionFactory, factory)), session


@pytest.mark.asyncio
async def test_queries_scope_counts_and_recent_rows_to_principal_tenant() -> None:
    count_result = MagicMock()
    count_result.all.return_value = [("allow", 7), ("block", 2)]
    record = InputPolicyAssessmentRecord(
        tenant_id=TENANT_ID.value,
        agent_run_id=RUN_ID,
        policy_version="conversation-input.v1",
        action="block",
        risk_score=0.95,
        findings=[
            {
                "code": "security.prompt_injection",
                "severity": "high",
                "safe_message": "The request conflicts with conversation policy",
            }
        ],
        assessed_at=ASSESSED_AT,
    )
    recent_result = MagicMock()
    recent_result.all.return_value = [(record, CONVERSATION_ID)]
    queries, session = _query_harness(count_result, recent_result)

    response = await queries.get_input_policy_snapshot(
        principal=_principal("admin"),
        limit=10,
    )

    assert response.counts == GovernancePolicyCountsResponse(
        allow=7,
        block=2,
        escalate=0,
        total=9,
    )
    assert response.recent == SNAPSHOT.recent
    count_statement = session.execute.await_args_list[0].args[0]
    recent_statement = session.execute.await_args_list[1].args[0]
    count_sql = str(count_statement)
    recent_sql = str(recent_statement)
    assert "input_policy_assessments.tenant_id" in count_sql
    assert "GROUP BY" in count_sql
    assert "JOIN" in recent_sql
    assert "agent_runs.tenant_id" in recent_sql
    assert "input_policy_assessments.tenant_id" in recent_sql
    assert "ORDER BY" in recent_sql
    assert TENANT_ID.value in set(count_statement.compile().params.values())
    assert TENANT_ID.value in set(recent_statement.compile().params.values())
    assert 10 in set(recent_statement.compile().params.values())
