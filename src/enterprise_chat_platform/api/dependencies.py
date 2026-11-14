"""FastAPI dependencies backed by application-lifespan resources."""

from fastapi import Request

from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import get_realtime_resources
from enterprise_chat_platform.infrastructure.realtime.tenant_service import RedisTenantService
from enterprise_chat_platform.runtime import get_application_resources
from enterprise_chat_platform.settings import Settings


def get_runtime_settings(request: Request) -> Settings:
    """Return the exact settings object used to create this application."""
    return get_application_resources(request.app).settings


def get_runtime_session_factory(request: Request) -> AsyncSessionFactory:
    """Return the process-scoped factory used to create request sessions."""
    return get_application_resources(request.app).session_factory


def get_optional_tenant_service(request: Request) -> RedisTenantService | None:
    """Return ephemeral quota coordination when realtime mode is enabled."""
    try:
        return get_realtime_resources(request.app).tenant_service
    except RuntimeError:
        return None
