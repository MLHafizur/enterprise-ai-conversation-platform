"""Immutable, typed assistant-profile domain values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AssistantProfileId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.primitives import normalize_utc

MAX_PROFILE_TEXT_LENGTH = 128
MAX_CONTEXT_CHARACTER_LIMIT = 64_000
MAX_PROMPT_TEMPLATE_VERSION_LENGTH = 64

_SAFE_PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .&()'_-]{0,127}")
_PROMPT_TEMPLATE_VERSION_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class PromptResponseTone(StrEnum):
    """Allowlisted response tones that may affect trusted instructions."""

    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"


class PromptExpertiseLevel(StrEnum):
    """Allowlisted expertise descriptions owned by server configuration."""

    SENIOR_ENGINEER = "senior_engineer"
    PRODUCT_SPECIALIST = "product_specialist"


class PromptCommunicationStyle(StrEnum):
    """Allowlisted response styles owned by server configuration."""

    DIRECT_HELPFUL = "direct_helpful"
    CONCISE_SUPPORTIVE = "concise_supportive"


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantProfileRef:
    """Stable reference to one immutable profile version."""

    id: AssistantProfileId
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, AssistantProfileId):
            raise DomainValidationError("Assistant profile reference ID is invalid")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError("Assistant profile version must be an integer")
        if self.version <= 0:
            raise DomainValidationError("Assistant profile version must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantProfile:
    """One immutable, tenant-scoped profile version used to build prompts."""

    tenant_id: TenantId
    ref: AssistantProfileRef
    prompt_template_version: str
    assistant_name: str
    organization_name: str
    response_tone: PromptResponseTone
    expertise: PromptExpertiseLevel
    communication_style: PromptCommunicationStyle
    context_character_limit: int
    created_at: datetime
    created_by: UserId

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise DomainValidationError("Assistant profile tenant ID is invalid")
        if not isinstance(self.ref, AssistantProfileRef):
            raise DomainValidationError("Assistant profile reference is invalid")
        if not isinstance(self.created_by, UserId):
            raise DomainValidationError("Assistant profile creator is invalid")
        if (
            not isinstance(self.prompt_template_version, str)
            or _PROMPT_TEMPLATE_VERSION_PATTERN.fullmatch(self.prompt_template_version) is None
        ):
            raise DomainValidationError("Assistant profile prompt template version is invalid")

        _validate_safe_name(field_name="assistant name", value=self.assistant_name)
        _validate_safe_name(field_name="organization name", value=self.organization_name)

        if not isinstance(self.response_tone, PromptResponseTone):
            raise DomainValidationError("Assistant profile response tone is invalid")
        if not isinstance(self.expertise, PromptExpertiseLevel):
            raise DomainValidationError("Assistant profile expertise is invalid")
        if not isinstance(self.communication_style, PromptCommunicationStyle):
            raise DomainValidationError("Assistant profile communication style is invalid")
        if (
            isinstance(self.context_character_limit, bool)
            or not isinstance(self.context_character_limit, int)
            or not 1 <= self.context_character_limit <= MAX_CONTEXT_CHARACTER_LIMIT
        ):
            raise DomainValidationError(
                "Assistant profile context character limit must be between "
                f"1 and {MAX_CONTEXT_CHARACTER_LIMIT}"
            )

        object.__setattr__(
            self,
            "created_at",
            normalize_utc(
                self.created_at,
                field_name="assistant_profile.created_at",
            ),
        )


def _validate_safe_name(*, field_name: str, value: str) -> None:
    if not isinstance(value, str):
        raise DomainValidationError(f"Assistant profile {field_name} must be text")
    if not value.strip():
        raise DomainValidationError(f"Assistant profile {field_name} cannot be blank")
    if value != value.strip():
        raise DomainValidationError(f"Assistant profile {field_name} must be trimmed")
    if len(value) > MAX_PROFILE_TEXT_LENGTH:
        raise DomainValidationError(
            f"Assistant profile {field_name} cannot exceed {MAX_PROFILE_TEXT_LENGTH} characters"
        )
    if _SAFE_PROFILE_NAME_PATTERN.fullmatch(value) is None:
        raise DomainValidationError(
            f"Assistant profile {field_name} contains unsupported characters"
        )
