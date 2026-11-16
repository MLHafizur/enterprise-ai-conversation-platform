from datetime import UTC, datetime

from enterprise_chat_platform.application.tenant_catalog import (
    DEFAULT_ASSISTANT_PROFILE_ID,
    DEFAULT_ASSISTANT_PROFILE_VERSION,
    DEFAULT_PROFILE_CREATED_BY,
    default_assistant_profile,
)
from enterprise_chat_platform.domain.assistant_profile import (
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.identifiers import TenantId

ACME_TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
UNKNOWN_TENANT_ID = TenantId.parse("99999999-9999-4999-8999-999999999999")
CREATED_AT = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)


def test_known_tenant_bootstrap_preserves_trusted_catalog_behavior() -> None:
    profile = default_assistant_profile(
        tenant_id=ACME_TENANT_ID,
        created_at=CREATED_AT,
    )

    assert profile.tenant_id == ACME_TENANT_ID
    assert profile.ref.id == DEFAULT_ASSISTANT_PROFILE_ID
    assert profile.ref.version == DEFAULT_ASSISTANT_PROFILE_VERSION
    assert profile.assistant_name == "Acme Assistant"
    assert profile.organization_name == "Acme Corp"
    assert profile.response_tone is PromptResponseTone.PROFESSIONAL
    assert profile.expertise is PromptExpertiseLevel.SENIOR_ENGINEER
    assert profile.communication_style is PromptCommunicationStyle.DIRECT_HELPFUL
    assert profile.context_character_limit == 4_096
    assert profile.created_at == CREATED_AT
    assert profile.created_by == DEFAULT_PROFILE_CREATED_BY


def test_unknown_tenant_bootstrap_is_safe_and_deterministic() -> None:
    first = default_assistant_profile(
        tenant_id=UNKNOWN_TENANT_ID,
        created_at=CREATED_AT,
    )
    second = default_assistant_profile(
        tenant_id=UNKNOWN_TENANT_ID,
        created_at=CREATED_AT,
    )

    assert first == second
    assert first.assistant_name == "Enterprise Assistant"
    assert first.organization_name == "your organization"
    assert first.response_tone is PromptResponseTone.PROFESSIONAL
    assert str(UNKNOWN_TENANT_ID) not in first.assistant_name
    assert str(UNKNOWN_TENANT_ID) not in first.organization_name
