from __future__ import annotations

from dataclasses import dataclass

import pytest

from enterprise_chat_platform.application.process_agent_run import (
    ChatProvider,
    ChatRequest,
    TerminalChatProviderError,
    TransientChatProviderError,
)
from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.gemini_provider import GeminiChatProvider

API_KEY = "test-api-key-that-must-stay-secret"


@dataclass(slots=True)
class StubResponse:
    status_code: int
    payload: object

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class RecordingClient:
    def __init__(
        self,
        *,
        response: StubResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,  # noqa: ASYNC109 - implements the injected HTTP protocol
    ) -> StubResponse:
        self.calls.append((url, headers, json, timeout))
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("Test client has no configured response")
        return self.response


class LibraryNetworkError(Exception):
    pass


def _request(content: str = "Explain transactional outbox.") -> ChatRequest:
    return ChatRequest(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        conversation_id=ConversationId.parse("22222222-2222-4222-8222-222222222222"),
        agent_run_id=AgentRunId.parse("33333333-3333-4333-8333-333333333333"),
        input_message_id=MessageId.parse("44444444-4444-4444-8444-444444444444"),
        input_content=content,
    )


def _success_payload(*parts: dict[str, object]) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": list(parts)}}]}


@pytest.mark.asyncio
async def test_calls_generate_content_and_parses_text_parts() -> None:
    client = RecordingClient(
        response=StubResponse(
            status_code=200,
            payload=_success_payload(
                {"text": " internal reasoning", "thought": True},
                {"text": "  Durable work"},
                {"text": " survives retries.  "},
            ),
        )
    )
    provider: ChatProvider = GeminiChatProvider(
        client=client,
        api_key=API_KEY,
        model="gemini-test-model",
        timeout_seconds=12.5,
    )

    completion = await provider.complete(request=_request())

    assert completion.content == "Durable work survives retries."
    assert len(client.calls) == 1
    url, headers, payload, timeout = client.calls[0]
    assert url.endswith("/v1beta/models/gemini-test-model:generateContent")
    assert API_KEY not in url
    assert headers == {
        "Content-Type": "application/json",
        "x-goog-api-key": API_KEY,
    }
    assert payload == {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Explain transactional outbox."}],
            }
        ]
    }
    assert timeout == 12.5


@pytest.mark.asyncio
async def test_trims_completion_to_domain_storage_limit() -> None:
    client = RecordingClient(
        response=StubResponse(
            status_code=200,
            payload=_success_payload({"text": f"  {'a' * (MAX_USER_MESSAGE_LENGTH + 20)}  "}),
        )
    )
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    completion = await provider.complete(request=_request())

    assert completion.content == "a" * MAX_USER_MESSAGE_LENGTH


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 429, 500, 503, 599])
async def test_retryable_http_statuses_are_transient(status_code: int) -> None:
    client = RecordingClient(response=StubResponse(status_code=status_code, payload={}))
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    with pytest.raises(TransientChatProviderError) as caught:
        await provider.complete(request=_request())

    assert API_KEY not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [300, 400, 401, 403, 404, 422])
async def test_non_retryable_http_statuses_are_terminal(status_code: int) -> None:
    client = RecordingClient(response=StubResponse(status_code=status_code, payload={}))
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    with pytest.raises(TerminalChatProviderError) as caught:
        await provider.complete(request=_request())

    assert API_KEY not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(f"timed out with {API_KEY}"),
        OSError(f"network unavailable for {API_KEY}"),
    ],
)
async def test_builtin_transport_failures_are_transient(error: Exception) -> None:
    client = RecordingClient(error=error)
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    with pytest.raises(TransientChatProviderError) as caught:
        await provider.complete(request=_request())

    assert API_KEY not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_injected_http_library_network_failure_is_transient() -> None:
    client = RecordingClient(error=LibraryNetworkError("request failed"))
    provider = GeminiChatProvider(
        client=client,
        api_key=API_KEY,
        model="gemini-test-model",
        transient_transport_errors=(TimeoutError, OSError, LibraryNetworkError),
    )

    with pytest.raises(TransientChatProviderError):
        await provider.complete(request=_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"candidates": []},
        {"candidates": [{}]},
        _success_payload({"not_text": "missing"}),
        _success_payload({"text": "   "}),
        _success_payload({"text": "invalid\x00content"}),
        ValueError(f"invalid JSON containing {API_KEY}"),
    ],
)
async def test_invalid_response_payloads_are_terminal(payload: object) -> None:
    client = RecordingClient(response=StubResponse(status_code=200, payload=payload))
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    with pytest.raises(TerminalChatProviderError) as caught:
        await provider.complete(request=_request())

    assert str(caught.value) == "Gemini returned an invalid response"
    assert API_KEY not in str(caught.value)
    assert caught.value.__context__ is None
