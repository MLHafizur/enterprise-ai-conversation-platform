from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from enterprise_chat_platform.api.conversations import (
    get_create_conversation_handler,
    router,
)
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.create_conversation import (
    CreateConversationCommand,
    CreateConversationResult,
)
from enterprise_chat_platform.application.errors import ConversationNotFoundError
from enterprise_chat_platform.domain.identifiers import (
    ConversationId,
    TenantId,
    UserId,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = ConversationId.parse("33333333-3333-4333-8333-333333333333")
CREATED_AT = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)
ACTOR = ActorContext(tenant_id=TENANT_ID, actor_id=ACTOR_ID)


@dataclass
class _StubHandler:
    outcome: CreateConversationResult | Exception

    def __post_init__(self) -> None:
        self.calls: list[tuple[ActorContext, CreateConversationCommand]] = []

    async def handle(
        self,
        *,
        actor: ActorContext,
        command: CreateConversationCommand,
    ) -> CreateConversationResult:
        self.calls.append((actor, command))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _application(handler: _StubHandler) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_actor_context] = lambda: ACTOR
    application.dependency_overrides[get_create_conversation_handler] = lambda: handler
    return application


def _result(*, replayed: bool) -> CreateConversationResult:
    return CreateConversationResult(
        conversation_id=CONVERSATION_ID,
        created_at=CREATED_AT,
        replayed=replayed,
    )


def test_first_put_returns_201_and_typed_result() -> None:
    handler = _StubHandler(_result(replayed=False))

    with TestClient(_application(handler)) as client:
        response = client.put(f"/v1/conversations/{CONVERSATION_ID}")

    assert response.status_code == 201
    assert response.json() == {
        "conversation_id": str(CONVERSATION_ID),
        "created_at": "2026-11-13T18:00:00Z",
        "replayed": False,
    }
    assert handler.calls == [
        (
            ACTOR,
            CreateConversationCommand(conversation_id=CONVERSATION_ID),
        )
    ]


def test_exact_put_replay_returns_200() -> None:
    handler = _StubHandler(_result(replayed=True))

    with TestClient(_application(handler)) as client:
        response = client.put(f"/v1/conversations/{CONVERSATION_ID}")

    assert response.status_code == 200
    assert response.json()["replayed"] is True


def test_invalid_identifier_returns_safe_422_without_handler_call() -> None:
    handler = _StubHandler(_result(replayed=False))

    with TestClient(_application(handler)) as client:
        response = client.put("/v1/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "invalid_conversation",
            "message": "Conversation identifier is invalid",
        }
    }
    assert handler.calls == []


def test_unsafe_conflict_is_concealed_as_not_found() -> None:
    handler = _StubHandler(ConversationNotFoundError("private ownership detail"))

    with TestClient(_application(handler)) as client:
        response = client.put(f"/v1/conversations/{CONVERSATION_ID}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "conversation_not_found"
    assert "private" not in response.text
