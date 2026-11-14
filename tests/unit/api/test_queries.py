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

from enterprise_chat_platform.api.queries import (
    AgentRunFailureResponse,
    AgentRunResponse,
    ConversationResponse,
    MessagePageResponse,
    MessageResponse,
    SqlAlchemyChatQueries,
    get_chat_queries,
    router,
)
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.chat import ConversationStatus, MessageRole
from enterprise_chat_platform.domain.identifiers import TenantId, UserId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    MessageRecord,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")
MESSAGE_ID = UUID("44444444-4444-4444-8444-444444444444")
SECOND_MESSAGE_ID = UUID("55555555-5555-4555-8555-555555555555")
THIRD_MESSAGE_ID = UUID("66666666-6666-4666-8666-666666666666")
RUN_ID = UUID("77777777-7777-4777-8777-777777777777")
OUTPUT_MESSAGE_ID = UUID("88888888-8888-4888-8888-888888888888")
CREATED_AT = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 11, 13, 20, 5, tzinfo=UTC)
ACTOR = ActorContext(
    tenant_id=TENANT_ID,
    actor_id=ACTOR_ID,
)

CONVERSATION_RESPONSE = ConversationResponse(
    id=CONVERSATION_ID,
    status=ConversationStatus.ACTIVE,
    version=6,
    last_message_sequence=5,
    created_at=CREATED_AT,
    updated_at=UPDATED_AT,
)
MESSAGE_RESPONSE = MessageResponse(
    id=MESSAGE_ID,
    conversation_id=CONVERSATION_ID,
    author_id=ACTOR_ID.value,
    role=MessageRole.USER,
    content="Explain cursor pagination.",
    sequence=5,
    created_at=UPDATED_AT,
)
MESSAGE_PAGE_RESPONSE = MessagePageResponse(
    items=(MESSAGE_RESPONSE,),
    next_cursor=None,
)
AGENT_RUN_RESPONSE = AgentRunResponse(
    id=RUN_ID,
    conversation_id=CONVERSATION_ID,
    input_message_id=MESSAGE_ID,
    status=AgentRunStatus.FAILED,
    created_at=UPDATED_AT,
    started_at=UPDATED_AT,
    finished_at=UPDATED_AT,
    output_message_id=None,
    failure=AgentRunFailureResponse(
        safe_message="The assistant is temporarily unavailable.",
    ),
)


@dataclass(frozen=True, slots=True)
class _Call:
    operation: str
    actor: ActorContext
    resource_id: UUID
    cursor: int | None = None
    limit: int | None = None


class _StubQueries:
    def __init__(
        self,
        *,
        conversation: ConversationResponse | None = CONVERSATION_RESPONSE,
        messages: MessagePageResponse | None = MESSAGE_PAGE_RESPONSE,
        agent_run: AgentRunResponse | None = AGENT_RUN_RESPONSE,
    ) -> None:
        self.conversation = conversation
        self.messages = messages
        self.agent_run = agent_run
        self.calls: list[_Call] = []

    async def get_conversation(
        self,
        *,
        actor: ActorContext,
        conversation_id: UUID,
    ) -> ConversationResponse | None:
        self.calls.append(
            _Call(
                operation="conversation",
                actor=actor,
                resource_id=conversation_id,
            )
        )
        return self.conversation

    async def list_messages(
        self,
        *,
        actor: ActorContext,
        conversation_id: UUID,
        cursor: int | None,
        limit: int,
    ) -> MessagePageResponse | None:
        self.calls.append(
            _Call(
                operation="messages",
                actor=actor,
                resource_id=conversation_id,
                cursor=cursor,
                limit=limit,
            )
        )
        return self.messages

    async def get_agent_run(
        self,
        *,
        actor: ActorContext,
        run_id: UUID,
    ) -> AgentRunResponse | None:
        self.calls.append(
            _Call(
                operation="agent-run",
                actor=actor,
                resource_id=run_id,
            )
        )
        return self.agent_run


def _application(queries: _StubQueries) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_actor_context] = lambda: ACTOR
    application.dependency_overrides[get_chat_queries] = lambda: queries
    return application


def test_get_conversation_returns_owned_public_snapshot() -> None:
    queries = _StubQueries()
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(f"/v1/conversations/{CONVERSATION_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(CONVERSATION_ID),
        "status": "active",
        "version": 6,
        "last_message_sequence": 5,
        "created_at": "2026-11-13T20:00:00Z",
        "updated_at": "2026-11-13T20:05:00Z",
    }
    assert queries.calls == [
        _Call(
            operation="conversation",
            actor=ACTOR,
            resource_id=CONVERSATION_ID,
        )
    ]


def test_missing_or_unauthorized_conversation_uses_the_same_safe_404() -> None:
    queries = _StubQueries(conversation=None)
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(f"/v1/conversations/{CONVERSATION_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "conversation_not_found",
            "message": "Conversation was not found",
        }
    }


def test_list_messages_delegates_exclusive_cursor_and_limit() -> None:
    page = MessagePageResponse(
        items=(MESSAGE_RESPONSE,),
        next_cursor=5,
    )
    queries = _StubQueries(messages=page)
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(
            f"/v1/conversations/{CONVERSATION_ID}/messages",
            params={"cursor": 4, "limit": 1},
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(MESSAGE_ID),
                "conversation_id": str(CONVERSATION_ID),
                "author_id": str(ACTOR_ID),
                "role": "user",
                "content": "Explain cursor pagination.",
                "sequence": 5,
                "created_at": "2026-11-13T20:05:00Z",
            }
        ],
        "next_cursor": 5,
    }
    assert queries.calls == [
        _Call(
            operation="messages",
            actor=ACTOR,
            resource_id=CONVERSATION_ID,
            cursor=4,
            limit=1,
        )
    ]


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("cursor", -1),
        ("limit", 0),
        ("limit", 101),
    ],
)
def test_message_pagination_parameters_are_bounded(
    parameter: str,
    value: int,
) -> None:
    queries = _StubQueries()
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(
            f"/v1/conversations/{CONVERSATION_ID}/messages",
            params={parameter: value},
        )

    assert response.status_code == 422
    assert queries.calls == []


def test_missing_or_unauthorized_message_collection_uses_safe_404() -> None:
    queries = _StubQueries(messages=None)
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(f"/v1/conversations/{CONVERSATION_ID}/messages")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "conversation_not_found",
        "message": "Conversation was not found",
    }


def test_agent_run_exposes_only_the_safe_failure_message() -> None:
    queries = _StubQueries()
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(f"/v1/agent-runs/{RUN_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(RUN_ID),
        "conversation_id": str(CONVERSATION_ID),
        "input_message_id": str(MESSAGE_ID),
        "status": "failed",
        "created_at": "2026-11-13T20:05:00Z",
        "started_at": "2026-11-13T20:05:00Z",
        "finished_at": "2026-11-13T20:05:00Z",
        "output_message_id": None,
        "failure": {
            "safe_message": "The assistant is temporarily unavailable.",
        },
    }
    assert "failure_code" not in response.text
    assert queries.calls == [
        _Call(
            operation="agent-run",
            actor=ACTOR,
            resource_id=RUN_ID,
        )
    ]


def test_missing_or_unauthorized_agent_run_uses_the_same_safe_404() -> None:
    queries = _StubQueries(agent_run=None)
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get(f"/v1/agent-runs/{RUN_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "agent_run_not_found",
            "message": "Agent run was not found",
        }
    }


def test_malformed_resource_id_is_rejected_before_a_query() -> None:
    queries = _StubQueries()
    application = _application(queries)

    with TestClient(application) as client:
        response = client.get("/v1/agent-runs/not-a-uuid")

    assert response.status_code == 422
    assert queries.calls == []


def _query_harness(
    *results: MagicMock,
) -> tuple[SqlAlchemyChatQueries, MagicMock, MagicMock]:
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.execute = AsyncMock(side_effect=results)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=session_mock)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context_manager)
    queries = SqlAlchemyChatQueries(
        cast(AsyncSessionFactory, factory),
    )
    return queries, session_mock, factory


@pytest.mark.asyncio
async def test_conversation_query_scopes_id_tenant_and_owner_in_sql() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    queries, session, factory = _query_harness(result)

    response = await queries.get_conversation(
        actor=ACTOR,
        conversation_id=CONVERSATION_ID,
    )

    assert response is None
    factory.assert_called_once_with()
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    parameters = set(statement.compile().params.values())

    assert "conversations.tenant_id" in sql
    assert "conversations.id" in sql
    assert "conversations.created_by" in sql
    assert TENANT_ID.value in parameters
    assert ACTOR_ID.value in parameters
    assert CONVERSATION_ID in parameters


def _message_record(*, identifier: UUID, sequence: int) -> MessageRecord:
    return MessageRecord(
        tenant_id=TENANT_ID.value,
        id=identifier,
        conversation_id=CONVERSATION_ID,
        author_id=ACTOR_ID.value,
        role=MessageRole.USER.value,
        content=f"Message {sequence}",
        sequence=sequence,
        idempotency_key=f"message-{sequence}",
        created_at=UPDATED_AT,
    )


@pytest.mark.asyncio
async def test_message_query_uses_owned_parent_and_deterministic_keyset_page() -> None:
    owner_result = MagicMock()
    owner_result.scalar_one_or_none.return_value = CONVERSATION_ID
    message_result = MagicMock()
    message_result.scalars.return_value.all.return_value = [
        _message_record(identifier=MESSAGE_ID, sequence=5),
        _message_record(identifier=SECOND_MESSAGE_ID, sequence=6),
        _message_record(identifier=THIRD_MESSAGE_ID, sequence=7),
    ]
    queries, session, _ = _query_harness(owner_result, message_result)

    response = await queries.list_messages(
        actor=ACTOR,
        conversation_id=CONVERSATION_ID,
        cursor=4,
        limit=2,
    )

    assert response is not None
    assert tuple(item.sequence for item in response.items) == (5, 6)
    assert response.next_cursor == 6
    assert session.execute.await_count == 2

    owner_statement = session.execute.await_args_list[0].args[0]
    owner_sql = str(owner_statement)
    assert "conversations.tenant_id" in owner_sql
    assert "conversations.created_by" in owner_sql

    message_statement = session.execute.await_args_list[1].args[0]
    message_sql = str(message_statement)
    message_parameters = set(message_statement.compile().params.values())
    assert "messages.tenant_id" in message_sql
    assert "messages.conversation_id" in message_sql
    assert "messages.sequence >" in message_sql
    assert "ORDER BY chat.messages.sequence ASC, chat.messages.id ASC" in message_sql
    assert 4 in message_parameters
    assert 3 in message_parameters


@pytest.mark.asyncio
async def test_unauthorized_message_parent_stops_before_message_lookup() -> None:
    owner_result = MagicMock()
    owner_result.scalar_one_or_none.return_value = None
    queries, session, _ = _query_harness(owner_result)

    response = await queries.list_messages(
        actor=ACTOR,
        conversation_id=CONVERSATION_ID,
        cursor=None,
        limit=50,
    )

    assert response is None
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_run_query_scopes_run_through_owned_conversation() -> None:
    record = AgentRunRecord(
        tenant_id=TENANT_ID.value,
        id=RUN_ID,
        conversation_id=CONVERSATION_ID,
        input_message_id=MESSAGE_ID,
        status=AgentRunStatus.FAILED.value,
        created_at=UPDATED_AT,
        started_at=UPDATED_AT,
        finished_at=UPDATED_AT,
        output_message_id=None,
        failure_code="provider.sensitive_internal_code",
        failure_safe_message="Please retry later.",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = record
    queries, session, _ = _query_harness(result)

    response = await queries.get_agent_run(
        actor=ACTOR,
        run_id=RUN_ID,
    )

    assert response is not None
    assert response.failure == AgentRunFailureResponse(
        safe_message="Please retry later.",
    )
    assert not hasattr(response, "failure_code")

    statement = session.execute.await_args.args[0]
    sql = str(statement)
    parameters = set(statement.compile().params.values())
    assert "JOIN" in sql
    assert "agent_runs.tenant_id" in sql
    assert "conversations.created_by" in sql
    assert TENANT_ID.value in parameters
    assert ACTOR_ID.value in parameters
    assert RUN_ID in parameters
