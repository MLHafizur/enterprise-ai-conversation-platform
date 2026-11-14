import pytest

from enterprise_chat_platform.application.process_agent_run import ChatRequest
from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.chat_provider import EchoChatProvider


def _request(content: str) -> ChatRequest:
    return ChatRequest(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        conversation_id=ConversationId.parse("22222222-2222-4222-8222-222222222222"),
        agent_run_id=AgentRunId.parse("33333333-3333-4333-8333-333333333333"),
        input_message_id=MessageId.parse("44444444-4444-4444-8444-444444444444"),
        input_content=content,
    )


@pytest.mark.asyncio
async def test_echo_provider_is_deterministic_and_has_no_external_dependencies() -> None:
    provider = EchoChatProvider()
    request = _request("Explain leases.")

    first = await provider.complete(request=request)
    second = await provider.complete(request=request)

    assert first == second
    assert first.content == "Echo: Explain leases."


@pytest.mark.asyncio
async def test_echo_provider_never_exceeds_message_storage_limit() -> None:
    completion = await EchoChatProvider().complete(request=_request("a" * MAX_USER_MESSAGE_LENGTH))

    assert len(completion.content) == MAX_USER_MESSAGE_LENGTH
