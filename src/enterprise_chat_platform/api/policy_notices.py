"""Owner-scoped, safe input-policy notices for conversation messages."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select

from enterprise_chat_platform.api.dependencies import get_runtime_session_factory
from enterprise_chat_platform.api.queries import QueryErrorResponse
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.governance import PolicyAction, PolicySeverity
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.input_policy_assessment import (
    input_policy_assessment_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    ConversationRecord,
    InputPolicyAssessmentRecord,
)

type InputPolicyNoticeAction = Literal["block", "escalate"]

router = APIRouter(
    prefix="/v1",
    tags=["queries"],
)


class InputPolicyNoticeFindingResponse(BaseModel):
    """One finding intentionally safe to place beside a user's message."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    severity: PolicySeverity
    safe_message: str


class InputPolicyNoticeResponse(BaseModel):
    """A durable block or escalation decision linked to its exact input message."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    input_message_id: UUID
    action: InputPolicyNoticeAction
    findings: tuple[InputPolicyNoticeFindingResponse, ...]
    assessed_at: datetime


class InputPolicyNoticeCollectionResponse(BaseModel):
    """All durable policy notices for one owned conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    items: tuple[InputPolicyNoticeResponse, ...]


class SqlAlchemyInputPolicyNoticeQueries:
    """Read safe notices only after proving conversation ownership."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def list_for_conversation(
        self,
        *,
        actor: ActorContext,
        conversation_id: UUID,
    ) -> InputPolicyNoticeCollectionResponse | None:
        """Return notices for an owned conversation, or ``None`` when hidden."""
        tenant_id = actor.tenant_id.value
        owner_statement = select(ConversationRecord.id).where(
            ConversationRecord.tenant_id == tenant_id,
            ConversationRecord.id == conversation_id,
            ConversationRecord.created_by == actor.actor_id.value,
        )
        notice_statement = (
            select(
                InputPolicyAssessmentRecord,
                AgentRunRecord.input_message_id,
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
                AgentRunRecord.conversation_id == conversation_id,
                InputPolicyAssessmentRecord.action.in_(
                    (
                        PolicyAction.BLOCK.value,
                        PolicyAction.ESCALATE.value,
                    )
                ),
            )
            .order_by(
                InputPolicyAssessmentRecord.assessed_at.asc(),
                InputPolicyAssessmentRecord.agent_run_id.asc(),
            )
        )

        async with self._session_factory() as session:
            owner_result = await session.execute(owner_statement)
            if owner_result.scalar_one_or_none() is None:
                return None

            notice_result = await session.execute(notice_statement)

        return InputPolicyNoticeCollectionResponse(
            items=tuple(
                self._response(
                    record=record,
                    input_message_id=input_message_id,
                )
                for record, input_message_id in notice_result.all()
            )
        )

    @staticmethod
    def _response(
        *,
        record: InputPolicyAssessmentRecord,
        input_message_id: UUID,
    ) -> InputPolicyNoticeResponse:
        assessment = input_policy_assessment_from_record(record)
        action: InputPolicyNoticeAction
        if assessment.action is PolicyAction.BLOCK:
            action = "block"
        elif assessment.action is PolicyAction.ESCALATE:
            action = "escalate"
        else:
            raise ValueError("Input policy notice action must be block or escalate")

        return InputPolicyNoticeResponse(
            input_message_id=input_message_id,
            action=action,
            findings=tuple(
                InputPolicyNoticeFindingResponse(
                    severity=finding.severity,
                    safe_message=finding.safe_message,
                )
                for finding in assessment.findings
            ),
            assessed_at=record.assessed_at,
        )


def get_input_policy_notice_queries(
    session_factory: Annotated[
        AsyncSessionFactory,
        Depends(get_runtime_session_factory),
    ],
) -> SqlAlchemyInputPolicyNoticeQueries:
    """Build the owner-facing notice query service."""
    return SqlAlchemyInputPolicyNoticeQueries(session_factory)


@router.get(
    "/conversations/{conversation_id}/input-policy-notices",
    response_model=InputPolicyNoticeCollectionResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": QueryErrorResponse}},
)
async def list_input_policy_notices(
    conversation_id: UUID,
    actor: Annotated[ActorContext, Depends(get_actor_context)],
    queries: Annotated[
        SqlAlchemyInputPolicyNoticeQueries,
        Depends(get_input_policy_notice_queries),
    ],
) -> InputPolicyNoticeCollectionResponse:
    """List safe durable notices for a conversation owned by the caller."""
    response = await queries.list_for_conversation(
        actor=actor,
        conversation_id=conversation_id,
    )
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "conversation_not_found",
                "message": "Conversation was not found",
            },
        )
    return response
