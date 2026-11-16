"""PostgreSQL persistence for human-review workflow state."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.application.errors import (
    HumanReviewConflictError,
    HumanReviewNotFoundError,
)
from enterprise_chat_platform.application.resolve_human_review import (
    ResolveHumanReviewCommand,
)
from enterprise_chat_platform.domain.human_review import HumanReview, HumanReviewStatus
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId, UserId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.human_review import (
    human_review_from_record,
    human_review_to_pending_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import HumanReviewRecord


class SqlAlchemyHumanReviewRepository:
    """Create cases within a caller-owned worker transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_pending_or_get(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
        created_at: datetime,
    ) -> HumanReview:
        """Create the single case for a run or return the existing case."""
        if not self._session.in_transaction():
            raise RuntimeError("Human review persistence requires an active transaction")

        candidate = human_review_to_pending_record(
            tenant_id=tenant_id,
            agent_run_id=agent_run_id,
            created_at=created_at,
        )
        statement = (
            insert(HumanReviewRecord)
            .values(
                tenant_id=candidate.tenant_id,
                agent_run_id=candidate.agent_run_id,
                policy_action=candidate.policy_action,
                status=candidate.status,
                version=candidate.version,
                created_at=candidate.created_at,
                resolved_by=None,
                resolved_at=None,
                resolution=None,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    HumanReviewRecord.tenant_id,
                    HumanReviewRecord.agent_run_id,
                ]
            )
            .returning(HumanReviewRecord)
        )
        result = await self._session.execute(statement)
        record = result.scalar_one_or_none()
        if record is None:
            record = await self._get_record(
                tenant_id=tenant_id,
                agent_run_id=agent_run_id,
            )
            if record is None:
                raise RuntimeError("Conflicting human review was not visible")
        return human_review_from_record(record)

    async def _get_record(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
    ) -> HumanReviewRecord | None:
        result = await self._session.execute(
            select(HumanReviewRecord).where(
                HumanReviewRecord.tenant_id == tenant_id.value,
                HumanReviewRecord.agent_run_id == agent_run_id.value,
            )
        )
        return result.scalar_one_or_none()


class SqlAlchemyHumanReviewResolutionStore:
    """Resolve cases through short, tenant-scoped optimistic transactions."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def resolve(
        self,
        *,
        tenant_id: TenantId,
        reviewer_id: UserId,
        command: ResolveHumanReviewCommand,
        resolved_at: datetime,
    ) -> HumanReview:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(HumanReviewRecord).where(
                        HumanReviewRecord.tenant_id == tenant_id.value,
                        HumanReviewRecord.agent_run_id == command.agent_run_id.value,
                    )
                )
                record = result.scalar_one_or_none()
                if record is None:
                    raise HumanReviewNotFoundError("Human review was not found")

                current = human_review_from_record(record)
                if (
                    current.status is not HumanReviewStatus.PENDING
                    or current.version != command.expected_version
                ):
                    raise HumanReviewConflictError("Human review changed before resolution")

                resolved = current.resolve(
                    reviewer_id=reviewer_id,
                    resolution=command.resolution,
                    at=resolved_at,
                )
                update_result = await session.execute(
                    update(HumanReviewRecord)
                    .where(
                        HumanReviewRecord.tenant_id == tenant_id.value,
                        HumanReviewRecord.agent_run_id == command.agent_run_id.value,
                        HumanReviewRecord.status == HumanReviewStatus.PENDING.value,
                        HumanReviewRecord.version == command.expected_version,
                    )
                    .values(
                        status=resolved.status.value,
                        version=resolved.version,
                        resolved_by=reviewer_id.value,
                        resolved_at=resolved.resolved_at,
                        resolution=resolved.resolution.value if resolved.resolution else None,
                    )
                    .returning(HumanReviewRecord)
                )
                updated_record = update_result.scalar_one_or_none()
                if updated_record is None:
                    raise HumanReviewConflictError("Human review changed before resolution")
                return human_review_from_record(updated_record)
