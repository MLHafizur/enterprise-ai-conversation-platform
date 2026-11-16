"""SQLAlchemy repository for immutable input-policy assessments."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.governance import PolicyAssessment
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId
from enterprise_chat_platform.infrastructure.persistence.mappers.input_policy_assessment import (
    input_policy_assessment_from_record,
    input_policy_assessment_to_new_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    InputPolicyAssessmentRecord,
)


class SqlAlchemyInputPolicyAssessmentRepository:
    """Store the first durable policy decision using a caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
    ) -> PolicyAssessment | None:
        """Return the durable decision for one tenant-scoped run, if present."""
        self._require_active_transaction("Input policy assessment lookup")

        record = await self._get_record(
            tenant_id=tenant_id,
            agent_run_id=agent_run_id,
        )
        if record is None:
            return None

        return input_policy_assessment_from_record(record)

    async def insert_or_get(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
        assessment: PolicyAssessment,
        assessed_at: datetime,
    ) -> PolicyAssessment:
        """Insert a decision or return the immutable decision that won the race."""
        self._require_active_transaction("Input policy assessment persistence")

        candidate = input_policy_assessment_to_new_record(
            tenant_id=tenant_id,
            agent_run_id=agent_run_id,
            assessment=assessment,
            assessed_at=assessed_at,
        )
        statement = (
            insert(InputPolicyAssessmentRecord)
            .values(
                tenant_id=candidate.tenant_id,
                agent_run_id=candidate.agent_run_id,
                policy_version=candidate.policy_version,
                action=candidate.action,
                risk_score=candidate.risk_score,
                findings=candidate.findings,
                assessed_at=candidate.assessed_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    InputPolicyAssessmentRecord.tenant_id,
                    InputPolicyAssessmentRecord.agent_run_id,
                ]
            )
            .returning(InputPolicyAssessmentRecord)
        )

        result = await self._session.execute(statement)
        stored = result.scalar_one_or_none()

        if stored is None:
            stored = await self._get_record(
                tenant_id=tenant_id,
                agent_run_id=agent_run_id,
            )
            if stored is None:
                raise RuntimeError("Conflicting input policy assessment was not visible")

        return input_policy_assessment_from_record(stored)

    async def _get_record(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
    ) -> InputPolicyAssessmentRecord | None:
        statement = select(InputPolicyAssessmentRecord).where(
            InputPolicyAssessmentRecord.tenant_id == tenant_id.value,
            InputPolicyAssessmentRecord.agent_run_id == agent_run_id.value,
        )
        result = await self._session.execute(statement)

        return result.scalar_one_or_none()

    def _require_active_transaction(self, action: str) -> None:
        if not self._session.in_transaction():
            raise RuntimeError(f"{action} requires an active transaction")
