"""Safe, dependency-free observability for the HTTP service.

The middleware deliberately records only a small allow-list of request metadata.
Request headers, query strings, bodies, and exception messages never enter a log
record here.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from traceback import extract_tb
from typing import Final
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from enterprise_chat_platform.settings import Settings

PACKAGE_LOGGER_NAME: Final = "enterprise_chat_platform"
REQUEST_ID_HEADER: Final = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES: Final = REQUEST_ID_HEADER.lower().encode("ascii")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the request ID bound to the current asynchronous context."""

    return _request_id.get()


class JsonLogFormatter(logging.Formatter):
    """Format a strict allow-list of log fields as one JSON object per line."""

    _optional_fields: Final = (
        "request_id",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "exception_class",
    )

    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "event": record.getMessage(),
        }

        for field_name in self._optional_fields:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value

        if record.exc_info is not None:
            exception_type, _, traceback = record.exc_info
            if "exception_class" not in payload and exception_type is not None:
                payload["exception_class"] = exception_type.__name__
            if traceback is not None:
                payload["stack"] = [
                    {
                        "file": frame.filename,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in extract_tb(traceback)
                ]

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _DynamicStderrHandler(logging.Handler):
    """Write to the current stderr, avoiding stale test-capture streams."""

    _enterprise_chat_json_handler = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(f"{self.format(record)}\n")
            sys.stderr.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure one reusable JSON handler for all service loggers."""

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    level = logging.getLevelNamesMapping()[settings.log_level]
    _configure_json_logger(
        logger,
        level=level,
        service_name=settings.service_name,
    )

    # Uvicorn is configured before it imports the app. Replace its raw error
    # formatter here so re-raised ASGI exceptions cannot leak exception text.
    _configure_json_logger(
        logging.getLogger("uvicorn.error"),
        level=level,
        service_name=settings.service_name,
        replace_existing=True,
    )
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.disabled = True
    access_logger.propagate = False
    access_logger.handlers.clear()
    return logger


def _configure_json_logger(
    logger: logging.Logger,
    *,
    level: int,
    service_name: str,
    replace_existing: bool = False,
) -> None:
    logger.setLevel(level)
    logger.disabled = False
    logger.propagate = False

    if replace_existing:
        logger.handlers = [
            handler
            for handler in logger.handlers
            if getattr(handler, "_enterprise_chat_json_handler", False)
        ]

    managed_handlers = [
        handler
        for handler in logger.handlers
        if getattr(handler, "_enterprise_chat_json_handler", False)
    ]
    if managed_handlers:
        handler = managed_handlers[0]
        for duplicate in managed_handlers[1:]:
            logger.removeHandler(duplicate)
            duplicate.close()
    else:
        handler = _DynamicStderrHandler()
        logger.addHandler(handler)

    handler.setLevel(level)
    handler.setFormatter(JsonLogFormatter(service_name=service_name))


def _route_path(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "<unmatched>"


class RequestObservabilityMiddleware:
    """Add a server-generated request ID and safe request lifecycle logs."""

    def __init__(self, app: ASGIApp, *, logger: logging.Logger | None = None) -> None:
        self._app = app
        self._logger = logger or logging.getLogger(f"{PACKAGE_LOGGER_NAME}.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = str(uuid4())
        token = _request_id.set(request_id)
        started_at = perf_counter()
        status_code = 500
        failed = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _REQUEST_ID_HEADER_BYTES
                ]
                headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        except Exception as error:
            failed = True
            self._logger.error(
                "http.request.failed",
                extra=self._log_fields(
                    scope=scope,
                    request_id=request_id,
                    status_code=status_code,
                    started_at=started_at,
                    exception_class=type(error).__name__,
                ),
                exc_info=(type(error), error, error.__traceback__),
            )
            raise
        finally:
            if not failed:
                self._logger.info(
                    "http.request.completed",
                    extra=self._log_fields(
                        scope=scope,
                        request_id=request_id,
                        status_code=status_code,
                        started_at=started_at,
                    ),
                )
            _request_id.reset(token)

    @staticmethod
    def _log_fields(
        *,
        scope: Scope,
        request_id: str,
        status_code: int,
        started_at: float,
        exception_class: str | None = None,
    ) -> dict[str, object]:
        fields: dict[str, object] = {
            "request_id": request_id,
            "method": scope.get("method", "<unknown>"),
            "route": _route_path(scope),
            "status_code": status_code,
            "duration_ms": round(max(0.0, perf_counter() - started_at) * 1000, 3),
        }
        if exception_class is not None:
            fields["exception_class"] = exception_class
        return fields
