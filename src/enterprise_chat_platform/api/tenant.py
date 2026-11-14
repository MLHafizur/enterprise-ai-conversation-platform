"""Tenant branding and ephemeral quota status for the bundled dashboard."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.tenant_catalog import find_tenant
from enterprise_chat_platform.infrastructure.realtime.runtime import get_realtime_resources

router = APIRouter(prefix="/v1/tenant", tags=["tenant"])


class TenantBrandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brand_name: str
    response_tone: str
    primary_color: str


class TenantInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    alias: str
    name: str
    domain: str
    brand: TenantBrandResponse


class TenantQuotaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_connections: int = Field(ge=0)
    max_connections: int = Field(gt=0)
    daily_messages_used: int = Field(ge=0)
    daily_message_limit: int = Field(gt=0)
    context_size_limit: int = Field(gt=0)


class TenantDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_info: TenantInfoResponse
    quota_info: TenantQuotaResponse


@router.get("/info", response_model=TenantDashboardResponse)
async def tenant_info(
    request: Request,
    actor: Annotated[ActorContext, Depends(get_actor_context)],
) -> TenantDashboardResponse:
    tenant = find_tenant(actor.tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant information was not found",
        )

    try:
        snapshot = await get_realtime_resources(request.app).tenant_service.quota_snapshot(
            tenant=tenant
        )
        current_connections = snapshot.current_connections
        daily_messages_used = snapshot.daily_messages_used
    except (RedisError, RuntimeError):
        current_connections = 0
        daily_messages_used = 0

    return TenantDashboardResponse(
        tenant_info=TenantInfoResponse(
            id=str(tenant.id),
            alias=tenant.alias,
            name=tenant.name,
            domain=tenant.domain,
            brand=TenantBrandResponse(
                brand_name=tenant.brand_name,
                response_tone=tenant.response_tone,
                primary_color=tenant.primary_color,
            ),
        ),
        quota_info=TenantQuotaResponse(
            current_connections=current_connections,
            max_connections=tenant.max_connections,
            daily_messages_used=daily_messages_used,
            daily_message_limit=tenant.daily_message_limit,
            context_size_limit=tenant.context_size_limit,
        ),
    )
