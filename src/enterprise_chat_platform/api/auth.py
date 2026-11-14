"""Explicitly gated local login used by the bundled browser demonstration."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.application.tenant_catalog import demo_user_id, find_tenant
from enterprise_chat_platform.settings import Settings

router = APIRouter(prefix="/auth", tags=["authentication"])

type DemoRole = Literal["user", "admin", "agent"]


class DemoLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tenant_id: str
    role: DemoRole = "user"


class DemoUserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    tenant_id: UUID
    tenant_alias: str
    email: str
    name: str
    roles: tuple[DemoRole, ...]


class DemoLoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user_info: DemoUserInfo


@router.post(
    "/login",
    response_model=DemoLoginResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Demo login is disabled"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Unknown demo identity"},
    },
)
async def demo_login(
    request_body: DemoLoginRequest,
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> DemoLoginResponse:
    """Issue a short-lived local JWT only when the operator opted in."""
    if not settings.demo_auth_enabled or settings.environment not in {"local", "test"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo authentication is disabled",
        )

    tenant = find_tenant(request_body.tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid demo identity",
        )

    key = settings.jwt_key
    if key is None:
        raise RuntimeError("Validated demo authentication is missing its signing key")

    user_id = demo_user_id(tenant=tenant, role=request_body.role)
    now = datetime.now(UTC)
    expires_in = settings.access_token_expire_minutes * 60
    roles: tuple[DemoRole, ...] = (request_body.role,)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "user_id": str(user_id),
            "tenant_id": str(tenant.id),
            "roles": list(roles),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        },
        key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    return DemoLoginResponse(
        access_token=token,
        expires_in=expires_in,
        user_info=DemoUserInfo(
            user_id=user_id.value,
            tenant_id=tenant.id.value,
            tenant_alias=tenant.alias,
            email=f"{request_body.role}@{tenant.domain}",
            name=f"Demo {request_body.role.title()}",
            roles=roles,
        ),
    )
