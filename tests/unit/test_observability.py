from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr

from enterprise_chat_platform.main import create_app
from enterprise_chat_platform.observability import (
    PACKAGE_LOGGER_NAME,
    JsonLogFormatter,
    RequestObservabilityMiddleware,
    configure_logging,
    get_request_id,
)
from enterprise_chat_platform.settings import Settings

DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:test-password@127.0.0.1:55432/enterprise_chat"
)


class _RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def request_logger() -> Iterator[tuple[logging.Logger, _RecordHandler]]:
    logger = logging.getLogger("tests.enterprise_chat_platform.http")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    handler = _RecordHandler()
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    yield logger, handler

    logger.handlers = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate
    handler.close()


def _observed_app(logger: logging.Logger) -> FastAPI:
    application = FastAPI()
    application.add_middleware(RequestObservabilityMiddleware, logger=logger)
    return application


def test_request_id_is_server_generated_and_safe_metadata_is_logged(
    request_logger: tuple[logging.Logger, _RecordHandler],
) -> None:
    logger, handler = request_logger
    application = _observed_app(logger)

    @application.post("/items/{item_id}")
    async def create_item(item_id: str, request: Request) -> dict[str, str]:
        await request.body()
        return {
            "item_id": item_id,
            "request_id": get_request_id() or "missing",
        }

    with TestClient(application) as client:
        response = client.post(
            "/items/private-item?access_token=query-secret",
            headers={
                "Authorization": "Bearer jwt-secret",
                "X-Request-ID": "attacker-controlled",
            },
            content="prompt-secret",
        )

    response_request_id = response.headers["X-Request-ID"]
    assert UUID(response_request_id).version == 4
    assert response_request_id != "attacker-controlled"
    assert response.json()["request_id"] == response_request_id
    assert get_request_id() is None

    assert len(handler.records) == 1
    record = handler.records[0]
    assert record.getMessage() == "http.request.completed"
    assert record.request_id == response_request_id  # type: ignore[attr-defined]
    assert record.method == "POST"  # type: ignore[attr-defined]
    assert record.route == "/items/{item_id}"  # type: ignore[attr-defined]
    assert record.status_code == 200  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, float)  # type: ignore[attr-defined]

    serialized = JsonLogFormatter(service_name="test-service").format(record)
    for secret in (
        "private-item",
        "query-secret",
        "jwt-secret",
        "prompt-secret",
        "attacker-controlled",
    ):
        assert secret not in serialized


def test_streaming_response_is_not_buffered_or_rewritten(
    request_logger: tuple[logging.Logger, _RecordHandler],
) -> None:
    logger, handler = request_logger
    application = _observed_app(logger)
    request_ids_seen_while_streaming: list[str | None] = []

    @application.get("/stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            request_ids_seen_while_streaming.append(get_request_id())
            yield b"first"
            request_ids_seen_while_streaming.append(get_request_id())
            yield b"-second"

        return StreamingResponse(chunks())

    with TestClient(application) as client:
        response = client.get("/stream")

    assert response.content == b"first-second"
    assert request_ids_seen_while_streaming == [
        response.headers["X-Request-ID"],
        response.headers["X-Request-ID"],
    ]
    assert len(handler.records) == 1
    assert handler.records[0].status_code == 200  # type: ignore[attr-defined]


def test_unhandled_exception_logs_class_and_stack_without_exception_text(
    request_logger: tuple[logging.Logger, _RecordHandler],
) -> None:
    logger, handler = request_logger
    application = _observed_app(logger)

    @application.get("/failure")
    async def failure() -> None:
        raise RuntimeError("database-password-must-not-be-logged")

    with TestClient(application, raise_server_exceptions=True) as client:
        with pytest.raises(RuntimeError, match="database-password-must-not-be-logged"):
            client.get("/failure")

    assert len(handler.records) == 1
    record = handler.records[0]
    payload = json.loads(JsonLogFormatter(service_name="test-service").format(record))

    assert payload["event"] == "http.request.failed"
    assert payload["exception_class"] == "RuntimeError"
    assert payload["route"] == "/failure"
    assert payload["status_code"] == 500
    assert payload["stack"]
    assert "database-password-must-not-be-logged" not in json.dumps(payload)


def test_repeated_app_creation_keeps_one_managed_handler() -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    original_handlers = package_logger.handlers[:]
    original_level = package_logger.level
    original_propagate = package_logger.propagate
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        service_name="observability-test-service",
        log_level="WARNING",
        _env_file=None,
    )

    try:
        create_app(settings=settings)
        create_app(settings=settings)

        managed_handlers = [
            handler
            for handler in package_logger.handlers
            if getattr(handler, "_enterprise_chat_json_handler", False)
        ]
        assert len(managed_handlers) == 1
        assert managed_handlers[0].level == logging.WARNING
        assert package_logger.level == logging.WARNING
    finally:
        for handler in package_logger.handlers:
            if handler not in original_handlers:
                handler.close()
        package_logger.handlers = original_handlers
        package_logger.setLevel(original_level)
        package_logger.propagate = original_propagate


def test_uvicorn_loggers_cannot_bypass_redaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    error_logger = logging.getLogger("uvicorn.error")
    access_logger = logging.getLogger("uvicorn.access")
    original_error_handlers = error_logger.handlers[:]
    original_error_level = error_logger.level
    original_error_propagate = error_logger.propagate
    original_access_handlers = access_logger.handlers[:]
    original_access_disabled = access_logger.disabled
    original_access_propagate = access_logger.propagate
    settings = Settings(
        database_url=SecretStr(DATABASE_URL),
        service_name="observability-test-service",
        _env_file=None,
    )

    try:
        configure_logging(settings)
        try:
            raise RuntimeError("query-token-and-row-content-must-stay-secret")
        except RuntimeError:
            error_logger.exception("Exception in ASGI application")

        output = capsys.readouterr().err
        payload = json.loads(output.strip().splitlines()[-1])
        assert payload["logger"] == "uvicorn.error"
        assert payload["exception_class"] == "RuntimeError"
        assert payload["event"] == "Exception in ASGI application"
        assert "query-token-and-row-content-must-stay-secret" not in output
        assert access_logger.disabled is True
        assert access_logger.propagate is False
    finally:
        for handler in error_logger.handlers:
            if handler not in original_error_handlers:
                handler.close()
        error_logger.handlers = original_error_handlers
        error_logger.setLevel(original_error_level)
        error_logger.propagate = original_error_propagate
        access_logger.disabled = original_access_disabled
        access_logger.propagate = original_access_propagate
        access_logger.handlers = original_access_handlers
