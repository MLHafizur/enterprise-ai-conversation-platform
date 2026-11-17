"""Generous regression budgets for the Day 10 learning workload."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pytest

from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.code_security import (
    AnalyzeCodeService,
    TenantCodeSecurityPolicy,
)
from enterprise_chat_platform.domain.code_security import CodeScanSource
from enterprise_chat_platform.domain.identifiers import (
    CodeScanId,
    SecurityReviewId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    BoundedGitRepositoryScanner,
    RepositoryScanLimits,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _Ids:
    def new_scan_id(self) -> CodeScanId:
        return CodeScanId.new()

    def new_review_id(self) -> SecurityReviewId:
        return SecurityReviewId.new()


class _Persistence:
    async def get_policy(self, *, tenant_id: TenantId) -> TenantCodeSecurityPolicy:
        return TenantCodeSecurityPolicy()

    async def save_completed_scan(self, **values: object) -> None:
        del values


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        actor_id=UserId.parse("22222222-2222-4222-8222-222222222222"),
    )


def test_snippet_analysis_stays_below_two_second_learning_target() -> None:
    code = "\n".join(f'query_{index} = "SELECT " + user_input' for index in range(500))
    started = perf_counter()

    SecurityEngine().analyze_code(code, "service.py")

    assert perf_counter() - started < 2


@pytest.mark.asyncio
async def test_five_concurrent_snippet_requests_complete_within_two_seconds() -> None:
    service = AnalyzeCodeService(
        persistence=_Persistence(),
        engine=SecurityEngine(),
        clock=_Clock(),
        ids=_Ids(),
    )
    started = perf_counter()

    results = await asyncio.gather(
        *(
            service.analyze(
                actor=_actor(),
                source=CodeScanSource.SNIPPET,
                source_label=f"snippet-{index}.py",
                code=f"eval(user_input_{index})\n",
            )
            for index in range(5)
        )
    )

    assert len(results) == 5
    assert perf_counter() - started < 2


def test_local_repository_walk_stays_below_thirty_second_target(tmp_path: Path) -> None:
    for index in range(250):
        (tmp_path / f"module_{index}.py").write_text(
            "cursor.execute('SELECT * FROM users WHERE id = ' + user_id)\n",
            encoding="utf-8",
        )
    scanner = BoundedGitRepositoryScanner(
        engine=SecurityEngine(),
        limits=RepositoryScanLimits(
            max_file_size_bytes=10_000,
            max_files=500,
            max_repository_bytes=5_000_000,
            max_findings=1_000,
        ),
    )
    started = perf_counter()

    result = scanner._scan_paths_sync(  # noqa: SLF001 - performance boundary test
        root=tmp_path,
        relative_paths=None,
        commit_sha="a" * 40,
    )

    assert result.files_analyzed == 250
    assert perf_counter() - started < 30
