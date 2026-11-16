from fastapi import FastAPI

from enterprise_chat_platform.api.assistant_profiles import (
    router as assistant_profiles_router,
)
from enterprise_chat_platform.api.auth import router as auth_router
from enterprise_chat_platform.api.conversations import router as conversations_router
from enterprise_chat_platform.api.governance import router as governance_router
from enterprise_chat_platform.api.health import router as health_router
from enterprise_chat_platform.api.human_reviews import router as human_reviews_router
from enterprise_chat_platform.api.policy_notices import router as policy_notices_router
from enterprise_chat_platform.api.queries import router as queries_router
from enterprise_chat_platform.api.tenant import router as tenant_router
from enterprise_chat_platform.api.turns import router as turns_router
from enterprise_chat_platform.api.websocket import router as websocket_router
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import (
    RedisClientFactory,
    create_redis_client,
    wrap_with_realtime_lifespan,
)
from enterprise_chat_platform.observability import (
    RequestObservabilityMiddleware,
    configure_logging,
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
    redis_client_factory: RedisClientFactory = create_redis_client,
) -> FastAPI:
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)

    database_lifespan = create_application_lifespan(
        resolved_settings,
        engine_factory=engine_factory,
        session_factory_builder=session_factory_builder,
    )
    application = FastAPI(
        title="Enterprise Chat Platform",
        version="0.1.0",
        debug=resolved_settings.debug,
        lifespan=wrap_with_realtime_lifespan(
            database_lifespan,
            resolved_settings,
            redis_client_factory=redis_client_factory,
        ),
    )
    application.add_middleware(RequestObservabilityMiddleware)

    application.include_router(auth_router)
    application.include_router(health_router)
    application.include_router(assistant_profiles_router)
    application.include_router(conversations_router)
    application.include_router(turns_router)
    application.include_router(queries_router)
    application.include_router(policy_notices_router)
    application.include_router(governance_router)
    application.include_router(human_reviews_router)
    application.include_router(tenant_router)
    application.include_router(websocket_router)
    return application
