"""FastAPI dependencies backed by application-lifespan resources."""

from fastapi import Request

from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.runtime import get_application_resources
from enterprise_chat_platform.settings import Settings


def get_runtime_settings(request: Request) -> Settings:
    """Return the exact settings object used to create this application."""
    return get_application_resources(request.app).settings


def get_runtime_session_factory(request: Request) -> AsyncSessionFactory:
    """Return the process-scoped factory used to create request sessions."""
    return get_application_resources(request.app).session_factory
