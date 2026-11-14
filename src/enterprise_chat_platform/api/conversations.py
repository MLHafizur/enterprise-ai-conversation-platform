"""HTTP transport for idempotent conversation provisioning."""

from datetime import datetime
from functools import partial
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from enterprise_chat_platform.api.security import get_actor_context
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.create_conversation import (
    CreateConversationCommand,
    CreateConversationHandler,
)
from enterprise_chat_platform.application.errors import ConversationNotFoundError
from enterprise_chat_platform.domain.errors import DomainError
from enterprise_chat_platform.domain.identifiers import ConversationId
from enterprise_chat_platform.infrastructure.persistence.create_conversation_unit_of_work import (
    SqlAlchemyCreateConversationUnitOfWork,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock
from enterprise_chat_platform.runtime import get_application_resources

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

type ConversationProvisionErrorCode = Literal[
    "conversation_not_found",
    "invalid_conversation",
]


class CreateConversationResponse(BaseModel):
    """Stable result for both the first PUT and an exact replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    created_at: datetime
    replayed: bool


class ConversationProvisionErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ConversationProvisionErrorCode
    message: str


class ConversationProvisionErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: ConversationProvisionErrorDetail


def get_create_conversation_handler(request: Request) -> CreateConversationHandler:
    """Build the use-case handler from lifespan-managed resources."""
    application = cast(FastAPI, request.app)
    resources = get_application_resources(application)
    return CreateConversationHandler(
        unit_of_work_factory=partial(
            SqlAlchemyCreateConversationUnitOfWork,
            resources.session_factory,
        ),
        clock=UtcSystemClock(),
    )


def _provision_error(
    *,
    status_code: int,
    code: ConversationProvisionErrorCode,
    message: str,
) -> HTTPException:
    detail = ConversationProvisionErrorDetail(code=code, message=message)
    return HTTPException(status_code=status_code, detail=detail.model_dump())


@router.put(
    "/{conversation_id}",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ConversationProvisionErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ConversationProvisionErrorResponse},
    },
)
async def create_conversation(
    conversation_id: str,
    response: Response,
    actor: Annotated[ActorContext, Depends(get_actor_context)],
    handler: Annotated[
        CreateConversationHandler,
        Depends(get_create_conversation_handler),
    ],
) -> CreateConversationResponse:
    """Create a client-addressable conversation or safely replay that PUT."""
    try:
        result = await handler.handle(
            actor=actor,
            command=CreateConversationCommand(
                conversation_id=ConversationId.parse(conversation_id),
            ),
        )
    except ConversationNotFoundError:
        raise _provision_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message="Conversation was not found",
        ) from None
    except DomainError:
        raise _provision_error(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_conversation",
            message="Conversation identifier is invalid",
        ) from None

    if result.replayed:
        response.status_code = status.HTTP_200_OK

    return CreateConversationResponse(
        conversation_id=result.conversation_id.value,
        created_at=result.created_at,
        replayed=result.replayed,
    )
