from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.api.human_reviews import (
    HumanReviewCaseResponse,
    HumanReviewCollectionResponse,
    HumanReviewDetailResponse,
    HumanReviewFindingResponse,
    SqlAlchemyHumanReviewQueries,
    get_human_review_queries,
    get_resolve_human_review_handler,
    router,
)
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.errors import HumanReviewConflictError
from enterprise_chat_platform.application.resolve_human_review import (
    ResolveHumanReviewCommand,
)
from enterprise_chat_platform.domain.governance import PolicySeverity
from enterprise_chat_platform.domain.human_review import (
    HumanReviewResolution,
    HumanReviewStatus,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId, UserId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    HumanReviewRecord,
    InputPolicyAssessmentRecord,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
USER_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
CONVERSATION_ID = UUID("44444444-4444-4444-8444-444444444444")
MESSAGE_ID = UUID("55555555-5555-4555-8555-555555555555")
CREATED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


def _principal(*roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor=ActorContext(tenant_id=TENANT_ID, actor_id=USER_ID),
        roles=frozenset(roles),
    )


FINDINGS = (
    HumanReviewFindingResponse(
        code="conversation.legal_review",
        severity=PolicySeverity.HIGH,
        safe_message="A qualified reviewer must handle this legal request",
    ),
)
CASE = HumanReviewCaseResponse(
    agent_run_id=RUN_ID,
    conversation_id=CONVERSATION_ID,
    input_message_id=MESSAGE_ID,
    status=HumanReviewStatus.PENDING,
    version=0,
    policy_version="conversation-input.v1",
    risk_score=0.75,
    findings=FINDINGS,
    created_at=CREATED_AT,
    resolved_at=None,
    resolution=None,
)
DETAIL = HumanReviewDetailResponse(
    **CASE.model_dump(),
    input_content="Please provide legal advice about this contract.",
)


class _StubQueries:
    def __init__(self, *, detail: HumanReviewDetailResponse | None = DETAIL) -> None:
        self.detail = detail
        self.list_calls: list[tuple[TenantId, HumanReviewStatus, int]] = []
        self.detail_calls: list[tuple[TenantId, UUID]] = []

    async def list_cases(
        self,
        *,
        tenant_id: TenantId,
        review_status: HumanReviewStatus,
        limit: int,
    ) -> HumanReviewCollectionResponse:
        self.list_calls.append((tenant_id, review_status, limit))
        return HumanReviewCollectionResponse(items=(CASE,))

    async def get_detail(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
    ) -> HumanReviewDetailResponse | None:
        self.detail_calls.append((tenant_id, agent_run_id.value))
        return self.detail


class _StubHandler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def handle(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def _application(
    *,
    principal: AuthenticatedPrincipal,
    queries: _StubQueries,
    handler: _StubHandler | None = None,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_authenticated_principal] = lambda: principal
    application.dependency_overrides[get_human_review_queries] = lambda: queries
    application.dependency_overrides[get_resolve_human_review_handler] = lambda: (
        handler or _StubHandler()
    )
    return application


@pytest.mark.parametrize("role", ["admin", "agent"])
def test_authorized_reviewer_lists_safe_fifo_metadata(role: str) -> None:
    queries = _StubQueries()
    with TestClient(_application(principal=_principal(role), queries=queries)) as client:
        response = client.get(
            "/v1/governance/human-reviews",
            params={"status": "pending", "limit": 10},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["agent_run_id"] == str(RUN_ID)
    assert "input_content" not in response.text
    assert "resolved_by" not in response.text
    assert queries.list_calls == [(TENANT_ID, HumanReviewStatus.PENDING, 10)]


@pytest.mark.parametrize("roles", [(), ("user",)])
def test_non_reviewer_is_forbidden_before_any_query(roles: tuple[str, ...]) -> None:
    queries = _StubQueries()
    with TestClient(_application(principal=_principal(*roles), queries=queries)) as client:
        response = client.get("/v1/governance/human-reviews")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert queries.list_calls == []
    assert queries.detail_calls == []


def test_detail_exposes_only_the_exact_escalated_input_to_reviewer() -> None:
    queries = _StubQueries()
    with TestClient(_application(principal=_principal("agent"), queries=queries)) as client:
        response = client.get(f"/v1/governance/human-reviews/{RUN_ID}")

    assert response.status_code == 200
    assert response.json()["input_content"] == DETAIL.input_content
    assert response.headers["cache-control"] == "no-store"
    assert "resolved_by" not in response.text
    assert queries.detail_calls == [(TENANT_ID, RUN_ID)]


def test_missing_and_cross_tenant_cases_share_safe_not_found() -> None:
    queries = _StubQueries(detail=None)
    with TestClient(_application(principal=_principal("admin"), queries=queries)) as client:
        response = client.get(f"/v1/governance/human-reviews/{RUN_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "human_review_not_found",
            "message": "Human review was not found",
        }
    }


def test_resolve_uses_signed_reviewer_and_returns_detail_shape() -> None:
    queries = _StubQueries()
    handler = _StubHandler()
    with TestClient(
        _application(
            principal=_principal("agent"),
            queries=queries,
            handler=handler,
        )
    ) as client:
        response = client.post(
            f"/v1/governance/human-reviews/{RUN_ID}/resolve",
            json={"expected_version": 0, "resolution": "confirmed"},
        )

    assert response.status_code == 200
    assert response.json()["input_content"] == DETAIL.input_content
    assert response.headers["cache-control"] == "no-store"
    assert len(handler.calls) == 1
    assert handler.calls[0]["tenant_id"] == TENANT_ID
    assert handler.calls[0]["reviewer_id"] == USER_ID
    command = cast(ResolveHumanReviewCommand, handler.calls[0]["command"])
    assert command.expected_version == 0
    assert command.resolution is HumanReviewResolution.CONFIRMED


def test_stale_resolution_returns_safe_conflict_without_detail_reload() -> None:
    queries = _StubQueries()
    handler = _StubHandler(error=HumanReviewConflictError("internal optimistic conflict detail"))
    with TestClient(
        _application(
            principal=_principal("admin"),
            queries=queries,
            handler=handler,
        )
    ) as client:
        response = client.post(
            f"/v1/governance/human-reviews/{RUN_ID}/resolve",
            json={"expected_version": 0, "resolution": "dismissed"},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "human_review_conflict",
            "message": "Human review changed before resolution",
        }
    }
    assert "internal" not in response.text
    assert queries.detail_calls == []


def _query_harness(result: MagicMock) -> tuple[SqlAlchemyHumanReviewQueries, MagicMock]:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=result)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context_manager)
    return SqlAlchemyHumanReviewQueries(cast(AsyncSessionFactory, factory)), session


@pytest.mark.asyncio
async def test_detail_query_scopes_exact_message_join_to_tenant_and_conversation() -> None:
    result = MagicMock()
    result.one_or_none.return_value = None
    queries, session = _query_harness(result)

    response = await queries.get_detail(
        tenant_id=TENANT_ID,
        agent_run_id=AgentRunId(RUN_ID),
    )

    assert response is None
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    assert "chat.messages.tenant_id = chat.agent_runs.tenant_id" in sql
    assert "chat.messages.conversation_id = chat.agent_runs.conversation_id" in sql
    assert "chat.messages.id = chat.agent_runs.input_message_id" in sql
    assert "messages.role" in sql
    assert TENANT_ID.value in set(statement.compile().params.values())


def test_case_response_never_serializes_raw_content_or_reviewer_identity() -> None:
    review_record = HumanReviewRecord(
        tenant_id=TENANT_ID.value,
        agent_run_id=RUN_ID,
        policy_action="escalate",
        status="pending",
        version=0,
        created_at=CREATED_AT,
        resolved_by=None,
        resolved_at=None,
        resolution=None,
    )
    assessment_record = InputPolicyAssessmentRecord(
        tenant_id=TENANT_ID.value,
        agent_run_id=RUN_ID,
        policy_version="conversation-input.v1",
        action="escalate",
        risk_score=0.75,
        findings=[
            {
                "code": "conversation.legal_review",
                "severity": "high",
                "safe_message": "A qualified reviewer must handle this legal request",
            }
        ],
        assessed_at=CREATED_AT,
    )

    response = SqlAlchemyHumanReviewQueries._case_response(
        review_record=review_record,
        assessment_record=assessment_record,
        conversation_id=CONVERSATION_ID,
        input_message_id=MESSAGE_ID,
    )

    assert set(response.model_dump()) == {
        "agent_run_id",
        "conversation_id",
        "input_message_id",
        "status",
        "version",
        "policy_version",
        "risk_score",
        "findings",
        "created_at",
        "resolved_at",
        "resolution",
    }
