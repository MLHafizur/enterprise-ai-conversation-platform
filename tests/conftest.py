from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from enterprise_chat_platform.main import create_app
from enterprise_chat_platform.settings import get_settings

TEST_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:test-password@127.0.0.1:55432/enterprise_chat"
)


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("CHAT_ENVIRONMENT", "test")
    monkeypatch.setenv("CHAT_SERVICE_NAME", "test-chat-api")
    monkeypatch.setenv("CHAT_LOG_LEVEL", "INFO")
    monkeypatch.setenv("CHAT_DEBUG", "false")
    monkeypatch.setenv("CHAT_DATABASE_URL", TEST_DATABASE_URL)

    app = create_app()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client
