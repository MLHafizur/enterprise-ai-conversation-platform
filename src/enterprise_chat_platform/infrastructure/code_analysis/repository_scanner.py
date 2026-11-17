"""Bounded Git repository ingestion for deterministic security analysis.

Git is treated as an untrusted network and filesystem boundary.  The caller
must pass a URL and refs that were validated by ``repository_url``.  Clones are
shallow, prompts are disabled, every command has a timeout, and source files
are read only after enforcing aggregate and per-file limits.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from enterprise_chat_platform.domain.code_security import SecurityFinding
from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    ValidatedRepositoryUrl,
    validate_git_ref,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import (
    SecurityEngine,
)

SUPPORTED_SOURCE_SUFFIXES = frozenset(
    {".c", ".cc", ".cpp", ".go", ".java", ".js", ".jsx", ".php", ".py", ".rb", ".ts", ".tsx"}
)
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)

INITIAL_HISTORY_DEPTH = 64
MAX_HISTORY_DEPTH = 1_024
_HEAD_REF = "refs/enterprise-security/head"
_BASE_REF = "refs/enterprise-security/base"


class RepositoryScanError(RuntimeError):
    """Safe, non-secret repository ingestion failure."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryScanLimits:
    """Resource ceilings applied before source enters the analyzer."""

    clone_timeout_seconds: float = 30
    scan_timeout_seconds: float = 120
    max_file_size_bytes: int = 1_048_576
    max_files: int = 2_000
    max_repository_bytes: int = 52_428_800
    max_findings: int = 10_000

    def __post_init__(self) -> None:
        values = (
            self.clone_timeout_seconds,
            self.scan_timeout_seconds,
            self.max_file_size_bytes,
            self.max_files,
            self.max_repository_bytes,
            self.max_findings,
        )
        if any(isinstance(value, bool) or value <= 0 for value in values):
            raise ValueError("Repository scan limits must be positive")
        if self.max_repository_bytes < self.max_file_size_bytes:
            raise ValueError("Repository byte limit cannot be smaller than file limit")


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryScanResult:
    """Content-safe result; source text appears only as redacted finding lines."""

    commit_sha: str
    files_analyzed: int
    bytes_analyzed: int
    findings: tuple[SecurityFinding, ...]


class BoundedGitRepositoryScanner:
    """Clone and inspect one public allowlisted repository without executing it."""

    def __init__(
        self,
        *,
        engine: SecurityEngine,
        limits: RepositoryScanLimits,
    ) -> None:
        self._engine = engine
        self._limits = limits

    async def scan_repository(
        self,
        *,
        repository: ValidatedRepositoryUrl,
        ref: str,
    ) -> RepositoryScanResult:
        trusted_ref = validate_git_ref(ref)
        with tempfile.TemporaryDirectory(prefix="enterprise-code-scan-") as directory:
            root = Path(directory)
            await self._initialize_repository(root=root, repository=repository)
            await self._fetch_ref(
                root=root,
                remote_ref=trusted_ref,
                local_ref=_HEAD_REF,
                depth=1,
            )
            await self._run_git(
                "-C",
                str(root),
                "checkout",
                "--quiet",
                "--detach",
                _HEAD_REF,
            )
            commit_sha = await self._commit_sha(root)
            return await self._scan_paths(root=root, relative_paths=None, commit_sha=commit_sha)

    async def scan_diff(
        self,
        *,
        repository: ValidatedRepositoryUrl,
        base_ref: str,
        head_ref: str,
    ) -> RepositoryScanResult:
        trusted_base = validate_git_ref(base_ref, field_name="Base Git reference")
        trusted_head = validate_git_ref(head_ref, field_name="Head Git reference")
        with tempfile.TemporaryDirectory(prefix="enterprise-code-diff-") as directory:
            root = Path(directory)
            await self._initialize_repository(root=root, repository=repository)
            await self._fetch_ref(
                root=root,
                remote_ref=trusted_head,
                local_ref=_HEAD_REF,
                depth=INITIAL_HISTORY_DEPTH,
            )
            await self._fetch_ref(
                root=root,
                remote_ref=trusted_base,
                local_ref=_BASE_REF,
                depth=INITIAL_HISTORY_DEPTH,
            )
            head_commit = await self._commit_for_ref(root=root, ref=_HEAD_REF)
            base_commit = await self._commit_for_ref(root=root, ref=_BASE_REF)
            await self._run_git("-C", str(root), "checkout", "--quiet", "--detach", head_commit)
            merge_base = await self._find_merge_base(
                root=root,
                head_commit=head_commit,
                base_commit=base_commit,
            )
            changed_output = await self._run_git(
                "-C",
                str(root),
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                "-z",
                f"{merge_base}..{head_commit}",
                "--",
            )
            relative_paths = tuple(
                path.decode("utf-8", errors="strict")
                for path in changed_output.split(b"\0")
                if path
            )
            commit_sha = await self._commit_sha(root)
            return await self._scan_paths(
                root=root,
                relative_paths=relative_paths,
                commit_sha=commit_sha,
            )

    async def _initialize_repository(
        self,
        *,
        root: Path,
        repository: ValidatedRepositoryUrl,
    ) -> None:
        await self._run_git("init", "--quiet", "--", str(root))
        await self._run_git(
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            repository.clone_url,
        )

    async def _fetch_ref(
        self,
        *,
        root: Path,
        remote_ref: str,
        local_ref: str,
        depth: int,
    ) -> None:
        await self._run_git(
            "-C",
            str(root),
            "fetch",
            "--quiet",
            "--depth",
            str(depth),
            "--no-tags",
            "--filter=blob:none",
            "origin",
            f"{remote_ref}:{local_ref}",
        )

    async def _commit_for_ref(self, *, root: Path, ref: str) -> str:
        output = await self._run_git("-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}")
        commit_sha = output.decode("ascii", errors="strict").strip().lower()
        if len(commit_sha) != 40 or any(
            character not in "0123456789abcdef" for character in commit_sha
        ):
            raise RepositoryScanError("Repository returned an invalid commit identifier")
        return commit_sha

    async def _find_merge_base(
        self,
        *,
        root: Path,
        head_commit: str,
        base_commit: str,
    ) -> str:
        depth = INITIAL_HISTORY_DEPTH
        while True:
            return_code, output = await self._run_git_result(
                "-C",
                str(root),
                "merge-base",
                head_commit,
                base_commit,
            )
            if return_code == 0:
                merge_base = output.decode("ascii", errors="strict").strip().lower()
                if len(merge_base) == 40 and all(
                    character in "0123456789abcdef" for character in merge_base
                ):
                    return merge_base
                raise RepositoryScanError("Repository returned an invalid merge base")
            if depth >= MAX_HISTORY_DEPTH:
                raise RepositoryScanError(
                    "Pull request history exceeds the configured merge-base limit"
                )
            deepen_by = min(depth, MAX_HISTORY_DEPTH - depth)
            await self._run_git(
                "-C",
                str(root),
                "fetch",
                "--quiet",
                "--deepen",
                str(deepen_by),
                "--no-tags",
                "--filter=blob:none",
                "origin",
                f"{head_commit}:{_HEAD_REF}",
                f"{base_commit}:{_BASE_REF}",
            )
            depth += deepen_by

    async def _commit_sha(self, root: Path) -> str:
        output = await self._run_git("-C", str(root), "rev-parse", "--verify", "HEAD")
        commit_sha = output.decode("ascii", errors="strict").strip().lower()
        if len(commit_sha) != 40 or any(
            character not in "0123456789abcdef" for character in commit_sha
        ):
            raise RepositoryScanError("Repository returned an invalid commit identifier")
        return commit_sha

    async def _run_git(self, *arguments: str) -> bytes:
        return_code, stdout = await self._run_git_result(*arguments)
        if return_code != 0:
            raise RepositoryScanError("Repository operation failed")
        return stdout

    async def _run_git_result(self, *arguments: str) -> tuple[int, bytes]:
        environment = {
            "GIT_ASKPASS": "/bin/false",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": tempfile.gettempdir(),
            "LANG": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._limits.clone_timeout_seconds,
            )
        except TimeoutError:
            if "process" in locals():
                process.kill()
                await process.wait()
            raise RepositoryScanError("Repository operation timed out") from None
        except OSError:
            raise RepositoryScanError("Repository operation could not start") from None
        return_code = process.returncode
        if return_code is None:  # pragma: no cover - communicate() always reaps the child
            raise RepositoryScanError("Repository operation did not finish")
        return return_code, stdout

    async def _scan_paths(
        self,
        *,
        root: Path,
        relative_paths: tuple[str, ...] | None,
        commit_sha: str,
    ) -> RepositoryScanResult:
        try:
            deadline = time.monotonic() + self._limits.scan_timeout_seconds
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._scan_paths_sync,
                    root=root,
                    relative_paths=relative_paths,
                    commit_sha=commit_sha,
                    deadline=deadline,
                ),
                timeout=self._limits.scan_timeout_seconds,
            )
        except TimeoutError:
            raise RepositoryScanError("Repository analysis timed out") from None

    def _scan_paths_sync(
        self,
        *,
        root: Path,
        relative_paths: tuple[str, ...] | None,
        commit_sha: str,
        deadline: float | None = None,
    ) -> RepositoryScanResult:
        paths = self._candidate_paths(root=root, relative_paths=relative_paths)
        findings: list[SecurityFinding] = []
        files_analyzed = 0
        bytes_analyzed = 0

        for relative_path, path in paths:
            if deadline is not None and time.monotonic() >= deadline:
                raise RepositoryScanError("Repository analysis timed out")
            if files_analyzed >= self._limits.max_files:
                raise RepositoryScanError("Repository exceeds the configured file limit")
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if (
                path.is_symlink()
                or not path.is_file()
                or stat.st_size > self._limits.max_file_size_bytes
            ):
                continue
            if bytes_analyzed + stat.st_size > self._limits.max_repository_bytes:
                raise RepositoryScanError("Repository exceeds the configured byte limit")
            try:
                raw_content = path.read_bytes()
                content = raw_content.decode("utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            if "\x00" in content or not content:
                continue

            file_findings = self._engine.analyze_code(content, relative_path)
            if deadline is not None and time.monotonic() >= deadline:
                raise RepositoryScanError("Repository analysis timed out")
            findings.extend(file_findings)
            if len(findings) > self._limits.max_findings:
                raise RepositoryScanError("Repository exceeds the configured finding limit")
            files_analyzed += 1
            bytes_analyzed += len(raw_content)

        return RepositoryScanResult(
            commit_sha=commit_sha,
            files_analyzed=files_analyzed,
            bytes_analyzed=bytes_analyzed,
            findings=tuple(findings),
        )

    def _candidate_paths(
        self,
        *,
        root: Path,
        relative_paths: tuple[str, ...] | None,
    ) -> Iterable[tuple[str, Path]]:
        if relative_paths is None:
            raw_paths = (path for path in root.rglob("*") if path.is_file())
        else:
            raw_paths = (root / relative_path for relative_path in relative_paths)

        root_resolved = root.resolve()
        for path in raw_paths:
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts[:-1]):
                continue
            if path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
                continue
            # ``resolve(strict=False)`` would follow symlinks, so reject them first.
            if path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_relative_to(root_resolved):
                continue
            relative_name = relative.as_posix()
            if not relative_name or len(relative_name) > 512:
                continue
            yield relative_name, path
