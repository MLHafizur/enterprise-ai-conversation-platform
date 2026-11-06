from fastapi import FastAPI
from fastapi.testclient import TestClient

from enterprise_chat_platform.api.health import check_readiness


def test_liveness_contract(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "test-chat-api",
    }


def test_readiness_contract(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "test-chat-api",
    }


def test_liveness_remains_healthy_when_not_ready(application: FastAPI) -> None:
    async def unavailable() -> bool:
        return False

    application.dependency_overrides[check_readiness] = unavailable

    with TestClient(application) as test_client:
        readiness_response = test_client.get("/health/ready")
        liveness_response = test_client.get("/health/live")

    assert readiness_response.status_code == 503
    assert readiness_response.json() == {"detail": "Service is not ready"}

    assert liveness_response.status_code == 200
    assert liveness_response.json() == {
        "status": "ok",
        "service": "test-chat-api",
    }
