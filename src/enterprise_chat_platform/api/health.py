from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from enterprise_chat_platform.settings import Settings, get_settings

router = APIRouter(prefix="/health", tags=["health"])


async def check_readiness() -> bool:
    """Check required service dependencies.
    No external dependencies exist yet, so the servce is ready/
    """
    return True


SettingsDependency = Annotated[Settings, Depends(get_settings)]
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
    """The service currently has no external readiness dependencies."""
    if not is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        )
    return HealthResponse(status="ok", service=settings.service_name)
