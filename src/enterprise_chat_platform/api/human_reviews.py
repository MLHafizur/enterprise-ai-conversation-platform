"""Tenant-scoped governance API for durable human-review cases."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select

from enterprise_chat_platform.api.dependencies import get_runtime_session_factory
from enterprise_chat_platform.api.governance import require_governance_reader
from enterprise_chat_platform.api.security import AuthenticatedPrincipal
from enterprise_chat_platform.application.errors import (
    HumanReviewConflictError,
    HumanReviewNotFoundError,
)
from enterprise_chat_platform.application.resolve_human_review import (
    ResolveHumanReviewCommand,
    ResolveHumanReviewHandler,
)
from enterprise_chat_platform.domain.chat import MessageRole
from enterprise_chat_platform.domain.governance import PolicySeverity
from enterprise_chat_platform.domain.human_review import (
    HumanReviewResolution,
    HumanReviewStatus,
)
from enterprise_chat_platform.domain.identifiers import AgentRunId, TenantId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.human_review import (
    human_review_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.input_policy_assessment import (
    input_policy_assessment_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    HumanReviewRecord,
    InputPolicyAssessmentRecord,
    MessageRecord,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.human_review import (
    SqlAlchemyHumanReviewResolutionStore,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock

DEFAULT_HUMAN_REVIEW_LIMIT = 25
MAX_HUMAN_REVIEW_LIMIT = 100

router = APIRouter(prefix="/v1/governance/human-reviews", tags=["governance"])


class HumanReviewFindingResponse(BaseModel):
    """One allow-listed policy reason safe for an authorized reviewer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str
    severity: PolicySeverity
    safe_message: str


class HumanReviewCaseResponse(BaseModel):
    """Review workflow metadata that intentionally omits message content."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_run_id: UUID
    conversation_id: UUID
    input_message_id: UUID
    status: HumanReviewStatus
    version: int = Field(ge=0, le=1)
    policy_version: str
    risk_score: float = Field(ge=0, le=1)
    findings: tuple[HumanReviewFindingResponse, ...]
    created_at: datetime
    resolved_at: datetime | None
    resolution: HumanReviewResolution | None


class HumanReviewDetailResponse(HumanReviewCaseResponse):
    """Exact escalated input exposed only through the reviewer detail API."""

    input_content: str


class HumanReviewCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    items: tuple[HumanReviewCaseResponse, ...]


class ResolveHumanReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    expected_version: int = Field(ge=0, le=1)
    resolution: Literal["confirmed", "dismissed"]


class HumanReviewErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: Literal["human_review_not_found", "human_review_conflict"]
    message: str


class HumanReviewErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    detail: HumanReviewErrorDetail


class SqlAlchemyHumanReviewQueries:
    """Run bounded tenant-scoped reviewer queries."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def list_cases(
        self,
        *,
        tenant_id: TenantId,
        review_status: HumanReviewStatus,
        limit: int,
    ) -> HumanReviewCollectionResponse:
        statement = (
            select(
                HumanReviewRecord,
                InputPolicyAssessmentRecord,
                AgentRunRecord.conversation_id,
                AgentRunRecord.input_message_id,
            )
            .join(
                InputPolicyAssessmentRecord,
                and_(
                    InputPolicyAssessmentRecord.tenant_id == HumanReviewRecord.tenant_id,
                    InputPolicyAssessmentRecord.agent_run_id == HumanReviewRecord.agent_run_id,
                    InputPolicyAssessmentRecord.action == HumanReviewRecord.policy_action,
                ),
            )
            .join(
                AgentRunRecord,
                and_(
                    AgentRunRecord.tenant_id == HumanReviewRecord.tenant_id,
                    AgentRunRecord.id == HumanReviewRecord.agent_run_id,
                ),
            )
            .where(
                HumanReviewRecord.tenant_id == tenant_id.value,
                InputPolicyAssessmentRecord.tenant_id == tenant_id.value,
                AgentRunRecord.tenant_id == tenant_id.value,
                HumanReviewRecord.status == review_status.value,
            )
            .order_by(
                HumanReviewRecord.created_at.asc(),
                HumanReviewRecord.agent_run_id.asc(),
            )
            .limit(limit)
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
        return HumanReviewCollectionResponse(
            items=tuple(
                self._case_response(
                    review_record=review_record,
                    assessment_record=assessment_record,
                    conversation_id=conversation_id,
                    input_message_id=input_message_id,
                )
                for (
                    review_record,
                    assessment_record,
                    conversation_id,
                    input_message_id,
                ) in result.all()
            )
        )

    async def get_detail(
        self,
        *,
        tenant_id: TenantId,
        agent_run_id: AgentRunId,
    ) -> HumanReviewDetailResponse | None:
        statement = (
            select(
                HumanReviewRecord,
                InputPolicyAssessmentRecord,
                AgentRunRecord.conversation_id,
                AgentRunRecord.input_message_id,
                MessageRecord.content,
            )
            .join(
                InputPolicyAssessmentRecord,
                and_(
                    InputPolicyAssessmentRecord.tenant_id == HumanReviewRecord.tenant_id,
                    InputPolicyAssessmentRecord.agent_run_id == HumanReviewRecord.agent_run_id,
                    InputPolicyAssessmentRecord.action == HumanReviewRecord.policy_action,
                ),
            )
            .join(
                AgentRunRecord,
                and_(
                    AgentRunRecord.tenant_id == HumanReviewRecord.tenant_id,
                    AgentRunRecord.id == HumanReviewRecord.agent_run_id,
                ),
            )
            .join(
                MessageRecord,
                and_(
                    MessageRecord.tenant_id == AgentRunRecord.tenant_id,
                    MessageRecord.conversation_id == AgentRunRecord.conversation_id,
                    MessageRecord.id == AgentRunRecord.input_message_id,
                ),
            )
            .where(
                HumanReviewRecord.tenant_id == tenant_id.value,
                HumanReviewRecord.agent_run_id == agent_run_id.value,
                InputPolicyAssessmentRecord.tenant_id == tenant_id.value,
                AgentRunRecord.tenant_id == tenant_id.value,
                MessageRecord.tenant_id == tenant_id.value,
                MessageRecord.role == MessageRole.USER.value,
            )
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
        row = result.one_or_none()
        if row is None:
            return None
        review_record, assessment_record, conversation_id, input_message_id, content = row
        case = self._case_response(
            review_record=review_record,
            assessment_record=assessment_record,
            conversation_id=conversation_id,
            input_message_id=input_message_id,
        )
        return HumanReviewDetailResponse(**case.model_dump(), input_content=content)

    @staticmethod
    def _case_response(
        *,
        review_record: HumanReviewRecord,
        assessment_record: InputPolicyAssessmentRecord,
        conversation_id: UUID,
        input_message_id: UUID,
    ) -> HumanReviewCaseResponse:
        review = human_review_from_record(review_record)
        assessment = input_policy_assessment_from_record(assessment_record)
        return HumanReviewCaseResponse(
            agent_run_id=review.agent_run_id.value,
            conversation_id=conversation_id,
            input_message_id=input_message_id,
            status=review.status,
            version=review.version,
            policy_version=assessment.policy_version,
            risk_score=assessment.risk_score,
            findings=tuple(
                HumanReviewFindingResponse(
                    code=finding.code,
                    severity=finding.severity,
                    safe_message=finding.safe_message,
                )
                for finding in assessment.findings
            ),
            created_at=review.created_at,
            resolved_at=review.resolved_at,
            resolution=review.resolution,
        )


def get_human_review_queries(
    session_factory: Annotated[
        AsyncSessionFactory,
        Depends(get_runtime_session_factory),
    ],
) -> SqlAlchemyHumanReviewQueries:
    return SqlAlchemyHumanReviewQueries(session_factory)


def get_resolve_human_review_handler(
    session_factory: Annotated[
        AsyncSessionFactory,
        Depends(get_runtime_session_factory),
    ],
) -> ResolveHumanReviewHandler:
    return ResolveHumanReviewHandler(store=SqlAlchemyHumanReviewResolutionStore(session_factory))


def _not_found() -> HTTPException:
    detail = HumanReviewErrorDetail(
        code="human_review_not_found",
        message="Human review was not found",
    )
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail.model_dump())


def _conflict() -> HTTPException:
    detail = HumanReviewErrorDetail(
        code="human_review_conflict",
        message="Human review changed before resolution",
    )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail.model_dump())


@router.get("", response_model=HumanReviewCollectionResponse)
async def list_human_reviews(
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_governance_reader),
    ],
    queries: Annotated[
        SqlAlchemyHumanReviewQueries,
        Depends(get_human_review_queries),
    ],
    review_status: Annotated[HumanReviewStatus, Query(alias="status")] = HumanReviewStatus.PENDING,
    limit: Annotated[int, Query(ge=1, le=MAX_HUMAN_REVIEW_LIMIT)] = DEFAULT_HUMAN_REVIEW_LIMIT,
) -> HumanReviewCollectionResponse:
    """List a bounded FIFO queue without exposing message content."""
    return await queries.list_cases(
        tenant_id=principal.actor.tenant_id,
        review_status=review_status,
        limit=limit,
    )


@router.get(
    "/{agent_run_id}",
    response_model=HumanReviewDetailResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": HumanReviewErrorResponse}},
)
async def get_human_review(
    agent_run_id: UUID,
    http_response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_governance_reader),
    ],
    queries: Annotated[
        SqlAlchemyHumanReviewQueries,
        Depends(get_human_review_queries),
    ],
) -> HumanReviewDetailResponse:
    """Return the exact escalated input for an authorized tenant reviewer."""
    http_response.headers["Cache-Control"] = "no-store"
    detail = await queries.get_detail(
        tenant_id=principal.actor.tenant_id,
        agent_run_id=AgentRunId(agent_run_id),
    )
    if detail is None:
        raise _not_found()
    return detail


@router.post(
    "/{agent_run_id}/resolve",
    response_model=HumanReviewDetailResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": HumanReviewErrorResponse},
        status.HTTP_409_CONFLICT: {"model": HumanReviewErrorResponse},
    },
)
async def resolve_human_review(
    agent_run_id: UUID,
    request_body: ResolveHumanReviewRequest,
    http_response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_governance_reader),
    ],
    handler: Annotated[
        ResolveHumanReviewHandler,
        Depends(get_resolve_human_review_handler),
    ],
    queries: Annotated[
        SqlAlchemyHumanReviewQueries,
        Depends(get_human_review_queries),
    ],
) -> HumanReviewDetailResponse:
    """Resolve one pending case without resuming its terminal agent run."""
    http_response.headers["Cache-Control"] = "no-store"
    run_id = AgentRunId(agent_run_id)
    try:
        await handler.handle(
            tenant_id=principal.actor.tenant_id,
            reviewer_id=principal.actor.actor_id,
            command=ResolveHumanReviewCommand(
                agent_run_id=run_id,
                expected_version=request_body.expected_version,
                resolution=HumanReviewResolution(request_body.resolution),
            ),
            resolved_at=UtcSystemClock().now(),
        )
    except HumanReviewNotFoundError:
        raise _not_found() from None
    except HumanReviewConflictError:
        raise _conflict() from None

    detail = await queries.get_detail(
        tenant_id=principal.actor.tenant_id,
        agent_run_id=run_id,
    )
    if detail is None:
        raise RuntimeError("Resolved human review could not be reloaded")
    return detail
