"""Provider-neutral, deterministic prompt value objects.

Prompt context is intentionally represented as structured messages. User content
must never be interpolated into the trusted system instruction.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from enterprise_chat_platform.domain.assistant_profile import (
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH
from enterprise_chat_platform.domain.errors import DomainValidationError

ENTERPRISE_CHAT_PROMPT_VERSION = "enterprise-chat.v1"
MAX_SYSTEM_INSTRUCTION_LENGTH = 4_096
_PROMPT_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

__all__ = [
    "ENTERPRISE_CHAT_PROMPT_VERSION",
    "PromptCommunicationStyle",
    "PromptExpertiseLevel",
    "PromptPlan",
    "PromptResponseTone",
    "PromptRole",
    "PromptTurn",
]


class PromptRole(StrEnum):
    """Provider-neutral roles allowed in durable conversation context."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptTurn:
    """One complete, immutable message supplied as conversation context."""

    role: PromptRole
    content: str = field(repr=False)
    sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.role, PromptRole):
            raise DomainValidationError("Prompt turn role is invalid")
        if self.sequence <= 0:
            raise DomainValidationError("Prompt turn sequence must be positive")
        if not self.content.strip():
            raise DomainValidationError("Prompt turn content cannot be blank")
        if "\x00" in self.content:
            raise DomainValidationError("Prompt turn content cannot contain NUL characters")
        if len(self.content) > MAX_USER_MESSAGE_LENGTH:
            raise DomainValidationError(
                f"Prompt turn content cannot exceed {MAX_USER_MESSAGE_LENGTH} characters"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptPlan:
    """A versioned prompt plan ready for a provider adapter.

    ``context_character_count`` measures Python string length: Unicode code
    points, not bytes or model-specific tokens. This keeps the budget stable
    without coupling the application layer to one provider tokenizer.
    """

    system_instruction: str = field(repr=False)
    context_messages: tuple[PromptTurn, ...] = field(default=(), repr=False)
    selected_message_count: int = 0
    omitted_message_count: int = 0
    context_character_count: int = 0
    version: str = ENTERPRISE_CHAT_PROMPT_VERSION

    def __post_init__(self) -> None:
        if _PROMPT_VERSION_PATTERN.fullmatch(self.version) is None:
            raise DomainValidationError("Prompt version is invalid")
        if not self.system_instruction.strip():
            raise DomainValidationError("System instruction cannot be blank")
        if self.system_instruction != self.system_instruction.strip():
            raise DomainValidationError("System instruction must be trimmed")
        if "\x00" in self.system_instruction:
            raise DomainValidationError("System instruction cannot contain NUL characters")
        if len(self.system_instruction) > MAX_SYSTEM_INSTRUCTION_LENGTH:
            raise DomainValidationError(
                f"System instruction cannot exceed {MAX_SYSTEM_INSTRUCTION_LENGTH} characters"
            )
        if not isinstance(self.context_messages, tuple):
            raise DomainValidationError("Prompt context messages must be a tuple")

        previous_sequence = 0
        for message in self.context_messages:
            if not isinstance(message, PromptTurn):
                raise DomainValidationError("Prompt context contains an invalid message")
            if message.sequence <= previous_sequence:
                raise DomainValidationError(
                    "Prompt context messages must have strictly increasing sequences"
                )
            previous_sequence = message.sequence

        if self.context_messages and self.context_messages[0].role is not PromptRole.USER:
            raise DomainValidationError("Prompt context must begin with a user message")

        if self.selected_message_count != len(self.context_messages):
            raise DomainValidationError("Selected message count must match the prompt context")
        if self.omitted_message_count < 0:
            raise DomainValidationError("Omitted message count cannot be negative")

        measured_characters = sum(len(message.content) for message in self.context_messages)
        if self.context_character_count != measured_characters:
            raise DomainValidationError(
                "Context character count must match selected message content"
            )

    @property
    def fingerprint(self) -> str:
        """Return a deterministic digest without exposing prompt content in logs."""
        canonical = json.dumps(
            {
                "context_messages": [
                    {
                        "content": message.content,
                        "role": message.role.value,
                        "sequence": message.sequence,
                    }
                    for message in self.context_messages
                ],
                "omitted_message_count": self.omitted_message_count,
                "system_instruction": self.system_instruction,
                "version": self.version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
