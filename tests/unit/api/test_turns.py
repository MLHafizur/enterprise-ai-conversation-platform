from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from enterprise_chat_platform.api.dependencies import get_optional_tenant_service
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.api.turns import (
    get_accept_user_turn_handler,
    router,
)
from enterprise_chat_platform.application.accept_user_turn import (
    AcceptUserTurnCommand,
    AcceptUserTurnResult,
    ActorContext,
)
from enterprise_chat_platform.application.errors import (
    ConversationConcurrencyError,
    ConversationNotFoundError,
    IdempotencyConflictError,
)
from enterprise_chat_platform.application.tenant_catalog import TenantDefinition
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.realtime.tenant_service import (
    MessageQuotaDecision,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = ConversationId.parse("33333333-3333-4333-8333-333333333333")
MESSAGE_ID = MessageId.parse("44444444-4444-4444-8444-444444444444")
AGENT_RUN_ID = AgentRunId.parse("55555555-5555-4555-8555-555555555555")
ACCEPTED_AT = datetime(2026, 11, 13, 17, 30, tzinfo=UTC)
IDEMPOTENCY_KEY = "turn-api-test"
ACTOR = ActorContext(
    tenant_id=TENANT_ID,
    actor_id=ACTOR_ID,
)
RESULT = AcceptUserTurnResult(
    conversation_id=CONVERSATION_ID,
    conversation_version=7,
    message_id=MESSAGE_ID,
    message_sequence=4,
    agent_run_id=AGENT_RUN_ID,
    accepted_at=ACCEPTED_AT,
    replayed=False,
)


@dataclass(frozen=True, slots=True)
class _HandlerCall:
    actor: ActorContext
    command: AcceptUserTurnCommand


class _StubHandler:
    def __init__(self, outcome: AcceptUserTurnResult | Exception) -> None:
        self._outcome = outcome
        self.calls: list[_HandlerCall] = []

    async def handle(
        self,
        *,
        actor: ActorContext,
        command: AcceptUserTurnCommand,
    ) -> AcceptUserTurnResult:
        self.calls.append(
            _HandlerCall(
                actor=actor,
                command=command,
            )
        )

        if isinstance(self._outcome, Exception):
            raise self._outcome

        return self._outcome


@dataclass(frozen=True, slots=True)
class _QuotaCall:
    tenant: TenantDefinition
    user_id: UserId
    idempotency_key: str


class _StubTenantService:
    def __init__(self, outcome: MessageQuotaDecision | Exception) -> None:
        self._outcome = outcome
        self.calls: list[_QuotaCall] = []

    async def reserve_message_quota(
        self,
        *,
        tenant: TenantDefinition,
        user_id: UserId,
        idempotency_key: str,
    ) -> MessageQuotaDecision:
        self.calls.append(
            _QuotaCall(
                tenant=tenant,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
        )
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _application(
    handler: _StubHandler,
    *,
    tenant_service: _StubTenantService | None = None,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_actor_context] = lambda: ACTOR
    application.dependency_overrides[get_accept_user_turn_handler] = lambda: handler
    application.dependency_overrides[get_optional_tenant_service] = lambda: tenant_service
    return application


def _turn_path(conversation_id: str = str(CONVERSATION_ID)) -> str:
    return f"/v1/conversations/{conversation_id}/turns"


def test_accept_turn_returns_stable_202_contract_and_delegates_typed_command() -> None:
    handler = _StubHandler(RESULT)
    application = _application(handler)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"content": "Explain transactional outboxes."},
        )

    assert response.status_code == 202
    assert response.json() == {
        "conversation_id": str(CONVERSATION_ID),
        "conversation_version": 7,
        "message_id": str(MESSAGE_ID),
        "message_sequence": 4,
        "agent_run_id": str(AGENT_RUN_ID),
        "accepted_at": "2026-11-13T17:30:00Z",
        "replayed": False,
    }
    assert handler.calls == [
        _HandlerCall(
            actor=ACTOR,
            command=AcceptUserTurnCommand(
                conversation_id=CONVERSATION_ID,
                content="Explain transactional outboxes.",
                idempotency_key=handler.calls[0].command.idempotency_key,
            ),
        )
    ]
    assert handler.calls[0].command.idempotency_key.value == IDEMPOTENCY_KEY


def test_replayed_turn_uses_the_same_response_contract() -> None:
    replayed_result = AcceptUserTurnResult(
        conversation_id=CONVERSATION_ID,
        conversation_version=7,
        message_id=MESSAGE_ID,
        message_sequence=4,
        agent_run_id=AGENT_RUN_ID,
        accepted_at=ACCEPTED_AT,
        replayed=True,
    )
    handler = _StubHandler(replayed_result)
    application = _application(handler)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"content": "Explain transactional outboxes."},
        )

    assert response.status_code == 202
    assert response.json()["replayed"] is True


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_code", "expected_message"),
    [
        (
            ConversationNotFoundError("sensitive lookup detail"),
            404,
            "conversation_not_found",
            "Conversation was not found",
        ),
        (
            IdempotencyConflictError("sensitive receipt detail"),
            409,
            "idempotency_conflict",
            "Idempotency key conflicts with an earlier request",
        ),
        (
            ConversationConcurrencyError("sensitive version detail"),
            409,
            "conversation_conflict",
            "Conversation changed; retry the request",
        ),
        (
            DomainValidationError("sensitive domain detail"),
            422,
            "invalid_turn",
            "Turn request is invalid",
        ),
    ],
)
def test_known_failures_map_to_safe_stable_errors(
    outcome: Exception,
    expected_status: int,
    expected_code: str,
    expected_message: str,
) -> None:
    handler = _StubHandler(outcome)
    application = _application(handler)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"content": "A valid message"},
        )

    assert response.status_code == expected_status
    assert response.json() == {
        "detail": {
            "code": expected_code,
            "message": expected_message,
        }
    }
    assert "sensitive" not in response.text


@pytest.mark.parametrize(
    ("conversation_id", "idempotency_key", "content"),
    [
        ("not-a-uuid", IDEMPOTENCY_KEY, "A valid message"),
        (str(CONVERSATION_ID), "x" * 129, "A valid message"),
    ],
)
def test_invalid_domain_input_returns_safe_422(
    conversation_id: str,
    idempotency_key: str,
    content: str,
) -> None:
    handler = _StubHandler(RESULT)
    application = _application(handler)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(conversation_id),
            headers={"Idempotency-Key": idempotency_key},
            json={"content": content},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "invalid_turn",
            "message": "Turn request is invalid",
        }
    }
    assert handler.calls == []


def test_identity_fields_are_forbidden_in_request_body() -> None:
    handler = _StubHandler(RESULT)
    application = _application(handler)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={
                "content": "A valid message",
                "tenant_id": str(TENANT_ID),
                "user_id": str(ACTOR_ID),
            },
        )

    assert response.status_code == 422
    assert handler.calls == []


def test_idempotency_key_header_is_required() -> None:
    handler = _StubHandler(RESULT)
    application = _application(handler)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            json={"content": "A valid message"},
        )

    assert response.status_code == 422
    assert handler.calls == []


def test_denied_message_quota_returns_stable_429_without_calling_handler() -> None:
    handler = _StubHandler(RESULT)
    tenant_service = _StubTenantService(
        MessageQuotaDecision(
            allowed=False,
            daily_messages_used=5000,
            rate_messages_used=4,
            reason="daily_limit_exceeded",
        )
    )
    application = _application(handler, tenant_service=tenant_service)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"content": "A valid message"},
        )

    assert response.status_code == 429
    assert response.json() == {
        "detail": {
            "code": "quota_exceeded",
            "message": "Message quota exceeded",
        }
    }
    assert handler.calls == []
    assert len(tenant_service.calls) == 1
    assert tenant_service.calls[0].user_id == ACTOR_ID


def test_redis_quota_outage_fails_open_for_durable_turn_acceptance() -> None:
    handler = _StubHandler(RESULT)
    tenant_service = _StubTenantService(RedisError("redis unavailable"))
    application = _application(handler, tenant_service=tenant_service)

    with TestClient(application) as client:
        response = client.post(
            _turn_path(),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"content": "A valid message"},
        )

    assert response.status_code == 202
    assert len(handler.calls) == 1
    assert len(tenant_service.calls) == 1


def test_invalid_domain_input_does_not_consume_quota() -> None:
    handler = _StubHandler(RESULT)
    tenant_service = _StubTenantService(
        MessageQuotaDecision(
            allowed=True,
            daily_messages_used=1,
            rate_messages_used=1,
            reason="reserved",
        )
    )
    application = _application(handler, tenant_service=tenant_service)

    with TestClient(application) as client:
        response = client.post(
            _turn_path("not-a-uuid"),
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"content": "A valid message"},
        )

    assert response.status_code == 422
    assert tenant_service.calls == []
    assert handler.calls == []
