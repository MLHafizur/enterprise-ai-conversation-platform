"""PostgreSQL persistence for versioned tenant assistant profiles."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
)
from enterprise_chat_platform.domain.identifiers import AssistantProfileId, TenantId
from enterprise_chat_platform.infrastructure.persistence.mappers.assistant_profile import (
    assistant_profile_from_record,
    assistant_profile_to_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AssistantProfileVersionRecord,
    TenantAssistantProfileAssignmentRecord,
)


class SqlAlchemyAssistantProfileRepository:
    """Resolve and lazily bootstrap the active profile in a caller transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_active(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
    ) -> AssistantProfile:
        """Lock the active pointer, bootstrapping its deterministic default once."""
        self._require_transaction()
        if default_profile.tenant_id != tenant_id:
            raise ValueError("Default assistant profile tenant must match the requested tenant")

        assignment = await self._get_assignment_for_share(tenant_id=tenant_id)
        if assignment is None:
            await self._insert_default_profile(default_profile)
            await self._insert_default_assignment(default_profile)
            assignment = await self._get_assignment_for_share(tenant_id=tenant_id)
            if assignment is None:
                raise RuntimeError("Active assistant profile assignment was not visible")

        return await self._get_profile(
            tenant_id=tenant_id,
            ref=AssistantProfileRef(
                id=AssistantProfileId(assignment.profile_id),
                version=assignment.profile_version,
            ),
        )

    async def get_by_ref(
        self,
        *,
        tenant_id: TenantId,
        ref: AssistantProfileRef,
    ) -> AssistantProfile:
        """Load one exact immutable profile revision for a pinned agent run."""
        self._require_transaction()
        return await self._get_profile(tenant_id=tenant_id, ref=ref)

    async def _get_assignment_for_share(
        self,
        *,
        tenant_id: TenantId,
    ) -> TenantAssistantProfileAssignmentRecord | None:
        result = await self._session.execute(
            select(TenantAssistantProfileAssignmentRecord)
            .where(TenantAssistantProfileAssignmentRecord.tenant_id == tenant_id.value)
            .with_for_update(
                read=True,
                of=TenantAssistantProfileAssignmentRecord,
            )
        )
        return result.scalar_one_or_none()

    async def _insert_default_profile(self, profile: AssistantProfile) -> None:
        record = assistant_profile_to_record(profile)
        await self._session.execute(
            insert(AssistantProfileVersionRecord)
            .values(
                tenant_id=record.tenant_id,
                profile_id=record.profile_id,
                version=record.version,
                prompt_template_version=record.prompt_template_version,
                assistant_name=record.assistant_name,
                organization_name=record.organization_name,
                response_tone=record.response_tone,
                expertise=record.expertise,
                communication_style=record.communication_style,
                context_character_limit=record.context_character_limit,
                created_at=record.created_at,
                created_by=record.created_by,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    AssistantProfileVersionRecord.tenant_id,
                    AssistantProfileVersionRecord.profile_id,
                    AssistantProfileVersionRecord.version,
                ]
            )
        )

    async def _insert_default_assignment(self, profile: AssistantProfile) -> None:
        await self._session.execute(
            insert(TenantAssistantProfileAssignmentRecord)
            .values(
                tenant_id=profile.tenant_id.value,
                profile_id=profile.ref.id.value,
                profile_version=profile.ref.version,
                revision=0,
                activated_at=profile.created_at,
                activated_by=profile.created_by.value,
            )
            .on_conflict_do_nothing(
                index_elements=[TenantAssistantProfileAssignmentRecord.tenant_id]
            )
        )

    async def _get_profile(
        self,
        *,
        tenant_id: TenantId,
        ref: AssistantProfileRef,
    ) -> AssistantProfile:
        result = await self._session.execute(
            select(AssistantProfileVersionRecord).where(
                AssistantProfileVersionRecord.tenant_id == tenant_id.value,
                AssistantProfileVersionRecord.profile_id == ref.id.value,
                AssistantProfileVersionRecord.version == ref.version,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise RuntimeError("Active assistant profile version was not found")
        return assistant_profile_from_record(record)

    def _require_transaction(self) -> None:
        if not self._session.in_transaction():
            raise RuntimeError("Assistant profile persistence requires an active transaction")
