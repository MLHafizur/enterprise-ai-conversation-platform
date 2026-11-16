"""Map immutable assistant profiles between domain and persistence forms."""

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
from enterprise_chat_platform.infrastructure.persistence.models import (
    AssistantProfileVersionRecord,
)


def assistant_profile_to_record(
    profile: AssistantProfile,
) -> AssistantProfileVersionRecord:
    """Create a new transient immutable profile-version record."""
    return AssistantProfileVersionRecord(
        tenant_id=profile.tenant_id.value,
        profile_id=profile.ref.id.value,
        version=profile.ref.version,
        prompt_template_version=profile.prompt_template_version,
        assistant_name=profile.assistant_name,
        organization_name=profile.organization_name,
        response_tone=profile.response_tone.value,
        expertise=profile.expertise.value,
        communication_style=profile.communication_style.value,
        context_character_limit=profile.context_character_limit,
        created_at=profile.created_at,
        created_by=profile.created_by.value,
    )


def assistant_profile_from_record(
    record: AssistantProfileVersionRecord,
) -> AssistantProfile:
    """Rebuild a validated profile from one immutable database row."""
    return AssistantProfile(
        tenant_id=TenantId(record.tenant_id),
        ref=AssistantProfileRef(
            id=AssistantProfileId(record.profile_id),
            version=record.version,
        ),
        prompt_template_version=record.prompt_template_version,
        assistant_name=record.assistant_name,
        organization_name=record.organization_name,
        response_tone=PromptResponseTone(record.response_tone),
        expertise=PromptExpertiseLevel(record.expertise),
        communication_style=PromptCommunicationStyle(record.communication_style),
        context_character_limit=record.context_character_limit,
        created_at=record.created_at,
        created_by=UserId(record.created_by),
    )
