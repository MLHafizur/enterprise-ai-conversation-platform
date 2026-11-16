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
from enterprise_chat_platform.domain.prompting import PromptPlan, PromptRole, PromptTurn
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


def _request(
    content: str = "Explain transactional outbox.",
    *,
    prompt: PromptPlan | None = None,
) -> ChatRequest:
    return ChatRequest(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        conversation_id=ConversationId.parse("22222222-2222-4222-8222-222222222222"),
        agent_run_id=AgentRunId.parse("33333333-3333-4333-8333-333333333333"),
        input_message_id=MessageId.parse("44444444-4444-4444-8444-444444444444"),
        input_content=content,
        prompt=prompt,
    )


def _prompt_plan(
    *,
    system_instruction: str = "You are the trusted enterprise assistant.",
    context_messages: tuple[PromptTurn, ...] = (),
) -> PromptPlan:
    return PromptPlan(
        version="enterprise-chat-prompt.v1",
        system_instruction=system_instruction,
        context_messages=context_messages,
        selected_message_count=len(context_messages),
        omitted_message_count=0,
        context_character_count=sum(len(turn.content) for turn in context_messages),
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
async def test_maps_structured_prompt_to_ordered_gemini_contents() -> None:
    current_input = "Current input {must remain exact}."
    system_instruction = "Trusted instruction; never contains the current input."
    prompt = _prompt_plan(
        system_instruction=system_instruction,
        context_messages=(
            PromptTurn(
                role=PromptRole.USER,
                content="Earlier user text.",
                sequence=1,
            ),
            PromptTurn(
                role=PromptRole.ASSISTANT,
                content="Earlier assistant text.",
                sequence=2,
            ),
        ),
    )
    client = RecordingClient(
        response=StubResponse(
            status_code=200,
            payload=_success_payload({"text": "Structured response"}),
        )
    )
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    await provider.complete(request=_request(current_input, prompt=prompt))

    payload = client.calls[0][2]
    assert payload == {
        "systemInstruction": {
            "parts": [{"text": system_instruction}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Earlier user text."}],
            },
            {
                "role": "model",
                "parts": [{"text": "Earlier assistant text."}],
            },
            {
                "role": "user",
                "parts": [{"text": current_input}],
            },
        ],
    }
    assert current_input not in str(payload["systemInstruction"])
    assert str(payload).count(current_input) == 1


@pytest.mark.asyncio
async def test_coalesces_consecutive_user_context_and_current_input_without_rewriting() -> None:
    current_input = "Second user turn {exact}."
    prompt = _prompt_plan(
        context_messages=(
            PromptTurn(
                role=PromptRole.USER,
                content="First user turn.",
                sequence=1,
            ),
        )
    )
    client = RecordingClient(
        response=StubResponse(
            status_code=200,
            payload=_success_payload({"text": "Response"}),
        )
    )
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    await provider.complete(request=_request(current_input, prompt=prompt))

    payload = client.calls[0][2]
    assert payload["contents"] == [
        {
            "role": "user",
            "parts": [
                {"text": "First user turn."},
                {"text": current_input},
            ],
        }
    ]
    assert str(payload).count(current_input) == 1


@pytest.mark.asyncio
async def test_coalesces_consecutive_assistant_context_without_rewriting() -> None:
    prompt = _prompt_plan(
        context_messages=(
            PromptTurn(
                role=PromptRole.USER,
                content="Earlier question.",
                sequence=1,
            ),
            PromptTurn(
                role=PromptRole.ASSISTANT,
                content="First assistant part.",
                sequence=2,
            ),
            PromptTurn(
                role=PromptRole.ASSISTANT,
                content="Second assistant part.",
                sequence=3,
            ),
        )
    )
    client = RecordingClient(
        response=StubResponse(
            status_code=200,
            payload=_success_payload({"text": "Response"}),
        )
    )
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    await provider.complete(request=_request("Current question.", prompt=prompt))

    assert client.calls[0][2]["contents"] == [
        {
            "role": "user",
            "parts": [{"text": "Earlier question."}],
        },
        {
            "role": "model",
            "parts": [
                {"text": "First assistant part."},
                {"text": "Second assistant part."},
            ],
        },
        {
            "role": "user",
            "parts": [{"text": "Current question."}],
        },
    ]


@pytest.mark.asyncio
async def test_structured_prompt_with_empty_context_sends_current_input_once() -> None:
    client = RecordingClient(
        response=StubResponse(
            status_code=200,
            payload=_success_payload({"text": "Response"}),
        )
    )
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    await provider.complete(
        request=_request(
            "Only current input.",
            prompt=_prompt_plan(system_instruction="Fixed trusted instruction."),
        )
    )

    assert client.calls[0][2] == {
        "systemInstruction": {
            "parts": [{"text": "Fixed trusted instruction."}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Only current input."}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_structured_prompt_details_do_not_escape_provider_errors() -> None:
    secret_instruction = "confidential-system-instruction"
    secret_input = "confidential-user-input"
    client = RecordingClient(response=StubResponse(status_code=503, payload={}))
    provider = GeminiChatProvider(client=client, api_key=API_KEY, model="gemini-test-model")

    with pytest.raises(TransientChatProviderError) as caught:
        await provider.complete(
            request=_request(
                secret_input,
                prompt=_prompt_plan(system_instruction=secret_instruction),
            )
        )

    assert str(caught.value) == "Gemini is temporarily unavailable"
    assert secret_instruction not in str(caught.value)
    assert secret_input not in str(caught.value)
    assert caught.value.__context__ is None


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
