from fastapi import FastAPI

from enterprise_chat_platform.api.health import router as health_router
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.runtime import (
    DatabaseEngineFactory,
    SessionFactoryBuilder,
    create_application_lifespan,
)
from enterprise_chat_platform.settings import Settings, get_settings


def create_app(
    *,
    settings: Settings | None = None,
    engine_factory: DatabaseEngineFactory = create_database_engine,
    session_factory_builder: SessionFactoryBuilder = create_session_factory,
) -> FastAPI:
    resolved_settings = settings if settings is not None else get_settings()

    application = FastAPI(
        title="Enterprise Chat Platform",
        version="0.1.0",
        debug=resolved_settings.debug,
        lifespan=create_application_lifespan(
            resolved_settings,
            engine_factory=engine_factory,
            session_factory_builder=session_factory_builder,
        ),
    )

    application.include_router(health_router)
    return application
