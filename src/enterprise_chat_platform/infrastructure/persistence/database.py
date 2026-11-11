"""Construction of asynchronous SQLAlchemy database resources."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from enterprise_chat_platform.settings import Settings

type AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create the process-scoped asynchronous database engine."""
    database_url = make_url(settings.database_url.get_secret_value())

    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        hide_parameters=True,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> AsyncSessionFactory:
    """Create a factory that produces independent database sessions."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )
