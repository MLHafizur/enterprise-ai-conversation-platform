"""Authentication boundary shared by HTTP and WebSocket transports."""

from dataclasses import dataclass
from typing import Annotated, cast

import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWTError

from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import TenantId, UserId
from enterprise_chat_platform.settings import Settings

_REQUIRED_JWT_CLAIMS = (
    "exp",
    "iat",
    "iss",
    "aud",
    "sub",
    "tenant_id",
)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Trusted identity and optional role claims extracted from one JWT."""

    actor: ActorContext
    roles: frozenset[str]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _parse_actor_ids(*, tenant_id: object, user_id: object) -> ActorContext:
    if not isinstance(tenant_id, str) or not isinstance(user_id, str):
        raise _unauthorized()

    try:
        return ActorContext(
            tenant_id=TenantId.parse(tenant_id),
            actor_id=UserId.parse(user_id),
        )
    except (DomainValidationError, TypeError):
        raise _unauthorized() from None


def _actor_from_development_headers(
    *,
    settings: Settings,
    tenant_id: str | None,
    user_id: str | None,
) -> ActorContext:
    if (
        settings.environment not in {"local", "test"}
        or not settings.allow_insecure_development_auth
    ):
        raise _unauthorized()

    return _parse_actor_ids(
        tenant_id=tenant_id,
        user_id=user_id,
    )


def authenticate_access_token(
    *,
    settings: Settings,
    token: str,
) -> AuthenticatedPrincipal:
    """Validate a raw bearer token without trusting transport-supplied identity."""
    key = settings.jwt_key

    if not token or key is None:
        raise _unauthorized()

    try:
        claims = cast(
            dict[str, object],
            jwt.decode(
                token,
                key.get_secret_value(),
                algorithms=[settings.jwt_algorithm],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
                options={"require": list(_REQUIRED_JWT_CLAIMS)},
            ),
        )
    except PyJWTError:
        raise _unauthorized() from None

    actor = _parse_actor_ids(
        tenant_id=claims.get("tenant_id"),
        user_id=claims.get("sub"),
    )
    raw_roles = claims.get("roles", [])
    if (
        not isinstance(raw_roles, list)
        or len(raw_roles) > 32
        or any(
            not isinstance(role, str) or not role or role != role.strip() or len(role) > 128
            for role in raw_roles
        )
    ):
        raise _unauthorized()
    return AuthenticatedPrincipal(
        actor=actor,
        roles=frozenset(cast(list[str], raw_roles)),
    )


def _principal_from_jwt(
    *,
    settings: Settings,
    authorization: str | None,
) -> AuthenticatedPrincipal:
    if authorization is None:
        raise _unauthorized()

    authorization_parts = authorization.split()

    if len(authorization_parts) != 2 or authorization_parts[0].lower() != "bearer":
        raise _unauthorized()

    return authenticate_access_token(
        settings=settings,
        token=authorization_parts[1],
    )


def get_authenticated_principal(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
    authorization: Annotated[str | None, Header()] = None,
    development_tenant_id: Annotated[
        str | None,
        Header(alias="X-Dev-Tenant-Id"),
    ] = None,
    development_user_id: Annotated[
        str | None,
        Header(alias="X-Dev-User-Id"),
    ] = None,
) -> AuthenticatedPrincipal:
    """Authenticate identity while preserving trusted authorization roles."""
    if settings.auth_mode == "development":
        return AuthenticatedPrincipal(
            actor=_actor_from_development_headers(
                settings=settings,
                tenant_id=development_tenant_id,
                user_id=development_user_id,
            ),
            roles=frozenset(),
        )

    return _principal_from_jwt(
        settings=settings,
        authorization=authorization,
    )


def get_actor_context(
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(get_authenticated_principal),
    ],
) -> ActorContext:
    """Return the authenticated actor for endpoints that do not need roles."""
    return principal.actor
