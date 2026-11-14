"""Tenant-safe HTTP queries for conversations, messages, and agent runs."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select

from enterprise_chat_platform.api.dependencies import get_runtime_session_factory
from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.chat import ConversationStatus, MessageRole
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    ConversationRecord,
    MessageRecord,
)

DEFAULT_MESSAGE_PAGE_SIZE = 50
MAX_MESSAGE_PAGE_SIZE = 100

type QueryErrorCode = Literal[
    "conversation_not_found",
    "agent_run_not_found",
]

router = APIRouter(
    prefix="/v1",
    tags=["queries"],
)


class ConversationResponse(BaseModel):
    """Public snapshot of one conversation owned by the caller."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    id: UUID
    status: ConversationStatus
    version: int = Field(ge=0)
    last_message_sequence: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    """Public representation of one immutable chat message."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    id: UUID
    conversation_id: UUID
    author_id: UUID
    role: MessageRole
    content: str
    sequence: int = Field(gt=0)
    created_at: datetime


class MessagePageResponse(BaseModel):
    """One stable, ascending page of conversation messages."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    items: tuple[MessageResponse, ...]
    next_cursor: int | None = Field(default=None, ge=1)


class AgentRunFailureResponse(BaseModel):
    """Failure information explicitly classified as safe for clients."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    safe_message: str


class AgentRunResponse(BaseModel):
    """Public lifecycle state for one agent run."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    id: UUID
    conversation_id: UUID
    input_message_id: UUID
    status: AgentRunStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    output_message_id: UUID | None
    failure: AgentRunFailureResponse | None


class QueryErrorDetail(BaseModel):
    """Stable public error detail that contains no lookup information."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    code: QueryErrorCode
    message: str


class QueryErrorResponse(BaseModel):
    """FastAPI-compatible error envelope."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    detail: QueryErrorDetail


class SqlAlchemyChatQueries:
    """Run read-only SQL queries through short-lived request sessions."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def get_conversation(
        self,
        *,
        actor: ActorContext,
        conversation_id: UUID,
    ) -> ConversationResponse | None:
        """Return an owned conversation without revealing other records."""
        statement = select(ConversationRecord).where(
            ConversationRecord.tenant_id == actor.tenant_id.value,
            ConversationRecord.id == conversation_id,
            ConversationRecord.created_by == actor.actor_id.value,
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            record = result.scalar_one_or_none()

            if record is None:
                return None

            return self._conversation_response(record)

    async def list_messages(
        self,
        *,
        actor: ActorContext,
        conversation_id: UUID,
        cursor: int | None,
        limit: int,
    ) -> MessagePageResponse | None:
        """Return the next deterministic message page for an owned conversation."""
        owner_statement = select(ConversationRecord.id).where(
            ConversationRecord.tenant_id == actor.tenant_id.value,
            ConversationRecord.id == conversation_id,
            ConversationRecord.created_by == actor.actor_id.value,
        )

        message_statement = (
            select(MessageRecord)
            .where(
                MessageRecord.tenant_id == actor.tenant_id.value,
                MessageRecord.conversation_id == conversation_id,
            )
            .order_by(
                MessageRecord.sequence.asc(),
                MessageRecord.id.asc(),
            )
            .limit(limit + 1)
        )

        if cursor is not None:
            message_statement = message_statement.where(MessageRecord.sequence > cursor)

        async with self._session_factory() as session:
            owner_result = await session.execute(owner_statement)

            if owner_result.scalar_one_or_none() is None:
                return None

            message_result = await session.execute(message_statement)
            records = list(message_result.scalars().all())

            has_more = len(records) > limit
            page_records = records[:limit]
            next_cursor = page_records[-1].sequence if has_more and page_records else None

            return MessagePageResponse(
                items=tuple(self._message_response(record) for record in page_records),
                next_cursor=next_cursor,
            )

    async def get_agent_run(
        self,
        *,
        actor: ActorContext,
        run_id: UUID,
    ) -> AgentRunResponse | None:
        """Return a run only when its conversation is owned by the caller."""
        statement = (
            select(AgentRunRecord)
            .join(
                ConversationRecord,
                and_(
                    ConversationRecord.tenant_id == AgentRunRecord.tenant_id,
                    ConversationRecord.id == AgentRunRecord.conversation_id,
                ),
            )
            .where(
                AgentRunRecord.tenant_id == actor.tenant_id.value,
                AgentRunRecord.id == run_id,
                ConversationRecord.tenant_id == actor.tenant_id.value,
                ConversationRecord.created_by == actor.actor_id.value,
            )
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            record = result.scalar_one_or_none()

            if record is None:
                return None

            return self._agent_run_response(record)

    @staticmethod
    def _conversation_response(record: ConversationRecord) -> ConversationResponse:
        return ConversationResponse(
            id=record.id,
            status=ConversationStatus(record.status),
            version=record.version,
            last_message_sequence=record.last_message_sequence,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _message_response(record: MessageRecord) -> MessageResponse:
        return MessageResponse(
            id=record.id,
            conversation_id=record.conversation_id,
            author_id=record.author_id,
            role=MessageRole(record.role),
            content=record.content,
            sequence=record.sequence,
            created_at=record.created_at,
        )

    @staticmethod
    def _agent_run_response(record: AgentRunRecord) -> AgentRunResponse:
        failure = None

        if record.failure_safe_message is not None:
            failure = AgentRunFailureResponse(
                safe_message=record.failure_safe_message,
            )

        return AgentRunResponse(
            id=record.id,
            conversation_id=record.conversation_id,
            input_message_id=record.input_message_id,
            status=AgentRunStatus(record.status),
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            output_message_id=record.output_message_id,
            failure=failure,
        )


def get_chat_queries(
    session_factory: Annotated[
        AsyncSessionFactory,
        Depends(get_runtime_session_factory),
    ],
) -> SqlAlchemyChatQueries:
    """Build the read service from the app-lifespan session factory."""
    return SqlAlchemyChatQueries(session_factory)


def _not_found(*, code: QueryErrorCode, message: str) -> HTTPException:
    detail = QueryErrorDetail(
        code=code,
        message=message,
    )
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail.model_dump(),
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": QueryErrorResponse}},
)
async def get_conversation(
    conversation_id: UUID,
    actor: Annotated[ActorContext, Depends(get_actor_context)],
    queries: Annotated[SqlAlchemyChatQueries, Depends(get_chat_queries)],
) -> ConversationResponse:
    """Return one conversation owned by the authenticated actor."""
    response = await queries.get_conversation(
        actor=actor,
        conversation_id=conversation_id,
    )

    if response is None:
        raise _not_found(
            code="conversation_not_found",
            message="Conversation was not found",
        )

    return response


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessagePageResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": QueryErrorResponse}},
)
async def list_messages(
    conversation_id: UUID,
    actor: Annotated[ActorContext, Depends(get_actor_context)],
    queries: Annotated[SqlAlchemyChatQueries, Depends(get_chat_queries)],
    cursor: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_MESSAGE_PAGE_SIZE),
    ] = DEFAULT_MESSAGE_PAGE_SIZE,
) -> MessagePageResponse:
    """List messages after an exclusive sequence cursor in ascending order."""
    response = await queries.list_messages(
        actor=actor,
        conversation_id=conversation_id,
        cursor=cursor,
        limit=limit,
    )

    if response is None:
        raise _not_found(
            code="conversation_not_found",
            message="Conversation was not found",
        )

    return response


@router.get(
    "/agent-runs/{run_id}",
    response_model=AgentRunResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": QueryErrorResponse}},
)
async def get_agent_run(
    run_id: UUID,
    actor: Annotated[ActorContext, Depends(get_actor_context)],
    queries: Annotated[SqlAlchemyChatQueries, Depends(get_chat_queries)],
) -> AgentRunResponse:
    """Return one agent run whose conversation is owned by the caller."""
    response = await queries.get_agent_run(
        actor=actor,
        run_id=run_id,
    )

    if response is None:
        raise _not_found(
            code="agent_run_not_found",
            message="Agent run was not found",
        )

    return response
