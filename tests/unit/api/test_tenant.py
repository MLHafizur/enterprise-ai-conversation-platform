from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.api.tenant import router
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.tenant_catalog import TenantDefinition
from enterprise_chat_platform.domain.identifiers import TenantId, UserId
from enterprise_chat_platform.infrastructure.realtime.connection_manager import (
    RealtimeConnectionManager,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import RealtimeResources
from enterprise_chat_platform.infrastructure.realtime.tenant_service import (
    RedisTenantService,
    TenantQuotaSnapshot,
)

ACTOR = ActorContext(
    tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
    actor_id=UserId.parse("22222222-2222-4222-8222-222222222222"),
)


class _StubTenantService:
    def __init__(self, outcome: TenantQuotaSnapshot | Exception) -> None:
        self._outcome = outcome
        self.calls: list[TenantDefinition] = []

    async def quota_snapshot(self, *, tenant: TenantDefinition) -> TenantQuotaSnapshot:
        self.calls.append(tenant)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _application(
    *,
    actor: ActorContext = ACTOR,
    tenant_service: _StubTenantService | None = None,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_actor_context] = lambda: actor
    if tenant_service is not None:
        application.state.realtime_resources = RealtimeResources(
            client=cast(Redis, object()),
            tenant_service=cast(RedisTenantService, tenant_service),
            connection_manager=cast(RealtimeConnectionManager, object()),
        )
    return application


def test_tenant_dashboard_returns_allowlisted_branding_and_live_quota() -> None:
    tenant_service = _StubTenantService(
        TenantQuotaSnapshot(
            current_connections=3,
            max_connections=50,
            daily_messages_used=47,
            daily_message_limit=5000,
            context_size_limit=4096,
        )
    )
    application = _application(tenant_service=tenant_service)

    with TestClient(application) as client:
        response = client.get("/v1/tenant/info")

    assert response.status_code == 200
    assert response.json() == {
        "tenant_info": {
            "id": "11111111-1111-4111-8111-111111111111",
            "alias": "tenant_1",
            "name": "Acme Corp",
            "domain": "acme.example",
            "brand": {
                "brand_name": "Acme Assistant",
                "response_tone": "professional",
                "primary_color": "#4f46e5",
            },
        },
        "quota_info": {
            "current_connections": 3,
            "max_connections": 50,
            "daily_messages_used": 47,
            "daily_message_limit": 5000,
            "context_size_limit": 4096,
        },
    }
    assert len(tenant_service.calls) == 1


def test_tenant_dashboard_degrades_ephemeral_quota_when_redis_is_unavailable() -> None:
    tenant_service = _StubTenantService(RedisError("redis unavailable"))
    application = _application(tenant_service=tenant_service)

    with TestClient(application) as client:
        response = client.get("/v1/tenant/info")

    assert response.status_code == 200
    assert response.json()["quota_info"]["current_connections"] == 0
    assert response.json()["quota_info"]["daily_messages_used"] == 0


def test_tenant_dashboard_rejects_tenants_outside_the_catalog() -> None:
    unknown_actor = ActorContext(
        tenant_id=TenantId.parse("99999999-9999-4999-8999-999999999999"),
        actor_id=ACTOR.actor_id,
    )
    application = _application(actor=unknown_actor)

    with TestClient(application) as client:
        response = client.get("/v1/tenant/info")

    assert response.status_code == 404
    assert response.json() == {"detail": "Tenant information was not found"}
