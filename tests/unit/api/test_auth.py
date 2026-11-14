from datetime import UTC, datetime

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from enterprise_chat_platform.api.auth import router
from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.api.security import authenticate_access_token
from enterprise_chat_platform.application.tenant_catalog import find_tenant
from enterprise_chat_platform.settings import Settings

RUNTIME_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)
JWT_KEY = "unit-test-signing-key-with-enough-entropy-for-demo-authentication-1234"
JWT_ISSUER = "demo-test-issuer"
JWT_AUDIENCE = "demo-test-audience"


def _settings(*, demo_auth_enabled: bool = True) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="jwt",
        jwt_algorithm="HS256",
        jwt_key=SecretStr(JWT_KEY),
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
        demo_auth_enabled=demo_auth_enabled,
        access_token_expire_minutes=15,
        _env_file=None,
    )


def _application(settings: Settings) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_runtime_settings] = lambda: settings
    return application


def test_demo_login_is_explicitly_disabled_by_default() -> None:
    application = _application(_settings(demo_auth_enabled=False))

    with TestClient(application) as client:
        response = client.post(
            "/auth/login",
            json={"tenant_id": "tenant_1", "role": "user"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Demo authentication is disabled"}


def test_demo_login_rejects_unknown_or_malformed_identity() -> None:
    application = _application(_settings())

    with TestClient(application) as client:
        unknown_response = client.post(
            "/auth/login",
            json={"tenant_id": "unknown-tenant", "role": "user"},
        )
        unsupported_role_response = client.post(
            "/auth/login",
            json={"tenant_id": "tenant_1", "role": "owner"},
        )
        extra_identity_response = client.post(
            "/auth/login",
            json={
                "tenant_id": "tenant_1",
                "role": "user",
                "user_id": "22222222-2222-4222-8222-222222222222",
            },
        )

    assert unknown_response.status_code == 401
    assert unknown_response.json() == {"detail": "Invalid demo identity"}
    assert unsupported_role_response.status_code == 422
    assert extra_identity_response.status_code == 422


def test_demo_login_issues_a_valid_deterministic_short_lived_jwt() -> None:
    settings = _settings()
    application = _application(settings)

    with TestClient(application) as client:
        first_response = client.post(
            "/auth/login",
            json={"tenant_id": "tenant_1", "role": "admin"},
        )
        second_response = client.post(
            "/auth/login",
            json={
                "tenant_id": "11111111-1111-4111-8111-111111111111",
                "role": "admin",
            },
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_body = first_response.json()
    second_body = second_response.json()
    assert first_body["token_type"] == "bearer"
    assert first_body["expires_in"] == 900
    assert first_body["user_info"] == {
        "user_id": second_body["user_info"]["user_id"],
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "tenant_alias": "tenant_1",
        "email": "admin@acme.example",
        "name": "Demo Admin",
        "roles": ["admin"],
    }

    principal = authenticate_access_token(
        settings=settings,
        token=first_body["access_token"],
    )
    tenant = find_tenant("tenant_1")
    assert tenant is not None
    assert principal.actor.tenant_id == tenant.id
    assert str(principal.actor.actor_id) == first_body["user_info"]["user_id"]
    assert principal.roles == {"admin"}

    claims = jwt.decode(
        first_body["access_token"],
        JWT_KEY,
        algorithms=["HS256"],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )
    assert claims["sub"] == claims["user_id"] == first_body["user_info"]["user_id"]
    assert claims["tenant_id"] == first_body["user_info"]["tenant_id"]
    assert claims["roles"] == ["admin"]
    assert claims["exp"] - claims["iat"] == 900
    assert claims["iat"] <= int(datetime.now(UTC).timestamp()) <= claims["exp"]
