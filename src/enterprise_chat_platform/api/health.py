import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.runtime import get_application_resources
from enterprise_chat_platform.settings import Settings

router = APIRouter(prefix="/health", tags=["health"])


async def check_readiness(request: Request) -> bool:
    """Return whether PostgreSQL can answer a bounded probe."""
    engine = get_application_resources(request.app).engine

    try:
        async with asyncio.timeout(2):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (TimeoutError, SQLAlchemyError):
        return False

    return True


SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
ReadinessDependency = Annotated[bool, Depends(check_readiness)]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]
    service: str


class ReadinessUnavailableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: Literal["Service is not ready"]


@router.get("/live", response_model=HealthResponse)
async def liveness(settings: SettingsDependency) -> HealthResponse:
    """The process is running and can serve HTTP."""
    return HealthResponse(status="ok", service=settings.service_name)


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessUnavailableResponse,
            "description": "A required dependency is unavailable",
        }
    },
)
async def readiness(settings: SettingsDependency, is_ready: ReadinessDependency) -> HealthResponse:
    """Report whether required service dependencies can serve traffic."""
    if not is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        )
    return HealthResponse(status="ok", service=settings.service_name)
