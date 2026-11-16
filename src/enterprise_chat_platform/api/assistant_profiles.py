"""Admin-only API for immutable tenant assistant profile versions."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field

from enterprise_chat_platform.api.dependencies import get_runtime_session_factory
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.errors import (
    AssistantProfileConflictError,
    AssistantProfileNotFoundError,
)
from enterprise_chat_platform.application.manage_assistant_profiles import (
    ActivateAssistantProfileVersionCommand,
    ActiveAssistantProfile,
    AssistantProfileAdminService,
    AssistantProfileCatalog,
    AssistantProfileFields,
    AssistantProfilePreview,
    CreateAssistantProfileVersionCommand,
)
from enterprise_chat_platform.application.tenant_prompting import TenantPromptBuilder
from enterprise_chat_platform.domain.assistant_profile import (
    MAX_CONTEXT_CHARACTER_LIMIT,
    MAX_PROFILE_TEXT_LENGTH,
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.errors import DomainError
from enterprise_chat_platform.domain.identifiers import AssistantProfileId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.assistant_profile import (
    SqlAlchemyAssistantProfileAdminStore,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock

router = APIRouter(
    prefix="/v1/admin/assistant-profiles",
    tags=["assistant-profile-administration"],
)

type AssistantProfileErrorCode = Literal[
    "assistant_profile_not_found",
    "assistant_profile_conflict",
    "invalid_assistant_profile",
]


class AssistantProfileFieldsRequest(BaseModel):
    """Only typed values that the server renderer knows how to interpret."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prompt_template_version: Literal["enterprise-chat.v1"]
    assistant_name: str = Field(
        min_length=1,
        max_length=MAX_PROFILE_TEXT_LENGTH,
        strict=True,
    )
    organization_name: str = Field(
        min_length=1,
        max_length=MAX_PROFILE_TEXT_LENGTH,
        strict=True,
    )
    response_tone: Literal["professional", "friendly"]
    expertise: Literal["senior_engineer", "product_specialist"]
    communication_style: Literal["direct_helpful", "concise_supportive"]
    context_character_limit: int = Field(
        ge=1,
        le=MAX_CONTEXT_CHARACTER_LIMIT,
        strict=True,
    )

    def to_domain(self) -> AssistantProfileFields:
        return AssistantProfileFields(
            prompt_template_version=self.prompt_template_version,
            assistant_name=self.assistant_name,
            organization_name=self.organization_name,
            response_tone=PromptResponseTone(self.response_tone),
            expertise=PromptExpertiseLevel(self.expertise),
            communication_style=PromptCommunicationStyle(self.communication_style),
            context_character_limit=self.context_character_limit,
        )


class CreateAssistantProfileVersionRequest(AssistantProfileFieldsRequest):
    expected_latest_version: int = Field(ge=1, strict=True)


class ActivateAssistantProfileVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    expected_revision: int = Field(ge=0, strict=True)


class AssistantProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    profile_id: UUID
    version: int = Field(gt=0)
    prompt_template_version: str
    assistant_name: str
    organization_name: str
    response_tone: PromptResponseTone
    expertise: PromptExpertiseLevel
    communication_style: PromptCommunicationStyle
    context_character_limit: int = Field(gt=0)
    created_at: datetime
    created_by: UUID


class ActiveAssistantProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    profile_id: UUID
    profile_version: int = Field(gt=0)
    revision: int = Field(ge=0)
    activated_at: datetime
    activated_by: UUID


class AssistantProfileCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    active: ActiveAssistantProfileResponse
    latest_version: int = Field(gt=0)
    versions: tuple[AssistantProfileResponse, ...]


class AssistantProfilePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prompt_template_version: str
    system_instruction: str


class AssistantProfileErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: AssistantProfileErrorCode
    message: str


class AssistantProfileErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    detail: AssistantProfileErrorDetail


def require_assistant_profile_admin(
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(get_authenticated_principal),
    ],
) -> AuthenticatedPrincipal:
    """Require the signed admin role before constructing any data dependency."""
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    return principal


def get_assistant_profile_admin_service(
    session_factory: Annotated[
        AsyncSessionFactory,
        Depends(get_runtime_session_factory),
    ],
) -> AssistantProfileAdminService:
    return AssistantProfileAdminService(
        store=SqlAlchemyAssistantProfileAdminStore(session_factory),
        clock=UtcSystemClock(),
        prompt_builder=TenantPromptBuilder(),
    )


def _profile_response(profile: AssistantProfile) -> AssistantProfileResponse:
    return AssistantProfileResponse(
        profile_id=profile.ref.id.value,
        version=profile.ref.version,
        prompt_template_version=profile.prompt_template_version,
        assistant_name=profile.assistant_name,
        organization_name=profile.organization_name,
        response_tone=profile.response_tone,
        expertise=profile.expertise,
        communication_style=profile.communication_style,
        context_character_limit=profile.context_character_limit,
        created_at=profile.created_at,
        created_by=profile.created_by.value,
    )


def _active_response(active: ActiveAssistantProfile) -> ActiveAssistantProfileResponse:
    return ActiveAssistantProfileResponse(
        profile_id=active.profile_ref.id.value,
        profile_version=active.profile_ref.version,
        revision=active.revision,
        activated_at=active.activated_at,
        activated_by=active.activated_by.value,
    )


def _catalog_response(catalog: AssistantProfileCatalog) -> AssistantProfileCatalogResponse:
    return AssistantProfileCatalogResponse(
        active=_active_response(catalog.active),
        latest_version=catalog.latest_version,
        versions=tuple(_profile_response(profile) for profile in catalog.versions),
    )


def _preview_response(preview: AssistantProfilePreview) -> AssistantProfilePreviewResponse:
    return AssistantProfilePreviewResponse(
        prompt_template_version=preview.prompt_template_version,
        system_instruction=preview.system_instruction,
    )


def _not_found() -> HTTPException:
    detail = AssistantProfileErrorDetail(
        code="assistant_profile_not_found",
        message="Assistant profile was not found",
    )
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail.model_dump())


def _conflict() -> HTTPException:
    detail = AssistantProfileErrorDetail(
        code="assistant_profile_conflict",
        message="Assistant profile changed before the request completed",
    )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail.model_dump())


def _invalid() -> HTTPException:
    detail = AssistantProfileErrorDetail(
        code="invalid_assistant_profile",
        message="Assistant profile values are invalid",
    )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=detail.model_dump(),
    )


@router.get(
    "",
    response_model=AssistantProfileCatalogResponse,
    responses={status.HTTP_403_FORBIDDEN: {"description": "Forbidden"}},
)
async def list_assistant_profiles(
    http_response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_assistant_profile_admin),
    ],
    service: Annotated[
        AssistantProfileAdminService,
        Depends(get_assistant_profile_admin_service),
    ],
) -> AssistantProfileCatalogResponse:
    """Return the authenticated tenant's immutable profile catalog."""
    http_response.headers["Cache-Control"] = "no-store"
    catalog = await service.get_catalog(actor=principal.actor)
    return _catalog_response(catalog)


@router.post(
    "/preview",
    response_model=AssistantProfilePreviewResponse,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AssistantProfileErrorResponse}},
)
async def preview_assistant_profile(
    request_body: AssistantProfileFieldsRequest,
    http_response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_assistant_profile_admin),
    ],
    service: Annotated[
        AssistantProfileAdminService,
        Depends(get_assistant_profile_admin_service),
    ],
) -> AssistantProfilePreviewResponse:
    """Render typed fields with the same trusted renderer used by workers."""
    http_response.headers["Cache-Control"] = "no-store"
    try:
        preview = service.preview(
            actor=principal.actor,
            fields=request_body.to_domain(),
        )
    except DomainError:
        raise _invalid() from None
    return _preview_response(preview)


@router.post(
    "/{profile_id}/versions",
    response_model=AssistantProfileResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": AssistantProfileErrorResponse},
        status.HTTP_409_CONFLICT: {"model": AssistantProfileErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AssistantProfileErrorResponse},
    },
)
async def create_assistant_profile_version(
    profile_id: UUID,
    request_body: CreateAssistantProfileVersionRequest,
    http_response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_assistant_profile_admin),
    ],
    service: Annotated[
        AssistantProfileAdminService,
        Depends(get_assistant_profile_admin_service),
    ],
) -> AssistantProfileResponse:
    """Append the next immutable version without changing the active pointer."""
    http_response.headers["Cache-Control"] = "no-store"
    try:
        profile = await service.create_next_version(
            actor=principal.actor,
            command=CreateAssistantProfileVersionCommand(
                profile_id=AssistantProfileId(profile_id),
                expected_latest_version=request_body.expected_latest_version,
                fields=request_body.to_domain(),
            ),
        )
    except AssistantProfileNotFoundError:
        raise _not_found() from None
    except AssistantProfileConflictError:
        raise _conflict() from None
    except DomainError:
        raise _invalid() from None
    return _profile_response(profile)


@router.post(
    "/{profile_id}/versions/{version}/activate",
    response_model=AssistantProfileCatalogResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": AssistantProfileErrorResponse},
        status.HTTP_409_CONFLICT: {"model": AssistantProfileErrorResponse},
    },
)
async def activate_assistant_profile_version(
    profile_id: UUID,
    version: Annotated[int, Path(ge=1)],
    request_body: ActivateAssistantProfileVersionRequest,
    http_response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_assistant_profile_admin),
    ],
    service: Annotated[
        AssistantProfileAdminService,
        Depends(get_assistant_profile_admin_service),
    ],
) -> AssistantProfileCatalogResponse:
    """Move the tenant pointer if the caller's assignment revision is current."""
    http_response.headers["Cache-Control"] = "no-store"
    try:
        catalog = await service.activate(
            actor=principal.actor,
            command=ActivateAssistantProfileVersionCommand(
                profile_ref=AssistantProfileRef(
                    id=AssistantProfileId(profile_id),
                    version=version,
                ),
                expected_revision=request_body.expected_revision,
            ),
        )
    except AssistantProfileNotFoundError:
        raise _not_found() from None
    except AssistantProfileConflictError:
        raise _conflict() from None
    return _catalog_response(catalog)
