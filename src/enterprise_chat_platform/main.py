from fastapi import FastAPI

from enterprise_chat_platform.api.health import router as health_router
from enterprise_chat_platform.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="Enterprise Chat Platform",
        version="0.1.0",
        debug=settings.debug,
    )

    application.include_router(health_router)
    return application


#app = create_app()