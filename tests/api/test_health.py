from types import TracebackType
from typing import Self, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from enterprise_chat_platform.api.health import check_readiness, router
from enterprise_chat_platform.infrastructure.persistence.database import AsyncSessionFactory
from enterprise_chat_platform.infrastructure.realtime.connection_manager import (
    RealtimeConnectionManager,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import RealtimeResources
from enterprise_chat_platform.infrastructure.realtime.tenant_service import RedisTenantService
from enterprise_chat_platform.runtime import ApplicationResources
from enterprise_chat_platform.settings import Settings

RUNTIME_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)


class _HealthyConnection:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def execute(self, statement: object) -> None:
        return None


class _HealthyEngine:
    def connect(self) -> _HealthyConnection:
        return _HealthyConnection()


class _FakeRedis:
    def __init__(self, *, error: RedisError | None = None) -> None:
        self._error = error
        self.ping_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self._error is not None:
            raise self._error
        return True


def _readiness_settings(*, realtime_enabled: bool) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt" if realtime_enabled else "development",
        jwt_algorithm="HS256",
        jwt_key=SecretStr("health-test-key") if realtime_enabled else None,
        realtime_enabled=realtime_enabled,
        _env_file=None,
    )


def _readiness_application(
    *,
    realtime_enabled: bool,
    redis_client: _FakeRedis | None = None,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.state.resources = ApplicationResources(
        settings=_readiness_settings(realtime_enabled=realtime_enabled),
        engine=cast(AsyncEngine, _HealthyEngine()),
        session_factory=cast(AsyncSessionFactory, object()),
    )
    if redis_client is not None:
        application.state.realtime_resources = RealtimeResources(
            client=cast(Redis, redis_client),
            tenant_service=cast(RedisTenantService, object()),
            connection_manager=cast(RealtimeConnectionManager, object()),
        )
    return application


def test_liveness_contract(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "test-chat-api",
    }


def test_readiness_contract(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "test-chat-api",
    }


def test_liveness_remains_healthy_when_not_ready(application: FastAPI) -> None:
    async def unavailable() -> bool:
        return False

    application.dependency_overrides[check_readiness] = unavailable

    with TestClient(application) as test_client:
        readiness_response = test_client.get("/health/ready")
        liveness_response = test_client.get("/health/live")

    assert readiness_response.status_code == 503
    assert readiness_response.json() == {"detail": "Service is not ready"}

    assert liveness_response.status_code == 200
    assert liveness_response.json() == {
        "status": "ok",
        "service": "test-chat-api",
    }


def test_readiness_skips_redis_when_realtime_is_disabled() -> None:
    application = _readiness_application(realtime_enabled=False)

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200


def test_readiness_requires_healthy_redis_when_realtime_is_enabled() -> None:
    healthy_redis = _FakeRedis()
    healthy_application = _readiness_application(
        realtime_enabled=True,
        redis_client=healthy_redis,
    )
    missing_application = _readiness_application(realtime_enabled=True)
    failed_application = _readiness_application(
        realtime_enabled=True,
        redis_client=_FakeRedis(error=RedisError("redis unavailable")),
    )

    with TestClient(healthy_application) as client:
        healthy_response = client.get("/health/ready")
    with TestClient(missing_application) as client:
        missing_response = client.get("/health/ready")
    with TestClient(failed_application) as client:
        failed_response = client.get("/health/ready")

    assert healthy_response.status_code == 200
    assert healthy_redis.ping_calls == 1
    assert missing_response.status_code == 503
    assert failed_response.status_code == 503
