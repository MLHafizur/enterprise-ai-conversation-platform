"""Local deterministic chat-provider adapters."""

from enterprise_chat_platform.application.process_agent_run import (
    ChatCompletion,
    ChatRequest,
)
from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH


class EchoChatProvider:
    """Deterministic local/test provider that performs no external I/O."""

    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        content = f"Echo: {request.input_content}"
        return ChatCompletion(content=content[:MAX_USER_MESSAGE_LENGTH])
