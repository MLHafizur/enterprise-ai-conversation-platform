"""Tenant-admin use cases for immutable assistant profile versions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from enterprise_chat_platform.application.accept_user_turn import ActorContext, Clock
from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
from enterprise_chat_platform.application.tenant_prompting import AgentPromptBuilder
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import AssistantProfileId, TenantId, UserId
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.domain.prompting import ENTERPRISE_CHAT_PROMPT_VERSION

MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantProfileFields:
    """Allowlisted values an administrator may use in a trusted prompt profile."""

    prompt_template_version: str
    assistant_name: str
    organization_name: str
    response_tone: PromptResponseTone
    expertise: PromptExpertiseLevel
    communication_style: PromptCommunicationStyle
    context_character_limit: int

    def __post_init__(self) -> None:
        if self.prompt_template_version != ENTERPRISE_CHAT_PROMPT_VERSION:
            raise DomainValidationError("Assistant profile prompt template is unsupported")


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateAssistantProfileVersionCommand:
    """Create exactly the next immutable version in one profile series."""

    profile_id: AssistantProfileId
    expected_latest_version: int
    fields: AssistantProfileFields

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, AssistantProfileId):
            raise DomainValidationError("Assistant profile identifier is invalid")
        if (
            isinstance(self.expected_latest_version, bool)
            or not isinstance(self.expected_latest_version, int)
            or self.expected_latest_version <= 0
        ):
            raise DomainValidationError(
                "Expected latest assistant profile version must be positive"
            )
        if not isinstance(self.fields, AssistantProfileFields):
            raise DomainValidationError("Assistant profile fields are invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivateAssistantProfileVersionCommand:
    """Move a tenant pointer using an optimistic assignment revision."""

    profile_ref: AssistantProfileRef
    expected_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.profile_ref, AssistantProfileRef):
            raise DomainValidationError("Assistant profile reference is invalid")
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 0
        ):
            raise DomainValidationError("Expected assistant profile revision cannot be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class ActiveAssistantProfile:
    """Audited mutable pointer to one exact immutable profile version."""

    tenant_id: TenantId
    profile_ref: AssistantProfileRef
    revision: int
    activated_at: datetime
    activated_by: UserId

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise DomainValidationError("Active assistant profile tenant is invalid")
        if not isinstance(self.profile_ref, AssistantProfileRef):
            raise DomainValidationError("Active assistant profile reference is invalid")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise DomainValidationError("Active assistant profile revision cannot be negative")
        if not isinstance(self.activated_by, UserId):
            raise DomainValidationError("Active assistant profile actor is invalid")
        object.__setattr__(
            self,
            "activated_at",
            normalize_utc(
                self.activated_at,
                field_name="active_assistant_profile.activated_at",
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantProfileCatalog:
    """One tenant's bounded profile history and active assignment."""

    active: ActiveAssistantProfile
    latest_version: int
    versions: tuple[AssistantProfile, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.latest_version, bool)
            or not isinstance(self.latest_version, int)
            or self.latest_version <= 0
        ):
            raise DomainValidationError("Latest assistant profile version must be positive")
        if not self.versions:
            raise DomainValidationError("Assistant profile catalog cannot be empty")
        if len(self.versions) > MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS:
            raise DomainValidationError("Assistant profile catalog exceeds its version limit")
        if any(profile.tenant_id != self.active.tenant_id for profile in self.versions):
            raise DomainValidationError("Assistant profile catalog contains another tenant")
        if any(profile.ref.id != self.active.profile_ref.id for profile in self.versions):
            raise DomainValidationError("Assistant profile catalog contains another profile series")
        version_numbers = tuple(profile.ref.version for profile in self.versions)
        if len(set(version_numbers)) != len(version_numbers):
            raise DomainValidationError("Assistant profile catalog contains duplicate versions")
        active_profile = next(
            (profile for profile in self.versions if profile.ref == self.active.profile_ref),
            None,
        )
        if active_profile is None:
            raise DomainValidationError("Active assistant profile is missing from the catalog")
        if self.latest_version != max(version_numbers):
            raise DomainValidationError("Latest assistant profile version is inconsistent")


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantProfilePreview:
    """Server-rendered trusted instruction for typed candidate fields."""

    prompt_template_version: str
    system_instruction: str


class AssistantProfileAdminStore(Protocol):
    """Persistence boundary for tenant-scoped profile administration."""

    async def get_catalog(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
    ) -> AssistantProfileCatalog: ...

    async def create_next_version(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
        expected_latest_version: int,
        profile: AssistantProfile,
    ) -> AssistantProfile: ...

    async def activate(
        self,
        *,
        tenant_id: TenantId,
        actor_id: UserId,
        command: ActivateAssistantProfileVersionCommand,
        activated_at: datetime,
        default_profile: AssistantProfile,
    ) -> AssistantProfileCatalog: ...


class AssistantProfileAdminService:
    """Coordinate safe profile preview, version creation, and activation."""

    def __init__(
        self,
        *,
        store: AssistantProfileAdminStore,
        clock: Clock,
        prompt_builder: AgentPromptBuilder,
    ) -> None:
        self._store = store
        self._clock = clock
        self._prompt_builder = prompt_builder

    async def get_catalog(self, *, actor: ActorContext) -> AssistantProfileCatalog:
        now = self._clock.now()
        return await self._store.get_catalog(
            tenant_id=actor.tenant_id,
            default_profile=default_assistant_profile(
                tenant_id=actor.tenant_id,
                created_at=now,
            ),
        )

    def preview(
        self,
        *,
        actor: ActorContext,
        fields: AssistantProfileFields,
    ) -> AssistantProfilePreview:
        profile = self._build_profile(
            actor=actor,
            profile_ref=AssistantProfileRef(
                id=AssistantProfileId.new(),
                version=1,
            ),
            fields=fields,
            created_at=self._clock.now(),
        )
        plan = self._prompt_builder.build(profile=profile, context_messages=())
        return AssistantProfilePreview(
            prompt_template_version=plan.version,
            system_instruction=plan.system_instruction,
        )

    async def create_next_version(
        self,
        *,
        actor: ActorContext,
        command: CreateAssistantProfileVersionCommand,
    ) -> AssistantProfile:
        created_at = self._clock.now()
        default_profile = default_assistant_profile(
            tenant_id=actor.tenant_id,
            created_at=created_at,
        )
        candidate = self._build_profile(
            actor=actor,
            profile_ref=AssistantProfileRef(
                id=command.profile_id,
                version=command.expected_latest_version + 1,
            ),
            fields=command.fields,
            created_at=created_at,
        )
        # Rendering before persistence makes unsupported templates fail closed.
        self._prompt_builder.build(profile=candidate, context_messages=())
        return await self._store.create_next_version(
            tenant_id=actor.tenant_id,
            default_profile=default_profile,
            expected_latest_version=command.expected_latest_version,
            profile=candidate,
        )

    async def activate(
        self,
        *,
        actor: ActorContext,
        command: ActivateAssistantProfileVersionCommand,
    ) -> AssistantProfileCatalog:
        activated_at = self._clock.now()
        return await self._store.activate(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            command=command,
            activated_at=activated_at,
            default_profile=default_assistant_profile(
                tenant_id=actor.tenant_id,
                created_at=activated_at,
            ),
        )

    @staticmethod
    def _build_profile(
        *,
        actor: ActorContext,
        profile_ref: AssistantProfileRef,
        fields: AssistantProfileFields,
        created_at: datetime,
    ) -> AssistantProfile:
        return AssistantProfile(
            tenant_id=actor.tenant_id,
            ref=profile_ref,
            prompt_template_version=fields.prompt_template_version,
            assistant_name=fields.assistant_name,
            organization_name=fields.organization_name,
            response_tone=fields.response_tone,
            expertise=fields.expertise,
            communication_style=fields.communication_style,
            context_character_limit=fields.context_character_limit,
            created_at=created_at,
            created_by=actor.actor_id,
        )
