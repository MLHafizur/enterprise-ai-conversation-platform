"""Gemini ``generateContent`` adapter with an injectable HTTP boundary."""

from __future__ import annotations

import re
from typing import Protocol, cast

from enterprise_chat_platform.application.process_agent_run import (
    ChatCompletion,
    ChatRequest,
    TerminalChatProviderError,
    TransientChatProviderError,
)
from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH
from enterprise_chat_platform.domain.prompting import PromptRole

DEFAULT_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429})


class AsyncHttpResponse(Protocol):
    """Small response surface required from an asynchronous HTTP client."""

    status_code: int

    def json(self) -> object: ...


class AsyncHttpClient(Protocol):
    """HTTP port implemented by clients such as ``httpx.AsyncClient``."""

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,  # noqa: ASYNC109 - mirrors common async HTTP client APIs
    ) -> AsyncHttpResponse: ...


class _InvalidGeminiResponse(ValueError):
    """Internal marker whose details must never escape the adapter."""


class GeminiChatProvider:
    """Generate one text completion through Gemini's REST API.

    The provider does not own or close the injected client. Callers using an
    HTTP library with custom timeout/network exception classes should include
    those classes in ``transient_transport_errors``.
    """

    def __init__(
        self,
        *,
        client: AsyncHttpClient,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        api_base_url: str = DEFAULT_GEMINI_API_BASE_URL,
        transient_transport_errors: tuple[type[Exception], ...] = (
            TimeoutError,
            OSError,
        ),
    ) -> None:
        normalized_api_key = api_key.strip()
        if not normalized_api_key:
            raise ValueError("Gemini API key is required")
        if _MODEL_ID_PATTERN.fullmatch(model) is None:
            raise ValueError("Gemini model must be a bare model identifier")
        if timeout_seconds <= 0:
            raise ValueError("Gemini timeout must be positive")

        normalized_base_url = api_base_url.rstrip("/")
        if not normalized_base_url.startswith("https://"):
            raise ValueError("Gemini API base URL must use HTTPS")

        self._client = client
        self._api_key = normalized_api_key
        self._endpoint = f"{normalized_base_url}/models/{model}:generateContent"
        self._timeout_seconds = timeout_seconds
        self._transient_transport_errors = transient_transport_errors

    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        """Return a bounded plain-text completion or a classified safe error."""
        payload = _build_request_payload(request)
        response: AsyncHttpResponse | None = None
        try:
            response = await self._client.post(
                self._endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                json=payload,
                timeout=self._timeout_seconds,
            )
        except self._transient_transport_errors:
            pass

        # Raise outside the transport exception handler so an HTTP-library
        # exception retaining the request headers cannot become error context.
        if response is None:
            raise TransientChatProviderError("Gemini is temporarily unavailable")

        status_code = response.status_code
        if status_code in _RETRYABLE_HTTP_STATUSES or 500 <= status_code <= 599:
            raise TransientChatProviderError("Gemini is temporarily unavailable")
        if not 200 <= status_code <= 299:
            raise TerminalChatProviderError("Gemini rejected the request")

        content = _read_response_text(response)
        if content is None:
            raise TerminalChatProviderError("Gemini returned an invalid response")

        return ChatCompletion(content=content)


def _build_request_payload(request: ChatRequest) -> dict[str, object]:
    """Map a provider-neutral prompt plan to Gemini without rewriting text."""
    if request.prompt is None:
        # Keep the established payload stable for callers that have not opted
        # into structured prompt construction.
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": request.input_content}],
                }
            ]
        }

    contents: list[dict[str, object]] = []
    for turn in request.prompt.context_messages:
        _append_gemini_content(
            contents,
            role=_gemini_role(turn.role),
            text=turn.content,
        )
    _append_gemini_content(
        contents,
        role="user",
        text=request.input_content,
    )
    return {
        "systemInstruction": {
            "parts": [{"text": request.prompt.system_instruction}],
        },
        "contents": contents,
    }


def _gemini_role(role: PromptRole) -> str:
    if role is PromptRole.USER:
        return "user"
    if role is PromptRole.ASSISTANT:
        return "model"
    raise RuntimeError("Prompt role cannot be mapped to Gemini")


def _append_gemini_content(
    contents: list[dict[str, object]],
    *,
    role: str,
    text: str,
) -> None:
    """Keep Gemini roles alternating without concatenating message text."""
    if contents and contents[-1]["role"] == role:
        parts = cast(list[dict[str, str]], contents[-1]["parts"])
        parts.append({"text": text})
        return
    contents.append(
        {
            "role": role,
            "parts": [{"text": text}],
        }
    )


def _read_response_text(response: AsyncHttpResponse) -> str | None:
    """Discard parsing exceptions before returning to the public error boundary."""
    try:
        return _extract_text(response.json())
    except (TypeError, ValueError):
        return None


def _extract_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise _InvalidGeminiResponse
    response_object = cast(dict[str, object], payload)

    candidates = response_object.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise _InvalidGeminiResponse

    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise _InvalidGeminiResponse
    candidate_object = cast(dict[str, object], candidate)

    content = candidate_object.get("content")
    if not isinstance(content, dict):
        raise _InvalidGeminiResponse
    content_object = cast(dict[str, object], content)

    parts = content_object.get("parts")
    if not isinstance(parts, list) or not parts:
        raise _InvalidGeminiResponse

    text_parts: list[str] = []
    for raw_part in parts:
        if not isinstance(raw_part, dict):
            continue
        part = cast(dict[str, object], raw_part)
        if part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text)

    normalized = "".join(text_parts).strip()
    if not normalized or "\x00" in normalized:
        raise _InvalidGeminiResponse

    return normalized[:MAX_USER_MESSAGE_LENGTH]
