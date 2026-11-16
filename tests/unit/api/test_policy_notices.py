from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.api.policy_notices import (
    InputPolicyNoticeCollectionResponse,
    InputPolicyNoticeFindingResponse,
    InputPolicyNoticeResponse,
    SqlAlchemyInputPolicyNoticeQueries,
    get_input_policy_notice_queries,
    router,
)
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.governance import PolicySeverity
from enterprise_chat_platform.domain.identifiers import TenantId, UserId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")
MESSAGE_ID = UUID("44444444-4444-4444-8444-444444444444")
RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
ASSESSED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
ACTOR = ActorContext(tenant_id=TENANT_ID, actor_id=ACTOR_ID)

NOTICE_COLLECTION = InputPolicyNoticeCollectionResponse(
    items=(
        InputPolicyNoticeResponse(
            input_message_id=MESSAGE_ID,
            action="block",
            findings=(
                InputPolicyNoticeFindingResponse(
                    severity=PolicySeverity.HIGH,
                    safe_message="This request cannot be processed automatically.",
                ),
            ),
            assessed_at=ASSESSED_AT,
        ),
    )
)


@dataclass(frozen=True, slots=True)
class _Call:
    actor: ActorContext
    conversation_id: UUID


class _StubQueries:
    def __init__(
        self,
        response: InputPolicyNoticeCollectionResponse | None = NOTICE_COLLECTION,
    ) -> None:
        self.response = response
        self.calls: list[_Call] = []

    async def list_for_conversation(
        self,
        *,
        actor: ActorContext,
        conversation_id: UUID,
    ) -> InputPolicyNoticeCollectionResponse | None:
        self.calls.append(_Call(actor=actor, conversation_id=conversation_id))
        return self.response


def _application(queries: _StubQueries) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_actor_context] = lambda: ACTOR
    application.dependency_overrides[get_input_policy_notice_queries] = lambda: queries
    return application


def test_endpoint_returns_only_message_safe_notice_fields() -> None:
    queries = _StubQueries()
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(f"/v1/conversations/{CONVERSATION_ID}/input-policy-notices")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "input_message_id": str(MESSAGE_ID),
                "action": "block",
                "findings": [
                    {
                        "severity": "high",
                        "safe_message": "This request cannot be processed automatically.",
                    }
                ],
                "assessed_at": "2026-11-16T20:00:00Z",
            }
        ]
    }
    prohibited_fields = {
        "agent_run_id",
        "code",
        "content",
        "policy_version",
        "risk_score",
        "tenant_id",
    }
    assert prohibited_fields.isdisjoint(response.json()["items"][0])
    assert prohibited_fields.isdisjoint(response.json()["items"][0]["findings"][0])
    assert queries.calls == [_Call(actor=ACTOR, conversation_id=CONVERSATION_ID)]


def test_missing_and_unauthorized_conversation_share_safe_not_found() -> None:
    application = _application(_StubQueries(response=None))

    with TestClient(application) as client:
        response = client.get(f"/v1/conversations/{CONVERSATION_ID}/input-policy-notices")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "conversation_not_found",
            "message": "Conversation was not found",
        }
    }


def _query_harness(
    *results: MagicMock,
) -> tuple[SqlAlchemyInputPolicyNoticeQueries, MagicMock]:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=results)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context_manager)
    return (
        SqlAlchemyInputPolicyNoticeQueries(cast(AsyncSessionFactory, factory)),
        session,
    )


@pytest.mark.asyncio
async def test_owner_gate_stops_before_policy_lookup() -> None:
    owner_result = MagicMock()
    owner_result.scalar_one_or_none.return_value = None
    queries, session = _query_harness(owner_result)

    response = await queries.list_for_conversation(
        actor=ACTOR,
        conversation_id=CONVERSATION_ID,
    )

    assert response is None
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    parameters = tuple(statement.compile().params.values())
    assert "conversations.tenant_id" in sql
    assert "conversations.id" in sql
    assert "conversations.created_by" in sql
    assert TENANT_ID.value in parameters
    assert ACTOR_ID.value in parameters
    assert CONVERSATION_ID in parameters


@pytest.mark.asyncio
async def test_notice_query_is_tenant_conversation_scoped_and_skips_allow() -> None:
    owner_result = MagicMock()
    owner_result.scalar_one_or_none.return_value = CONVERSATION_ID
    notice_result = MagicMock()
    record = InputPolicyAssessmentRecord(
        tenant_id=TENANT_ID.value,
        agent_run_id=RUN_ID,
        policy_version="conversation-input.v1",
        action="escalate",
        risk_score=0.7,
        findings=[
            {
                "code": "review.human_requested",
                "severity": "medium",
                "safe_message": "This request requires review.",
            }
        ],
        assessed_at=ASSESSED_AT,
    )
    notice_result.all.return_value = [
        (
            record,
            MESSAGE_ID,
        )
    ]
    queries, session = _query_harness(owner_result, notice_result)

    response = await queries.list_for_conversation(
        actor=ACTOR,
        conversation_id=CONVERSATION_ID,
    )

    assert response == InputPolicyNoticeCollectionResponse(
        items=(
            InputPolicyNoticeResponse(
                input_message_id=MESSAGE_ID,
                action="escalate",
                findings=(
                    InputPolicyNoticeFindingResponse(
                        severity=PolicySeverity.MEDIUM,
                        safe_message="This request requires review.",
                    ),
                ),
                assessed_at=ASSESSED_AT,
            ),
        )
    )
    statement = session.execute.await_args_list[1].args[0]
    sql = str(statement)
    parameters = tuple(statement.compile().params.values())
    expanding_values = [value for value in parameters if isinstance(value, list)]

    assert "input_policy_assessments" in sql
    assert "JOIN chat.agent_runs" in sql
    assert "agent_runs.conversation_id" in sql
    assert "chat.messages" not in sql
    assert "content" not in sql
    assert TENANT_ID.value in parameters
    assert CONVERSATION_ID in parameters
    assert expanding_values == [["block", "escalate"]]


def test_response_mapper_rejects_allow_even_if_storage_filter_regresses() -> None:
    record = InputPolicyAssessmentRecord(
        tenant_id=TENANT_ID.value,
        agent_run_id=RUN_ID,
        policy_version="conversation-input.v1",
        action="allow",
        risk_score=0.1,
        findings=[],
        assessed_at=ASSESSED_AT,
    )
    with pytest.raises(ValueError, match="must be block or escalate"):
        SqlAlchemyInputPolicyNoticeQueries._response(
            record=record,
            input_message_id=MESSAGE_ID,
        )
