"""Fail-closed local tenant catalog used by the browser demonstration."""

from dataclasses import dataclass
from uuid import UUID, uuid5

from enterprise_chat_platform.domain.identifiers import TenantId, UserId
from enterprise_chat_platform.domain.prompting import (
    PromptCommunicationStyle,
    PromptExpertiseLevel,
)

DEMO_USER_NAMESPACE = UUID("f1dd116d-bf43-4e1e-a642-62676f784ae6")


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
    response_tone: str
    assistant_expertise: PromptExpertiseLevel | None
    assistant_style: PromptCommunicationStyle | None
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
        response_tone="professional",
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
        response_tone="friendly",
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
