import os
import subprocess
from pathlib import Path

import pytest

from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    BoundedGitRepositoryScanner,
    RepositoryScanError,
    RepositoryScanLimits,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    ValidatedRepositoryUrl,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import (
    SecurityEngine,
)


def _scanner(**overrides: int) -> BoundedGitRepositoryScanner:
    values: dict[str, object] = {
        "max_file_size_bytes": 1_024,
        "max_files": 10,
        "max_repository_bytes": 4_096,
        "max_findings": 20,
    }
    values.update(overrides)
    return BoundedGitRepositoryScanner(
        engine=SecurityEngine(),
        limits=RepositoryScanLimits(**values),  # type: ignore[arg-type]
    )


def test_local_scan_is_bounded_and_redacts_secrets(tmp_path: Path) -> None:
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    (tmp_path / "vulnerable.py").write_text(
        'api_key = "sk-this-must-never-be-returned"\n',
        encoding="utf-8",
    )
    (tmp_path / "ignored.bin").write_bytes(b"\x00secret")

    result = _scanner()._scan_paths_sync(  # noqa: SLF001 - bounded adapter unit test
        root=tmp_path,
        relative_paths=None,
        commit_sha="a" * 40,
    )

    assert result.files_analyzed == 2
    assert result.commit_sha == "a" * 40
    assert len(result.findings) == 1
    assert "sk-this" not in result.findings[0].code_snippet
    assert "[REDACTED]" in result.findings[0].code_snippet


def test_local_scan_rejects_repository_byte_overflow(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("a" * 40, encoding="utf-8")
    (tmp_path / "two.py").write_text("b" * 40, encoding="utf-8")

    with pytest.raises(RepositoryScanError, match="byte limit"):
        _scanner(max_file_size_bytes=64, max_repository_bytes=64)._scan_paths_sync(  # noqa: SLF001
            root=tmp_path,
            relative_paths=None,
            commit_sha="b" * 40,
        )


def test_local_scan_rejects_oversized_supported_source(tmp_path: Path) -> None:
    (tmp_path / "oversized.py").write_text("a" * 65, encoding="utf-8")

    with pytest.raises(RepositoryScanError, match="file size limit"):
        _scanner(max_file_size_bytes=64, max_repository_bytes=128)._scan_paths_sync(  # noqa: SLF001
            root=tmp_path,
            relative_paths=None,
            commit_sha="c" * 40,
        )


def test_local_scan_rejects_overlong_supported_source_path(tmp_path: Path) -> None:
    parent = tmp_path
    for index in range(5):
        parent /= f"segment-{index}-" + ("a" * 96)
    parent.mkdir(parents=True)
    (parent / "vulnerable.py").write_text("eval(user_input)\n", encoding="utf-8")

    with pytest.raises(RepositoryScanError, match="path exceeds"):
        _scanner()._scan_paths_sync(  # noqa: SLF001 - path boundary test
            root=tmp_path,
            relative_paths=None,
            commit_sha="2" * 40,
        )


def test_local_scan_counts_empty_supported_sources_as_inspected(tmp_path: Path) -> None:
    (tmp_path / "empty.py").write_bytes(b"")
    (tmp_path / "safe.js").write_text("const safe = true;\n", encoding="utf-8")

    result = _scanner(max_files=2)._scan_paths_sync(  # noqa: SLF001 - file limit test
        root=tmp_path,
        relative_paths=None,
        commit_sha="3" * 40,
    )

    assert result.files_analyzed == 2
    assert result.bytes_analyzed == len(b"const safe = true;\n")


def test_local_scan_rejects_empty_supported_sources_over_file_limit(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"empty_{index}.py").write_bytes(b"")

    with pytest.raises(RepositoryScanError, match="file limit"):
        _scanner(max_files=2)._scan_paths_sync(  # noqa: SLF001 - file limit test
            root=tmp_path,
            relative_paths=None,
            commit_sha="4" * 40,
        )


def test_local_scan_rejects_supported_source_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "uninspectable.py"
    source.write_text("print('safe')\n", encoding="utf-8")
    original_stat = Path.stat

    def failing_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == source:
            raise OSError("private filesystem detail")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", failing_stat)

    with pytest.raises(RepositoryScanError, match="could not be inspected"):
        _scanner()._scan_paths_sync(  # noqa: SLF001 - failure boundary test
            root=tmp_path,
            relative_paths=None,
            commit_sha="d" * 40,
        )


def test_local_scan_rejects_supported_source_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "unreadable.py"
    source.write_text("print('safe')\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def failing_read(path: Path) -> bytes:
        if path == source:
            raise OSError("private filesystem detail")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", failing_read)

    with pytest.raises(RepositoryScanError, match="could not be read"):
        _scanner()._scan_paths_sync(  # noqa: SLF001 - failure boundary test
            root=tmp_path,
            relative_paths=None,
            commit_sha="e" * 40,
        )


@pytest.mark.parametrize(
    ("content", "safe_error"),
    [
        (b"\xff\xfe", "not valid UTF-8"),
        (b"print('safe')\x00\n", "contains binary content"),
    ],
)
def test_local_scan_rejects_non_text_supported_source(
    tmp_path: Path,
    content: bytes,
    safe_error: str,
) -> None:
    (tmp_path / "non_text.py").write_bytes(content)

    with pytest.raises(RepositoryScanError, match=safe_error):
        _scanner()._scan_paths_sync(  # noqa: SLF001 - failure boundary test
            root=tmp_path,
            relative_paths=None,
            commit_sha="f" * 40,
        )


def test_local_scan_skips_symlink_and_vendor_tree(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text('password = "a-very-secret-value"\n', encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(target)
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "third_party.py").write_text("eval(user_input)\n", encoding="utf-8")

    result = _scanner()._scan_paths_sync(  # noqa: SLF001
        root=tmp_path,
        relative_paths=None,
        commit_sha="c" * 40,
    )

    assert result.files_analyzed == 1
    assert {finding.file_path for finding in result.findings} == {"target.py"}


def test_diff_scan_excludes_path_outside_repository_boundary(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (tmp_path / "outside.py").write_text("eval(user_input)\n", encoding="utf-8")

    result = _scanner()._scan_paths_sync(  # noqa: SLF001 - containment boundary test
        root=repository,
        relative_paths=("../outside.py",),
        commit_sha="1" * 40,
    )

    assert result.files_analyzed == 0
    assert result.findings == ()


@pytest.mark.asyncio
async def test_pull_request_scan_deepens_to_real_merge_base_and_uses_head_sha(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    _git(source, "init", "--initial-branch", "main")
    _git(source, "config", "user.name", "Security Test")
    _git(source, "config", "user.email", "security@example.test")
    (source / "shared.py").write_text("print('safe')\n", encoding="utf-8")
    _git(source, "add", "shared.py")
    _git(source, "commit", "-m", "base")

    _git(source, "checkout", "-b", "feature")
    (source / "feature.py").write_text("eval(user_input)\n", encoding="utf-8")
    _git(source, "add", "feature.py")
    _git(source, "commit", "-m", "feature change")
    head_sha = _git(source, "rev-parse", "HEAD").strip()

    _git(source, "checkout", "main")
    (source / "main_only.py").write_text(
        'api_key = "main-branch-secret-not-in-the-pr"\n',
        encoding="utf-8",
    )
    _git(source, "add", "main_only.py")
    _git(source, "commit", "-m", "divergent main change")
    base_sha = _git(source, "rev-parse", "HEAD").strip()
    _git(tmp_path, "clone", "--bare", str(source), str(remote))

    result = await _scanner().scan_diff(
        repository=ValidatedRepositoryUrl(
            clone_url=str(remote),
            normalized_url="https://github.com/security/local-test",
            provider="github",
            owner="security",
            repository="local-test",
        ),
        base_ref=base_sha,
        head_ref=head_sha,
    )

    assert result.commit_sha == head_sha
    assert {finding.file_path for finding in result.findings} == {"feature.py"}

    full_head = await _scanner().scan_repository(
        repository=ValidatedRepositoryUrl(
            clone_url=str(remote),
            normalized_url="https://github.com/security/local-test",
            provider="github",
            owner="security",
            repository="local-test",
        ),
        ref=head_sha,
    )
    assert full_head.commit_sha == head_sha
    assert {finding.file_path for finding in full_head.findings} == {"feature.py"}


def _git(directory: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    return completed.stdout
