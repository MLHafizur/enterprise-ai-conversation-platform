from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from enterprise_chat_platform.api.assistant_profiles import (
    get_assistant_profile_admin_service,
    router,
)
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.errors import (
    AssistantProfileConflictError,
    AssistantProfileNotFoundError,
)
from enterprise_chat_platform.application.manage_assistant_profiles import (
    ActivateAssistantProfileVersionCommand,
    ActiveAssistantProfile,
    AssistantProfileAdminService,
    AssistantProfileCatalog,
    AssistantProfilePreview,
    CreateAssistantProfileVersionCommand,
)
from enterprise_chat_platform.application.tenant_catalog import default_assistant_profile
from enterprise_chat_platform.domain.assistant_profile import AssistantProfile
from enterprise_chat_platform.domain.identifiers import TenantId, UserId

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 11, 16, 22, 0, tzinfo=UTC)


def _principal(*roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor=ActorContext(tenant_id=TENANT_ID, actor_id=ACTOR_ID),
        roles=frozenset(roles),
    )


PROFILE_V1 = default_assistant_profile(tenant_id=TENANT_ID, created_at=NOW)
PROFILE_V2 = replace(
    PROFILE_V1,
    ref=replace(PROFILE_V1.ref, version=2),
    assistant_name="Acme Guide",
    created_by=ACTOR_ID,
)
CATALOG = AssistantProfileCatalog(
    active=ActiveAssistantProfile(
        tenant_id=TENANT_ID,
        profile_ref=PROFILE_V1.ref,
        revision=0,
        activated_at=NOW,
        activated_by=PROFILE_V1.created_by,
    ),
    latest_version=2,
    versions=(PROFILE_V2, PROFILE_V1),
)

FIELDS = {
    "prompt_template_version": "enterprise-chat.v1",
    "assistant_name": "Acme Guide",
    "organization_name": "Acme Corp",
    "response_tone": "friendly",
    "expertise": "product_specialist",
    "communication_style": "concise_supportive",
    "context_character_limit": 4096,
}


class _StubService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, object]] = []

    async def get_catalog(self, *, actor: ActorContext) -> AssistantProfileCatalog:
        self.calls.append(("catalog", actor))
        return CATALOG

    def preview(self, *, actor: ActorContext, fields: object) -> AssistantProfilePreview:
        self.calls.append(("preview", (actor, fields)))
        return AssistantProfilePreview(
            prompt_template_version="enterprise-chat.v1",
            system_instruction="You are Acme Guide.",
        )

    async def create_next_version(
        self,
        *,
        actor: ActorContext,
        command: CreateAssistantProfileVersionCommand,
    ) -> AssistantProfile:
        self.calls.append(("create", (actor, command)))
        if self.error is not None:
            raise self.error
        return PROFILE_V2

    async def activate(
        self,
        *,
        actor: ActorContext,
        command: ActivateAssistantProfileVersionCommand,
    ) -> AssistantProfileCatalog:
        self.calls.append(("activate", (actor, command)))
        if self.error is not None:
            raise self.error
        return replace(
            CATALOG,
            active=replace(
                CATALOG.active,
                profile_ref=PROFILE_V2.ref,
                revision=1,
                activated_by=ACTOR_ID,
            ),
        )


def _application(*, principal: AuthenticatedPrincipal, service: _StubService) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_authenticated_principal] = lambda: principal
    application.dependency_overrides[get_assistant_profile_admin_service] = lambda: cast(
        AssistantProfileAdminService,
        service,
    )
    return application


def test_admin_lists_tenant_catalog_without_tenant_input() -> None:
    service = _StubService()
    with TestClient(_application(principal=_principal("admin"), service=service)) as client:
        response = client.get("/v1/admin/assistant-profiles")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["active"]["profile_id"] == str(PROFILE_V1.ref.id)
    assert response.json()["latest_version"] == 2
    assert [item["version"] for item in response.json()["versions"]] == [2, 1]
    assert service.calls == [("catalog", _principal("admin").actor)]
    assert "tenant_id" not in response.text


@pytest.mark.parametrize("roles", [(), ("user",), ("agent",)])
def test_non_admin_is_forbidden_before_service_access(roles: tuple[str, ...]) -> None:
    service = _StubService()
    with TestClient(_application(principal=_principal(*roles), service=service)) as client:
        responses = (
            client.get("/v1/admin/assistant-profiles"),
            client.post("/v1/admin/assistant-profiles/preview", json=FIELDS),
            client.post(
                f"/v1/admin/assistant-profiles/{PROFILE_V1.ref.id}/versions",
                json={**FIELDS, "expected_latest_version": 1},
            ),
            client.post(
                f"/v1/admin/assistant-profiles/{PROFILE_V1.ref.id}/versions/2/activate",
                json={"expected_revision": 0},
            ),
        )

    assert {response.status_code for response in responses} == {403}
    assert service.calls == []


def test_preview_returns_only_server_rendered_instruction() -> None:
    service = _StubService()
    with TestClient(_application(principal=_principal("admin"), service=service)) as client:
        response = client.post("/v1/admin/assistant-profiles/preview", json=FIELDS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "prompt_template_version": "enterprise-chat.v1",
        "system_instruction": "You are Acme Guide.",
    }
    assert service.calls[0][0] == "preview"


def test_create_uses_path_ref_expected_version_and_signed_actor() -> None:
    service = _StubService()
    with TestClient(_application(principal=_principal("admin"), service=service)) as client:
        response = client.post(
            f"/v1/admin/assistant-profiles/{PROFILE_V1.ref.id}/versions",
            json={**FIELDS, "expected_latest_version": 1},
        )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    _, arguments = service.calls[0]
    actor, command = cast(tuple[ActorContext, CreateAssistantProfileVersionCommand], arguments)
    assert actor == _principal("admin").actor
    assert command.profile_id == PROFILE_V1.ref.id
    assert command.expected_latest_version == 1


def test_activation_uses_path_ref_revision_and_signed_actor() -> None:
    service = _StubService()
    with TestClient(_application(principal=_principal("admin"), service=service)) as client:
        response = client.post(
            f"/v1/admin/assistant-profiles/{PROFILE_V1.ref.id}/versions/2/activate",
            json={"expected_revision": 0},
        )

    assert response.status_code == 200
    assert response.json()["active"]["profile_version"] == 2
    assert response.json()["active"]["revision"] == 1
    _, arguments = service.calls[0]
    actor, command = cast(tuple[ActorContext, ActivateAssistantProfileVersionCommand], arguments)
    assert actor == _principal("admin").actor
    assert command.profile_ref == PROFILE_V2.ref
    assert command.expected_revision == 0


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (AssistantProfileNotFoundError("private"), 404, "assistant_profile_not_found"),
        (AssistantProfileConflictError("private"), 409, "assistant_profile_conflict"),
    ],
)
def test_create_maps_store_errors_to_safe_contract(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    service = _StubService(error=error)
    with TestClient(_application(principal=_principal("admin"), service=service)) as client:
        response = client.post(
            f"/v1/admin/assistant-profiles/{PROFILE_V1.ref.id}/versions",
            json={**FIELDS, "expected_latest_version": 1},
        )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert "private" not in response.text


def test_request_cannot_supply_tenant_or_audit_identity() -> None:
    service = _StubService()
    with TestClient(_application(principal=_principal("admin"), service=service)) as client:
        response = client.post(
            "/v1/admin/assistant-profiles/preview",
            json={
                **FIELDS,
                "tenant_id": str(UUID("99999999-9999-4999-8999-999999999999")),
                "created_by": str(ACTOR_ID),
            },
        )

    assert response.status_code == 422
    assert service.calls == []
