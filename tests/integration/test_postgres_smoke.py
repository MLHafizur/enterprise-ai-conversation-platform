import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
)
from enterprise_chat_platform.settings import Settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_database_can_execute_query() -> None:
    settings = Settings()
    database_url = make_url(settings.database_url.get_secret_value())

    # Safety guard: this test must never target a remote database.
    assert settings.environment in {"local", "test"}
    assert database_url.host in {"127.0.0.1", "localhost"}

    engine = create_database_engine(settings)

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                result = await connection.execute(text("SELECT 1"))

                assert result.scalar_one() == 1
    finally:
        await engine.dispose()
