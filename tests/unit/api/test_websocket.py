from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.websockets import WebSocketDisconnect

from enterprise_chat_platform.api.websocket import router
from enterprise_chat_platform.application.tenant_catalog import TenantDefinition
from enterprise_chat_platform.infrastructure.persistence.database import AsyncSessionFactory
from enterprise_chat_platform.infrastructure.realtime.connection_manager import (
    RealtimeConnectionManager,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import RealtimeResources
from enterprise_chat_platform.infrastructure.realtime.tenant_service import (
    RedisTenantService,
    TenantQuotaSnapshot,
)
from enterprise_chat_platform.runtime import ApplicationResources
from enterprise_chat_platform.settings import Settings

RUNTIME_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)
JWT_KEY = "unit-test-signing-key-with-enough-entropy-for-websocket-tests-1234"
JWT_ISSUER = "websocket-test-issuer"
JWT_AUDIENCE = "websocket-test-audience"
TENANT_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "22222222-2222-4222-8222-222222222222"


class _StubTenantService:
    def __init__(self, *, reservation_allowed: bool = True) -> None:
        self.reservation_allowed = reservation_allowed
        self.reserve_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self.release_calls: list[str] = []
        self.snapshot_calls = 0

    async def reserve_connection(
        self,
        *,
        tenant: TenantDefinition,
        connection_id: str,
    ) -> bool:
        self.reserve_calls.append(connection_id)
        return self.reservation_allowed

    async def refresh_connection(
        self,
        *,
        tenant: TenantDefinition,
        connection_id: str,
    ) -> None:
        self.refresh_calls.append(connection_id)

    async def release_connection(
        self,
        *,
        tenant: TenantDefinition,
        connection_id: str,
    ) -> None:
        self.release_calls.append(connection_id)

    async def quota_snapshot(self, *, tenant: TenantDefinition) -> TenantQuotaSnapshot:
        self.snapshot_calls += 1
        return TenantQuotaSnapshot(
            current_connections=1,
            max_connections=tenant.max_connections,
            daily_messages_used=0,
            daily_message_limit=tenant.daily_message_limit,
            context_size_limit=tenant.context_size_limit,
        )


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt",
        jwt_algorithm="HS256",
        jwt_key=SecretStr(JWT_KEY),
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
        realtime_enabled=True,
        websocket_allowed_origins="https://dashboard.example.test",
        _env_file=None,
    )


def _token() -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": USER_ID,
            "tenant_id": TENANT_ID,
            "roles": ["user"],
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        JWT_KEY,
        algorithm="HS256",
    )


def _application(tenant_service: _StubTenantService) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    settings = _settings()
    application.state.resources = ApplicationResources(
        settings=settings,
        engine=cast(AsyncEngine, object()),
        session_factory=cast(AsyncSessionFactory, object()),
    )
    application.state.realtime_resources = RealtimeResources(
        client=cast(Redis, object()),
        tenant_service=cast(RedisTenantService, tenant_service),
        connection_manager=RealtimeConnectionManager(),
    )
    return application


def test_websocket_rejects_disallowed_origin_before_authentication() -> None:
    tenant_service = _StubTenantService()
    application = _application(tenant_service)

    with TestClient(application) as client:
        with client.websocket_connect(
            "/ws/browser-1",
            headers={"origin": "https://attacker.example"},
        ) as websocket:
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Origin is not allowed",
            }
            with pytest.raises(WebSocketDisconnect) as captured_disconnect:
                websocket.receive_json()

    assert captured_disconnect.value.code == 4403
    assert tenant_service.reserve_calls == []


def test_websocket_rejects_invalid_token_without_reserving_connection() -> None:
    tenant_service = _StubTenantService()
    application = _application(tenant_service)

    with TestClient(application) as client:
        with client.websocket_connect(
            "/ws/browser-1",
            headers={"origin": "https://dashboard.example.test"},
        ) as websocket:
            websocket.send_json(
                {
                    "type": "authenticate",
                    "token": "not-a-valid-jwt-token",
                }
            )
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Authentication failed",
            }
            with pytest.raises(WebSocketDisconnect) as captured_disconnect:
                websocket.receive_json()

    assert captured_disconnect.value.code == 4401
    assert tenant_service.reserve_calls == []


def test_websocket_denied_quota_is_not_released_as_if_reserved() -> None:
    tenant_service = _StubTenantService(reservation_allowed=False)
    application = _application(tenant_service)

    with TestClient(application) as client:
        with client.websocket_connect(
            "/ws/browser-1",
            headers={"origin": "https://dashboard.example.test"},
        ) as websocket:
            websocket.send_json({"type": "authenticate", "token": _token()})
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Connection quota exceeded",
            }
            with pytest.raises(WebSocketDisconnect) as captured_disconnect:
                websocket.receive_json()

    assert captured_disconnect.value.code == 4429
    assert len(tenant_service.reserve_calls) == 1
    assert tenant_service.release_calls == []


def test_websocket_authenticates_heartbeats_and_releases_reserved_lease() -> None:
    tenant_service = _StubTenantService()
    application = _application(tenant_service)

    with TestClient(application) as client:
        with client.websocket_connect(
            "/ws/browser-1",
            headers={"origin": "https://dashboard.example.test/"},
        ) as websocket:
            websocket.send_json({"type": "authenticate", "token": _token()})
            authenticated = websocket.receive_json()
            assert authenticated["type"] == "authenticated"
            assert authenticated["tenant_id"] == TENANT_ID
            assert authenticated["active_connections"] == 1

            websocket.send_json({"type": "heartbeat"})
            assert websocket.receive_json() == {
                "type": "heartbeat_ack",
                "active_connections": 1,
            }

    assert len(tenant_service.reserve_calls) == 1
    assert tenant_service.refresh_calls == tenant_service.reserve_calls
    assert tenant_service.release_calls == tenant_service.reserve_calls
    assert tenant_service.snapshot_calls == 2
