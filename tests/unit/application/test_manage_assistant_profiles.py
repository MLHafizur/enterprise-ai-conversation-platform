from dataclasses import replace
from datetime import UTC, datetime

import pytest

from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.manage_assistant_profiles import (
    MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS,
    ActivateAssistantProfileVersionCommand,
    ActiveAssistantProfile,
    AssistantProfileAdminService,
    AssistantProfileCatalog,
    AssistantProfileFields,
    CreateAssistantProfileVersionCommand,
)
from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
from enterprise_chat_platform.application.tenant_prompting import TenantPromptBuilder
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AssistantProfileId,
    TenantId,
    UserId,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 11, 16, 23, 30, tzinfo=UTC)
ACTOR = ActorContext(tenant_id=TENANT_ID, actor_id=ACTOR_ID)
FIELDS = AssistantProfileFields(
    prompt_template_version="enterprise-chat.v1",
    assistant_name="Acme Guide",
    organization_name="Acme Corp",
    response_tone=PromptResponseTone.FRIENDLY,
    expertise=PromptExpertiseLevel.PRODUCT_SPECIALIST,
    communication_style=PromptCommunicationStyle.CONCISE_SUPPORTIVE,
    context_character_limit=4096,
)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Store:
    def __init__(self) -> None:
        self.created: AssistantProfile | None = None
        self.activate_arguments: tuple[TenantId, UserId, datetime] | None = None

    async def get_catalog(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
    ) -> AssistantProfileCatalog:
        return _catalog(default_profile)

    async def create_next_version(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
        expected_latest_version: int,
        profile: AssistantProfile,
    ) -> AssistantProfile:
        del tenant_id, default_profile, expected_latest_version
        self.created = profile
        return profile

    async def activate(
        self,
        *,
        tenant_id: TenantId,
        actor_id: UserId,
        command: ActivateAssistantProfileVersionCommand,
        activated_at: datetime,
        default_profile: AssistantProfile,
    ) -> AssistantProfileCatalog:
        self.activate_arguments = (tenant_id, actor_id, activated_at)
        active_profile = replace(default_profile, ref=command.profile_ref)
        return _catalog(
            active_profile,
            revision=command.expected_revision + 1,
            activated_by=actor_id,
        )


def _catalog(
    profile: AssistantProfile,
    *,
    revision: int = 0,
    activated_by: UserId | None = None,
) -> AssistantProfileCatalog:
    return AssistantProfileCatalog(
        active=ActiveAssistantProfile(
            tenant_id=profile.tenant_id,
            profile_ref=profile.ref,
            revision=revision,
            activated_at=NOW,
            activated_by=activated_by or profile.created_by,
        ),
        latest_version=profile.ref.version,
        versions=(profile,),
    )


def _service(store: _Store) -> AssistantProfileAdminService:
    return AssistantProfileAdminService(
        store=store,
        clock=_Clock(),
        prompt_builder=TenantPromptBuilder(),
    )


def test_preview_exactly_matches_worker_prompt_builder() -> None:
    store = _Store()
    preview = _service(store).preview(actor=ACTOR, fields=FIELDS)
    expected_profile = AssistantProfile(
        tenant_id=TENANT_ID,
        ref=AssistantProfileRef(id=AssistantProfileId.new(), version=1),
        prompt_template_version=FIELDS.prompt_template_version,
        assistant_name=FIELDS.assistant_name,
        organization_name=FIELDS.organization_name,
        response_tone=FIELDS.response_tone,
        expertise=FIELDS.expertise,
        communication_style=FIELDS.communication_style,
        context_character_limit=FIELDS.context_character_limit,
        created_at=NOW,
        created_by=ACTOR_ID,
    )
    expected = TenantPromptBuilder().build(profile=expected_profile, context_messages=())

    assert preview.prompt_template_version == expected.version
    assert preview.system_instruction == expected.system_instruction
    assert "Acme Guide" in preview.system_instruction


def test_catalog_rejects_more_than_its_bounded_version_window() -> None:
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=NOW)
    versions = tuple(
        replace(profile, ref=replace(profile.ref, version=version))
        for version in range(1, MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS + 2)
    )

    with pytest.raises(DomainValidationError, match="exceeds its version limit"):
        AssistantProfileCatalog(
            active=ActiveAssistantProfile(
                tenant_id=TENANT_ID,
                profile_ref=profile.ref,
                revision=0,
                activated_at=NOW,
                activated_by=profile.created_by,
            ),
            latest_version=MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS + 1,
            versions=versions,
        )


def test_catalog_rejects_versions_from_another_profile_series() -> None:
    profile = default_assistant_profile(tenant_id=TENANT_ID, created_at=NOW)
    other_series = replace(
        profile,
        ref=AssistantProfileRef(id=AssistantProfileId.new(), version=2),
    )

    with pytest.raises(DomainValidationError, match="another profile series"):
        AssistantProfileCatalog(
            active=ActiveAssistantProfile(
                tenant_id=TENANT_ID,
                profile_ref=profile.ref,
                revision=0,
                activated_at=NOW,
                activated_by=profile.created_by,
            ),
            latest_version=2,
            versions=(other_series, profile),
        )


@pytest.mark.asyncio
async def test_create_assigns_exact_next_version_and_server_audit_identity() -> None:
    store = _Store()
    profile_id = AssistantProfileId.new()

    created = await _service(store).create_next_version(
        actor=ACTOR,
        command=CreateAssistantProfileVersionCommand(
            profile_id=profile_id,
            expected_latest_version=4,
            fields=FIELDS,
        ),
    )

    assert created == store.created
    assert created.tenant_id == TENANT_ID
    assert created.ref == AssistantProfileRef(id=profile_id, version=5)
    assert created.created_by == ACTOR_ID
    assert created.created_at == NOW


@pytest.mark.asyncio
async def test_activation_uses_authenticated_actor_and_server_time() -> None:
    store = _Store()
    default = default_assistant_profile(tenant_id=TENANT_ID, created_at=NOW)

    activated = await _service(store).activate(
        actor=ACTOR,
        command=ActivateAssistantProfileVersionCommand(
            profile_ref=default.ref,
            expected_revision=7,
        ),
    )

    assert store.activate_arguments == (TENANT_ID, ACTOR_ID, NOW)
    assert activated.active.revision == 8
    assert activated.active.activated_by == ACTOR_ID
