from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import inspect

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
from enterprise_chat_platform.infrastructure.persistence.mappers.assistant_profile import (
    assistant_profile_from_record,
    assistant_profile_to_record,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
PROFILE_ID = AssistantProfileId.parse("22222222-2222-4222-8222-222222222222")
CREATED_BY = UserId.parse("33333333-3333-4333-8333-333333333333")
CREATED_AT = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)


def _profile() -> AssistantProfile:
    return AssistantProfile(
        tenant_id=TENANT_ID,
        ref=AssistantProfileRef(id=PROFILE_ID, version=4),
        prompt_template_version="enterprise-chat.v1",
        assistant_name="Acme Assistant",
        organization_name="Acme Corp",
        response_tone=PromptResponseTone.PROFESSIONAL,
        expertise=PromptExpertiseLevel.SENIOR_ENGINEER,
        communication_style=PromptCommunicationStyle.DIRECT_HELPFUL,
        context_character_limit=4096,
        created_at=CREATED_AT,
        created_by=CREATED_BY,
    )


def test_assistant_profile_round_trips_every_typed_field() -> None:
    profile = _profile()
    record = assistant_profile_to_record(profile)

    assert record.tenant_id == TENANT_ID.value
    assert record.profile_id == PROFILE_ID.value
    assert record.version == 4
    assert record.prompt_template_version == "enterprise-chat.v1"
    assert record.assistant_name == "Acme Assistant"
    assert record.organization_name == "Acme Corp"
    assert record.response_tone == "professional"
    assert record.expertise == "senior_engineer"
    assert record.communication_style == "direct_helpful"
    assert record.context_character_limit == 4096
    assert record.created_at == CREATED_AT
    assert record.created_by == CREATED_BY.value
    assert assistant_profile_from_record(record) == profile


def test_forward_mapping_returns_unsaved_transient_record() -> None:
    record = assistant_profile_to_record(_profile())

    state = inspect(record)
    assert state.transient is True
    assert state.session is None


def test_reverse_mapping_uses_strong_identifier_types() -> None:
    mapped = assistant_profile_from_record(assistant_profile_to_record(_profile()))

    assert isinstance(mapped.tenant_id, TenantId)
    assert isinstance(mapped.ref.id, AssistantProfileId)
    assert isinstance(mapped.created_by, UserId)
    assert mapped.ref.id.value == UUID("22222222-2222-4222-8222-222222222222")
