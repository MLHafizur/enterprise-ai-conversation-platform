"""PostgreSQL persistence for versioned tenant assistant profiles."""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.errors import (
    AssistantProfileConflictError,
    AssistantProfileNotFoundError,
)
from enterprise_chat_platform.application.manage_assistant_profiles import (
    MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS,
    ActivateAssistantProfileVersionCommand,
    ActiveAssistantProfile,
    AssistantProfileCatalog,
)
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
)
from enterprise_chat_platform.domain.identifiers import (
    AssistantProfileId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
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
        exclusive: bool = False,
    ) -> AssistantProfile:
        """Lock the active pointer, bootstrapping its deterministic default once."""
        self._require_transaction()
        if default_profile.tenant_id != tenant_id:
            raise ValueError("Default assistant profile tenant must match the requested tenant")

        assignment = await self._get_assignment_for_lock(
            tenant_id=tenant_id,
            exclusive=exclusive,
        )
        if assignment is None:
            await self._insert_default_profile(default_profile)
            await self._insert_default_assignment(default_profile)
            assignment = await self._get_assignment_for_lock(
                tenant_id=tenant_id,
                exclusive=exclusive,
            )
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

    async def _get_assignment_for_lock(
        self,
        *,
        tenant_id: TenantId,
        exclusive: bool,
    ) -> TenantAssistantProfileAssignmentRecord | None:
        result = await self._session.execute(
            select(TenantAssistantProfileAssignmentRecord)
            .where(TenantAssistantProfileAssignmentRecord.tenant_id == tenant_id.value)
            .with_for_update(
                read=not exclusive,
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


class SqlAlchemyAssistantProfileAdminStore:
    """Run short tenant-admin profile transactions through the runtime role."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def get_catalog(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
    ) -> AssistantProfileCatalog:
        """Return a tenant catalog, lazily creating its deterministic v1 once."""
        async with self._session_factory() as session, session.begin():
            repository = SqlAlchemyAssistantProfileRepository(session)
            await repository.get_or_create_active(
                tenant_id=tenant_id,
                default_profile=default_profile,
            )
            return await self._load_catalog(session=session, tenant_id=tenant_id)

    async def create_next_version(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
        expected_latest_version: int,
        profile: AssistantProfile,
    ) -> AssistantProfile:
        """Append one exact next version without moving the active pointer."""
        if profile.tenant_id != tenant_id:
            raise ValueError("Assistant profile tenant must match the requested tenant")
        if profile.ref.version != expected_latest_version + 1:
            raise ValueError("Assistant profile candidate must be the exact next version")

        async with self._session_factory() as session, session.begin():
            repository = SqlAlchemyAssistantProfileRepository(session)
            await repository.get_or_create_active(
                tenant_id=tenant_id,
                default_profile=default_profile,
                exclusive=True,
            )
            latest_version = await session.scalar(
                select(func.max(AssistantProfileVersionRecord.version)).where(
                    AssistantProfileVersionRecord.tenant_id == tenant_id.value,
                    AssistantProfileVersionRecord.profile_id == profile.ref.id.value,
                )
            )
            if latest_version is None:
                raise AssistantProfileNotFoundError("Assistant profile was not found")
            if latest_version != expected_latest_version:
                raise AssistantProfileConflictError(
                    "Assistant profile changed before the version was created"
                )

            session.add(assistant_profile_to_record(profile))
            try:
                await session.flush()
            except IntegrityError:
                raise AssistantProfileConflictError(
                    "Assistant profile changed before the version was created"
                ) from None
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
        """Atomically activate an exact tenant version using assignment CAS."""
        async with self._session_factory() as session, session.begin():
            repository = SqlAlchemyAssistantProfileRepository(session)
            await repository.get_or_create_active(
                tenant_id=tenant_id,
                default_profile=default_profile,
                exclusive=True,
            )
            target = await session.scalar(
                select(AssistantProfileVersionRecord).where(
                    AssistantProfileVersionRecord.tenant_id == tenant_id.value,
                    AssistantProfileVersionRecord.profile_id == command.profile_ref.id.value,
                    AssistantProfileVersionRecord.version == command.profile_ref.version,
                )
            )
            if target is None:
                raise AssistantProfileNotFoundError("Assistant profile was not found")

            result = await session.execute(
                update(TenantAssistantProfileAssignmentRecord)
                .where(
                    TenantAssistantProfileAssignmentRecord.tenant_id == tenant_id.value,
                    TenantAssistantProfileAssignmentRecord.revision == command.expected_revision,
                )
                .values(
                    profile_id=command.profile_ref.id.value,
                    profile_version=command.profile_ref.version,
                    revision=TenantAssistantProfileAssignmentRecord.revision + 1,
                    activated_at=activated_at,
                    activated_by=actor_id.value,
                )
                .returning(TenantAssistantProfileAssignmentRecord)
            )
            if result.scalar_one_or_none() is None:
                raise AssistantProfileConflictError(
                    "Assistant profile assignment changed before activation"
                )
            return await self._load_catalog(session=session, tenant_id=tenant_id)

    @staticmethod
    async def _load_catalog(
        *,
        session: AsyncSession,
        tenant_id: TenantId,
    ) -> AssistantProfileCatalog:
        assignment = await session.scalar(
            select(TenantAssistantProfileAssignmentRecord).where(
                TenantAssistantProfileAssignmentRecord.tenant_id == tenant_id.value,
            )
        )
        if assignment is None:
            raise RuntimeError("Active assistant profile assignment was not visible")

        newest_records = tuple(
            (
                await session.scalars(
                    select(AssistantProfileVersionRecord)
                    .where(
                        AssistantProfileVersionRecord.tenant_id == tenant_id.value,
                        AssistantProfileVersionRecord.profile_id == assignment.profile_id,
                    )
                    .order_by(AssistantProfileVersionRecord.version.desc())
                    .limit(MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS)
                )
            ).all()
        )
        if not newest_records:
            raise RuntimeError("Active assistant profile was missing from its catalog")
        records = newest_records
        if not any(record.version == assignment.profile_version for record in records):
            active_record = await session.scalar(
                select(AssistantProfileVersionRecord).where(
                    AssistantProfileVersionRecord.tenant_id == tenant_id.value,
                    AssistantProfileVersionRecord.profile_id == assignment.profile_id,
                    AssistantProfileVersionRecord.version == assignment.profile_version,
                )
            )
            if active_record is None:
                raise RuntimeError("Active assistant profile was missing from its catalog")
            records = (
                *newest_records[: MAX_ASSISTANT_PROFILE_CATALOG_VERSIONS - 1],
                active_record,
            )
        profiles = tuple(assistant_profile_from_record(record) for record in records)
        if not profiles:
            raise RuntimeError("Active assistant profile was missing from its catalog")

        return AssistantProfileCatalog(
            active=ActiveAssistantProfile(
                tenant_id=tenant_id,
                profile_ref=AssistantProfileRef(
                    id=AssistantProfileId(assignment.profile_id),
                    version=assignment.profile_version,
                ),
                revision=assignment.revision,
                activated_at=assignment.activated_at,
                activated_by=UserId(assignment.activated_by),
            ),
            latest_version=newest_records[0].version,
            versions=profiles,
        )
