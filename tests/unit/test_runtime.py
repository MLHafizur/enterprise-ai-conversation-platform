from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.main import create_app
from enterprise_chat_platform.runtime import get_application_resources
from enterprise_chat_platform.settings import Settings

TEST_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:runtime-test-password@127.0.0.1:55432/enterprise_chat"
)


class _RecordingEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def dispose(self) -> None:
        self._events.append("dispose")


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(TEST_DATABASE_URL),
        _env_file=None,
    )


def test_database_resources_follow_application_lifespan() -> None:
    events: list[str] = []
    settings = _settings()
    recording_engine = _RecordingEngine(events)
    engine = cast(AsyncEngine, recording_engine)
    session_factory = cast(AsyncSessionFactory, object())

    def build_engine(received_settings: Settings) -> AsyncEngine:
        assert received_settings is settings
        events.append("engine")
        return engine

    def build_session_factory(
        received_engine: AsyncEngine,
    ) -> AsyncSessionFactory:
        assert received_engine is engine
        events.append("session_factory")
        return session_factory

    application = create_app(
        settings=settings,
        engine_factory=build_engine,
        session_factory_builder=build_session_factory,
    )

    assert events == []

    with pytest.raises(
        RuntimeError,
        match="Application resources are unavailable",
    ):
        get_application_resources(application)

    with TestClient(application):
        assert events == ["engine", "session_factory"]

        resources = get_application_resources(application)

        assert resources.settings is settings
        assert resources.engine is engine
        assert resources.session_factory is session_factory

    assert events == ["engine", "session_factory", "dispose"]

    with pytest.raises(
        RuntimeError,
        match="Application resources are unavailable",
    ):
        get_application_resources(application)


def test_partial_startup_failure_disposes_engine() -> None:
    events: list[str] = []
    settings = _settings()
    recording_engine = _RecordingEngine(events)
    engine = cast(AsyncEngine, recording_engine)

    def build_engine(received_settings: Settings) -> AsyncEngine:
        assert received_settings is settings
        events.append("engine")
        return engine

    def fail_session_factory(
        received_engine: AsyncEngine,
    ) -> AsyncSessionFactory:
        assert received_engine is engine
        events.append("session_factory")
        raise RuntimeError("session factory failed")

    application = create_app(
        settings=settings,
        engine_factory=build_engine,
        session_factory_builder=fail_session_factory,
    )

    with pytest.raises(
        RuntimeError,
        match="session factory failed",
    ):
        with TestClient(application):
            pass

    assert events == ["engine", "session_factory", "dispose"]

    with pytest.raises(
        RuntimeError,
        match="Application resources are unavailable",
    ):
        get_application_resources(application)
