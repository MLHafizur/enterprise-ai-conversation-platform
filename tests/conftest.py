from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from enterprise_chat_platform.api.health import check_readiness
from enterprise_chat_platform.main import create_app
from enterprise_chat_platform.settings import Settings, get_settings

TEST_DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:test-password@127.0.0.1:55432/enterprise_chat"
)


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def application() -> Iterator[FastAPI]:
    settings = Settings(
        environment="test",
        service_name="test-chat-api",
        log_level="INFO",
        debug=False,
        database_url=SecretStr(TEST_DATABASE_URL),
        auth_mode="development",
        allow_insecure_development_auth=True,
        demo_auth_enabled=False,
        realtime_enabled=False,
        provider_mode="disabled",
        _env_file=None,
    )

    app = create_app(settings=settings)
    app.dependency_overrides[check_readiness] = lambda: True

    yield app

    app.dependency_overrides.clear()


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client
