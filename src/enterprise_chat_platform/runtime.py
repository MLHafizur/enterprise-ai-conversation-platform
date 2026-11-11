"""Application runtime resources and lifespan management."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Lifespan

from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.settings import Settings

type DatabaseEngineFactory = Callable[[Settings], AsyncEngine]
type SessionFactoryBuilder = Callable[[AsyncEngine], AsyncSessionFactory]


@dataclass(frozen=True, slots=True)
class ApplicationResources:
    """Process-scoped resources owned by the application lifespan."""

    settings: Settings
    engine: AsyncEngine
    session_factory: AsyncSessionFactory


def create_application_lifespan(
    settings: Settings,
    *,
    engine_factory: DatabaseEngineFactory,
    session_factory_builder: SessionFactoryBuilder,
) -> Lifespan[FastAPI]:
    """Create the lifespan responsible for runtime resource ownership."""

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        engine = engine_factory(settings)
        resources_installed = False

        try:
            session_factory = session_factory_builder(engine)
            application.state.resources = ApplicationResources(
                settings=settings,
                engine=engine,
                session_factory=session_factory,
            )
            resources_installed = True

            yield
        finally:
            try:
                await engine.dispose()
            finally:
                if resources_installed:
                    del application.state.resources

    return lifespan


def get_application_resources(
    application: FastAPI,
) -> ApplicationResources:
    """Return resources while the application lifespan is active."""
    resources: object = getattr(
        application.state,
        "resources",
        None,
    )

    if not isinstance(resources, ApplicationResources):
        raise RuntimeError("Application resources are unavailable outside the active lifespan")

    return resources
