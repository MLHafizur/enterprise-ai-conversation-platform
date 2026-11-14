"""HTTP transport for accepting one tenant-scoped user turn."""

from datetime import datetime
from functools import partial
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import (
    AcceptUserTurnCommand,
    AcceptUserTurnHandler,
    ActorContext,
)
from enterprise_chat_platform.application.errors import (
    ConversationConcurrencyError,
    ConversationNotFoundError,
    IdempotencyConflictError,
)
from enterprise_chat_platform.domain.errors import DomainError
from enterprise_chat_platform.domain.identifiers import ConversationId
from enterprise_chat_platform.domain.primitives import IdempotencyKey
from enterprise_chat_platform.infrastructure.persistence.unit_of_work import (
    SqlAlchemyAcceptUserTurnUnitOfWork,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import (
    OwnerOnlyConversationWriteAuthorizer,
    UtcSystemClock,
    UuidTurnIds,
)
from enterprise_chat_platform.runtime import get_application_resources

router = APIRouter(
    prefix="/v1/conversations",
    tags=["conversation-turns"],
)

type TurnErrorCode = Literal[
    "conversation_not_found",
    "idempotency_conflict",
    "conversation_conflict",
    "invalid_turn",
]


class AcceptUserTurnRequest(BaseModel):
    """The only client-controlled JSON field for a user turn."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    content: str


class AcceptUserTurnResponse(BaseModel):
    """Stable representation of an accepted or replayed user turn."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    conversation_id: UUID
    conversation_version: int = Field(ge=0)
    message_id: UUID
    message_sequence: int = Field(gt=0)
    agent_run_id: UUID
    accepted_at: datetime
    replayed: bool


class TurnErrorDetail(BaseModel):
    """Stable machine code paired with a safe public message."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    code: TurnErrorCode
    message: str


class TurnErrorResponse(BaseModel):
    """FastAPI-compatible error envelope."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    detail: TurnErrorDetail


def get_accept_user_turn_handler(request: Request) -> AcceptUserTurnHandler:
    """Build the use-case handler from resources owned by the app lifespan."""
    application = cast(FastAPI, request.app)
    resources = get_application_resources(application)

    return AcceptUserTurnHandler(
        unit_of_work_factory=partial(
            SqlAlchemyAcceptUserTurnUnitOfWork,
            resources.session_factory,
        ),
        clock=UtcSystemClock(),
        ids=UuidTurnIds(),
        authorizer=OwnerOnlyConversationWriteAuthorizer(),
    )


def _turn_error(
    *,
    status_code: int,
    code: TurnErrorCode,
    message: str,
) -> HTTPException:
    detail = TurnErrorDetail(
        code=code,
        message=message,
    )
    return HTTPException(
        status_code=status_code,
        detail=detail.model_dump(),
    )


@router.post(
    "/{conversation_id}/turns",
    response_model=AcceptUserTurnResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": TurnErrorResponse},
        status.HTTP_409_CONFLICT: {"model": TurnErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": TurnErrorResponse},
    },
)
async def accept_user_turn(
    conversation_id: str,
    request_body: AcceptUserTurnRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    actor: Annotated[ActorContext, Depends(get_actor_context)],
    handler: Annotated[
        AcceptUserTurnHandler,
        Depends(get_accept_user_turn_handler),
    ],
) -> AcceptUserTurnResponse:
    """Accept one user message without trusting identity from client JSON."""
    try:
        command = AcceptUserTurnCommand(
            conversation_id=ConversationId.parse(conversation_id),
            content=request_body.content,
            idempotency_key=IdempotencyKey(idempotency_key),
        )
        result = await handler.handle(
            actor=actor,
            command=command,
        )
    except ConversationNotFoundError:
        raise _turn_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message="Conversation was not found",
        ) from None
    except IdempotencyConflictError:
        raise _turn_error(
            status_code=status.HTTP_409_CONFLICT,
            code="idempotency_conflict",
            message="Idempotency key conflicts with an earlier request",
        ) from None
    except ConversationConcurrencyError:
        raise _turn_error(
            status_code=status.HTTP_409_CONFLICT,
            code="conversation_conflict",
            message="Conversation changed; retry the request",
        ) from None
    except DomainError:
        raise _turn_error(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_turn",
            message="Turn request is invalid",
        ) from None

    return AcceptUserTurnResponse(
        conversation_id=result.conversation_id.value,
        conversation_version=result.conversation_version,
        message_id=result.message_id.value,
        message_sequence=result.message_sequence,
        agent_run_id=result.agent_run_id.value,
        accepted_at=result.accepted_at,
        replayed=result.replayed,
    )
