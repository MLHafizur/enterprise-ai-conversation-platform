from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.settings import Settings

RUNTIME_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)
TENANT_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "22222222-2222-4222-8222-222222222222"
JWT_KEY = "unit-test-signing-key-with-enough-entropy-for-all-hmac-tests-1234"
JWT_ISSUER = "unit-test-issuer"
JWT_AUDIENCE = "unit-test-audience"


def _settings(
    *,
    auth_mode: Literal["development", "jwt"],
) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode=auth_mode,
        allow_insecure_development_auth=auth_mode == "development",
        jwt_algorithm="HS256",
        jwt_key=SecretStr(JWT_KEY) if auth_mode == "jwt" else None,
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
        _env_file=None,
    )


def _application(settings: Settings) -> FastAPI:
    application = FastAPI()
    application.dependency_overrides[get_runtime_settings] = lambda: settings

    @application.get("/protected")
    async def protected(
        actor: Annotated[ActorContext, Depends(get_actor_context)],
    ) -> dict[str, str]:
        return {
            "tenant_id": str(actor.tenant_id),
            "actor_id": str(actor.actor_id),
        }

    return application


def _jwt_token(
    *,
    algorithm: str = "HS256",
    remove_claim: str | None = None,
    issuer: str = JWT_ISSUER,
    audience: str = JWT_AUDIENCE,
    subject: object = USER_ID,
    tenant_id: object = TENANT_ID,
    expired: bool = False,
) -> str:
    issued_at = datetime.now(UTC)
    claims: dict[str, object] = {
        "exp": issued_at + timedelta(minutes=-5 if expired else 5),
        "iat": issued_at,
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "tenant_id": tenant_id,
    }

    if remove_claim is not None:
        claims.pop(remove_claim)

    return jwt.encode(
        claims,
        JWT_KEY,
        algorithm=algorithm,
    )


def _assert_generic_unauthorized(response_body: str, status_code: int, authenticate: str) -> None:
    assert status_code == 401
    assert response_body == '{"detail":"Unauthorized"}'
    assert authenticate == "Bearer"


def test_development_headers_create_trusted_actor_context() -> None:
    application = _application(_settings(auth_mode="development"))

    with TestClient(application) as client:
        response = client.get(
            "/protected",
            headers={
                "X-Dev-Tenant-Id": TENANT_ID,
                "X-Dev-User-Id": USER_ID,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": TENANT_ID,
        "actor_id": USER_ID,
    }


def test_development_headers_are_rejected_without_explicit_opt_in() -> None:
    settings = Settings(
        environment="local",
        database_url=SecretStr(RUNTIME_DATABASE_URL),
        auth_mode="development",
        allow_insecure_development_auth=False,
        _env_file=None,
    )

    with TestClient(_application(settings)) as client:
        response = client.get(
            "/protected",
            headers={
                "X-Dev-Tenant-Id": TENANT_ID,
                "X-Dev-User-Id": USER_ID,
            },
        )

    _assert_generic_unauthorized(
        response.text,
        response.status_code,
        response.headers["WWW-Authenticate"],
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {
            "X-Dev-Tenant-Id": "not-a-uuid",
            "X-Dev-User-Id": USER_ID,
        },
        {
            "X-Dev-Tenant-Id": TENANT_ID,
            "X-Dev-User-Id": "00000000-0000-0000-0000-000000000000",
        },
    ],
)
def test_invalid_development_identity_returns_generic_unauthorized(
    headers: dict[str, str],
) -> None:
    application = _application(_settings(auth_mode="development"))

    with TestClient(application) as client:
        response = client.get("/protected", headers=headers)

    _assert_generic_unauthorized(
        response.text,
        response.status_code,
        response.headers["WWW-Authenticate"],
    )
    assert "not-a-uuid" not in response.text


def test_valid_jwt_creates_trusted_actor_context() -> None:
    application = _application(_settings(auth_mode="jwt"))

    with TestClient(application) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {_jwt_token()}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": TENANT_ID,
        "actor_id": USER_ID,
    }


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub", "tenant_id"])
def test_jwt_requires_every_identity_and_validation_claim(claim: str) -> None:
    application = _application(_settings(auth_mode="jwt"))
    token = _jwt_token(remove_claim=claim)

    with TestClient(application) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )

    _assert_generic_unauthorized(
        response.text,
        response.status_code,
        response.headers["WWW-Authenticate"],
    )
    assert token not in response.text


@pytest.mark.parametrize(
    "token",
    [
        _jwt_token(algorithm="HS384"),
        _jwt_token(issuer="wrong-issuer"),
        _jwt_token(audience="wrong-audience"),
        _jwt_token(subject="not-a-uuid"),
        _jwt_token(tenant_id="not-a-uuid"),
        _jwt_token(expired=True),
        "not-a-token",
    ],
)
def test_invalid_jwt_returns_generic_unauthorized(token: str) -> None:
    application = _application(_settings(auth_mode="jwt"))

    with TestClient(application) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )

    _assert_generic_unauthorized(
        response.text,
        response.status_code,
        response.headers["WWW-Authenticate"],
    )
    assert token not in response.text


def test_development_headers_are_ignored_in_jwt_mode() -> None:
    application = _application(_settings(auth_mode="jwt"))

    with TestClient(application) as client:
        response = client.get(
            "/protected",
            headers={
                "X-Dev-Tenant-Id": TENANT_ID,
                "X-Dev-User-Id": USER_ID,
            },
        )

    _assert_generic_unauthorized(
        response.text,
        response.status_code,
        response.headers["WWW-Authenticate"],
    )
