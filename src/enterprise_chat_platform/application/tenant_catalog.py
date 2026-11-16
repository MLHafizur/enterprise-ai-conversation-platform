"""Fail-closed local tenant catalog used by the browser demonstration."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.identifiers import (
    AssistantProfileId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.prompting import ENTERPRISE_CHAT_PROMPT_VERSION

DEMO_USER_NAMESPACE = UUID("f1dd116d-bf43-4e1e-a642-62676f784ae6")
DEFAULT_ASSISTANT_PROFILE_ID = AssistantProfileId.parse("00000000-0000-4000-8000-000000000101")
DEFAULT_ASSISTANT_PROFILE_VERSION = 1
DEFAULT_PROFILE_CREATED_BY = UserId.parse("00000000-0000-4000-8000-000000000102")
DEFAULT_CONTEXT_CHARACTER_LIMIT = 4_096


@dataclass(frozen=True, slots=True, kw_only=True)
class TenantDefinition:
    """Safe, non-secret tenant metadata and local quota policy."""

    alias: str
    id: TenantId
    name: str
    domain: str
    max_connections: int
    daily_message_limit: int
    context_size_limit: int
    brand_name: str
    response_tone: PromptResponseTone
    assistant_expertise: PromptExpertiseLevel
    assistant_style: PromptCommunicationStyle
    primary_color: str


DEMO_TENANTS = (
    TenantDefinition(
        alias="tenant_1",
        id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        name="Acme Corp",
        domain="acme.example",
        max_connections=50,
        daily_message_limit=5_000,
        context_size_limit=4_096,
        brand_name="Acme Assistant",
        response_tone=PromptResponseTone.PROFESSIONAL,
        assistant_expertise=PromptExpertiseLevel.SENIOR_ENGINEER,
        assistant_style=PromptCommunicationStyle.DIRECT_HELPFUL,
        primary_color="#4f46e5",
    ),
    TenantDefinition(
        alias="tenant_2",
        id=TenantId.parse("22222222-2222-4222-8222-222222222222"),
        name="TechStart Inc",
        domain="techstart.example",
        max_connections=25,
        daily_message_limit=2_500,
        context_size_limit=2_048,
        brand_name="TechStart Support",
        response_tone=PromptResponseTone.FRIENDLY,
        assistant_expertise=PromptExpertiseLevel.PRODUCT_SPECIALIST,
        assistant_style=PromptCommunicationStyle.CONCISE_SUPPORTIVE,
        primary_color="#059669",
    ),
)

_TENANTS_BY_ALIAS = {tenant.alias: tenant for tenant in DEMO_TENANTS}
_TENANTS_BY_ID = {tenant.id.value: tenant for tenant in DEMO_TENANTS}


def find_tenant(value: str | UUID | TenantId) -> TenantDefinition | None:
    """Resolve only an allowlisted alias or UUID; unknown values stay unknown."""
    if isinstance(value, TenantId):
        return _TENANTS_BY_ID.get(value.value)
    if isinstance(value, UUID):
        return _TENANTS_BY_ID.get(value)

    by_alias = _TENANTS_BY_ALIAS.get(value)
    if by_alias is not None:
        return by_alias

    try:
        return _TENANTS_BY_ID.get(UUID(value))
    except ValueError:
        return None


def demo_user_id(*, tenant: TenantDefinition, role: str) -> UserId:
    """Return a stable local identity so a role can reopen its conversations."""
    return UserId(uuid5(DEMO_USER_NAMESPACE, f"{tenant.id}:{role}"))


def default_assistant_profile(
    *,
    tenant_id: TenantId,
    created_at: datetime,
) -> AssistantProfile:
    """Build the deterministic immutable v1 profile used during lazy bootstrap."""
    tenant = find_tenant(tenant_id)
    if tenant is None:
        assistant_name = "Enterprise Assistant"
        organization_name = "your organization"
        response_tone = PromptResponseTone.PROFESSIONAL
        expertise = PromptExpertiseLevel.SENIOR_ENGINEER
        communication_style = PromptCommunicationStyle.DIRECT_HELPFUL
        context_character_limit = DEFAULT_CONTEXT_CHARACTER_LIMIT
    else:
        assistant_name = tenant.brand_name
        organization_name = tenant.name
        response_tone = tenant.response_tone
        expertise = tenant.assistant_expertise
        communication_style = tenant.assistant_style
        context_character_limit = tenant.context_size_limit

    return AssistantProfile(
        tenant_id=tenant_id,
        ref=AssistantProfileRef(
            id=DEFAULT_ASSISTANT_PROFILE_ID,
            version=DEFAULT_ASSISTANT_PROFILE_VERSION,
        ),
        prompt_template_version=ENTERPRISE_CHAT_PROMPT_VERSION,
        assistant_name=assistant_name,
        organization_name=organization_name,
        response_tone=response_tone,
        expertise=expertise,
        communication_style=communication_style,
        context_character_limit=context_character_limit,
        created_at=created_at,
        created_by=DEFAULT_PROFILE_CREATED_BY,
    )
