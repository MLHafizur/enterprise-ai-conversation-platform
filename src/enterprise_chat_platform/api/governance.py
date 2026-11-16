"""Role-gated, tenant-scoped governance read API."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select

from enterprise_chat_platform.api.dependencies import get_runtime_session_factory
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicySeverity,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.input_policy_assessment import (
    input_policy_assessment_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    InputPolicyAssessmentRecord,
)

DEFAULT_RECENT_ASSESSMENT_LIMIT = 25
MAX_RECENT_ASSESSMENT_LIMIT = 100
GOVERNANCE_READ_ROLES = frozenset({"admin", "agent"})

router = APIRouter(
    prefix="/v1/governance",
    tags=["governance"],
)


class GovernancePolicyFindingResponse(BaseModel):
    """One allow-listed finding that is explicitly safe for dashboard users."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str
    severity: PolicySeverity
    safe_message: str


class GovernancePolicyAssessmentResponse(BaseModel):
    """Safe metadata for one recent input-policy decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_run_id: UUID
    conversation_id: UUID
    policy_version: str
    action: PolicyAction
    risk_score: float = Field(ge=0, le=1)
    findings: tuple[GovernancePolicyFindingResponse, ...]
    assessed_at: datetime


class GovernancePolicyCountsResponse(BaseModel):
    """Tenant-wide counts with an explicit zero for every supported action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    allow: int = Field(ge=0)
    block: int = Field(ge=0)
    escalate: int = Field(ge=0)
    total: int = Field(ge=0)


class GovernanceInputPolicyResponse(BaseModel):
    """Bounded tenant governance snapshot with no message content."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    counts: GovernancePolicyCountsResponse
    recent: tuple[GovernancePolicyAssessmentResponse, ...]


class SqlAlchemyGovernanceQueries:
    """Run tenant-scoped governance queries through request-owned sessions."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def get_input_policy_snapshot(
        self,
        *,
        principal: AuthenticatedPrincipal,
        limit: int,
    ) -> GovernanceInputPolicyResponse:
        """Return counts and recent safe decisions for the principal's tenant."""
        tenant_id = principal.actor.tenant_id.value
        count_statement = (
            select(
                InputPolicyAssessmentRecord.action,
                func.count(),
            )
            .where(InputPolicyAssessmentRecord.tenant_id == tenant_id)
            .group_by(InputPolicyAssessmentRecord.action)
        )
        recent_statement = (
            select(
                InputPolicyAssessmentRecord,
                AgentRunRecord.conversation_id,
            )
            .join(
                AgentRunRecord,
                and_(
                    AgentRunRecord.tenant_id == InputPolicyAssessmentRecord.tenant_id,
                    AgentRunRecord.id == InputPolicyAssessmentRecord.agent_run_id,
                ),
            )
            .where(
                InputPolicyAssessmentRecord.tenant_id == tenant_id,
                AgentRunRecord.tenant_id == tenant_id,
            )
            .order_by(
                InputPolicyAssessmentRecord.assessed_at.desc(),
                InputPolicyAssessmentRecord.agent_run_id.desc(),
            )
            .limit(limit)
        )

        async with self._session_factory() as session:
            count_result = await session.execute(count_statement)
            recent_result = await session.execute(recent_statement)

        counts = {action: 0 for action in PolicyAction}
        for action_value, action_count in count_result.all():
            counts[PolicyAction(action_value)] = action_count

        recent = tuple(
            self._assessment_response(
                record=record,
                conversation_id=conversation_id,
            )
            for record, conversation_id in recent_result.all()
        )
        total = sum(counts.values())

        return GovernanceInputPolicyResponse(
            counts=GovernancePolicyCountsResponse(
                allow=counts[PolicyAction.ALLOW],
                block=counts[PolicyAction.BLOCK],
                escalate=counts[PolicyAction.ESCALATE],
                total=total,
            ),
            recent=recent,
        )

    @staticmethod
    def _assessment_response(
        *,
        record: InputPolicyAssessmentRecord,
        conversation_id: UUID,
    ) -> GovernancePolicyAssessmentResponse:
        assessment = input_policy_assessment_from_record(record)
        return GovernancePolicyAssessmentResponse(
            agent_run_id=record.agent_run_id,
            conversation_id=conversation_id,
            policy_version=assessment.policy_version,
            action=assessment.action,
            risk_score=assessment.risk_score,
            findings=tuple(
                GovernancePolicyFindingResponse(
                    code=finding.code,
                    severity=finding.severity,
                    safe_message=finding.safe_message,
                )
                for finding in assessment.findings
            ),
            assessed_at=record.assessed_at,
        )


def require_governance_reader(
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(get_authenticated_principal),
    ],
) -> AuthenticatedPrincipal:
    """Reject authenticated principals without a governance-reader role."""
    if principal.roles.isdisjoint(GOVERNANCE_READ_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    return principal


def get_governance_queries(
    session_factory: Annotated[
        AsyncSessionFactory,
        Depends(get_runtime_session_factory),
    ],
) -> SqlAlchemyGovernanceQueries:
    """Build governance queries from the lifespan-managed session factory."""
    return SqlAlchemyGovernanceQueries(session_factory)


@router.get(
    "/input-policy",
    response_model=GovernanceInputPolicyResponse,
    responses={status.HTTP_403_FORBIDDEN: {"description": "Forbidden"}},
)
async def get_input_policy_governance(
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_governance_reader),
    ],
    queries: Annotated[
        SqlAlchemyGovernanceQueries,
        Depends(get_governance_queries),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_RECENT_ASSESSMENT_LIMIT),
    ] = DEFAULT_RECENT_ASSESSMENT_LIMIT,
) -> GovernanceInputPolicyResponse:
    """Return tenant-wide policy counts and a bounded recent-decision list."""
    return await queries.get_input_policy_snapshot(
        principal=principal,
        limit=limit,
    )
