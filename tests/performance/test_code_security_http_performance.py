"""HTTP-boundary concurrency evidence for direct Day 10 snippet scans."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from enterprise_chat_platform.api.code_security import (
    get_analyze_code_service,
    router,
)
from enterprise_chat_platform.api.dependencies import get_runtime_settings
from enterprise_chat_platform.api.security import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.code_security import (
    AnalyzeCodeService,
    TenantCodeSecurityPolicy,
)
from enterprise_chat_platform.domain.identifiers import (
    CodeScanId,
    SecurityReviewId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine
from enterprise_chat_platform.settings import Settings

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
DATABASE_URL = (
    "postgresql+asyncpg://enterprise_chat_app:test-password@127.0.0.1:55432/enterprise_chat"
)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class _Ids:
    def new_scan_id(self) -> CodeScanId:
        return CodeScanId.new()

    def new_review_id(self) -> SecurityReviewId:
        return SecurityReviewId.new()


class _Persistence:
    def __init__(self) -> None:
        self.saved_scans: list[dict[str, object]] = []

    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy:
        assert tenant_id == TENANT_ID
        return TenantCodeSecurityPolicy()

    async def save_completed_scan(self, **values: object) -> None:
        self.saved_scans.append(values)


def _application(*, service: AnalyzeCodeService) -> FastAPI:
    settings = Settings(
        environment="test",
        database_url=SecretStr(DATABASE_URL),
        auth_mode="jwt",
        jwt_key=SecretStr("day10-performance-test-key"),
        realtime_enabled=False,
        provider_mode="disabled",
        code_security_enabled=True,
        _env_file=None,
    )
    principal = AuthenticatedPrincipal(
        actor=ActorContext(tenant_id=TENANT_ID, actor_id=ACTOR_ID),
        roles=frozenset({"agent"}),
    )
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_runtime_settings] = lambda: settings
    application.dependency_overrides[get_authenticated_principal] = lambda: principal
    application.dependency_overrides[get_analyze_code_service] = lambda: service
    return application


@pytest.mark.asyncio
async def test_five_concurrent_snippet_http_requests_complete_within_two_seconds() -> None:
    persistence = _Persistence()
    service = AnalyzeCodeService(
        persistence=persistence,
        engine=SecurityEngine(),
        clock=_Clock(),
        ids=_Ids(),
    )
    transport = httpx.ASGITransport(app=_application(service=service))

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = perf_counter()
        responses = await asyncio.gather(
            *(
                client.post(
                    "/v1/security/scans/snippet",
                    json={
                        "source_label": f"concurrent-{index}.py",
                        "code": f"eval(user_input_{index})\n",
                    },
                )
                for index in range(5)
            )
        )
        elapsed = perf_counter() - started

    assert [response.status_code for response in responses] == [201] * 5
    assert {response.json()["gate_decision"] for response in responses} == {"review_required"}
    assert {response.json()["findings"][0]["rule_id"] for response in responses} == {
        "unsafe_eval_exec"
    }
    assert len({response.json()["id"] for response in responses}) == 5
    assert len(persistence.saved_scans) == 5
    assert elapsed < 2
