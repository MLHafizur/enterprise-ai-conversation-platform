from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from enterprise_chat_platform.infrastructure.persistence import database
from enterprise_chat_platform.settings import Settings

TEST_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(TEST_DATABASE_URL),
        _env_file=None,
    )


def test_create_database_engine_uses_safe_runtime_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_url: str | URL | None = None
    captured_options: dict[str, object] = {}
    expected_engine = cast(AsyncEngine, object())

    def fake_create_async_engine(
        url: str | URL,
        **options: object,
    ) -> AsyncEngine:
        nonlocal captured_url, captured_options

        captured_url = url
        captured_options = options
        return expected_engine

    monkeypatch.setattr(
        database,
        "create_async_engine",
        fake_create_async_engine,
    )

    engine = database.create_database_engine(_settings())

    assert engine is expected_engine
    assert isinstance(captured_url, URL)
    assert captured_url.drivername == "postgresql+asyncpg"
    assert captured_url.username == "enterprise_chat_app"
    assert captured_url.password == "runtime-test-password"
    assert captured_url.host == "127.0.0.1"
    assert captured_url.port == 55432
    assert captured_url.database == "enterprise_chat"

    assert "runtime-test-password" not in str(captured_url)
    assert "runtime-test-password" not in repr(captured_url)

    assert captured_options["pool_pre_ping"] is True
    assert captured_options["hide_parameters"] is True


@pytest.mark.asyncio
async def test_session_factory_creates_isolated_sessions() -> None:
    engine = database.create_database_engine(_settings())
    session_factory = database.create_session_factory(engine)

    try:
        async with session_factory() as first_session:
            async with session_factory() as second_session:
                assert isinstance(first_session, AsyncSession)
                assert first_session is not second_session
                assert first_session.bind is engine
                assert first_session.autoflush is False
                assert first_session.sync_session.expire_on_commit is False
    finally:
        await engine.dispose()
