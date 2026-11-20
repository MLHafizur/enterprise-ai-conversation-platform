"""Strict repository URL and Git-reference validation.

Repository URLs are an outbound network boundary.  This module accepts only
explicit allowlisted HTTPS hosts and a conservative path shape; local paths,
SSH, embedded credentials, alternate ports, query strings, and fragments are
rejected before the worker starts Git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

_GIT_REF_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,253}[A-Za-z0-9])?")
_PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedRepositoryUrl:
    """Canonical public HTTPS Git repository location."""

    clone_url: str
    normalized_url: str
    provider: str
    owner: str
    repository: str


def validate_repository_url(
    raw_url: str,
    *,
    allowed_hosts: frozenset[str],
) -> ValidatedRepositoryUrl:
    """Validate one outbound clone target and return a canonical identity."""
    if not isinstance(raw_url, str) or not raw_url or raw_url != raw_url.strip():
        raise ValueError("Repository URL must be nonblank trimmed text")
    if len(raw_url) > 2_048:
        raise ValueError("Repository URL is too long")

    parsed = urlsplit(raw_url)
    hostname = parsed.hostname.lower().rstrip(".") if parsed.hostname is not None else None
    if parsed.scheme != "https" or hostname is None:
        raise ValueError("Repository URL must use HTTPS")
    if hostname not in allowed_hosts:
        raise ValueError("Repository host is not allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Repository URL cannot contain credentials")
    if parsed.port not in {None, 443}:
        raise ValueError("Repository URL cannot use a custom port")
    if parsed.query or parsed.fragment:
        raise ValueError("Repository URL cannot contain a query or fragment")

    decoded_path = unquote(parsed.path)
    if decoded_path != parsed.path or "\\" in decoded_path:
        raise ValueError("Repository URL path is invalid")
    segments = [segment for segment in decoded_path.split("/") if segment]
    if len(segments) != 2:
        raise ValueError("Repository URL must identify an owner and repository")

    owner, repository_with_suffix = segments
    repository = (
        repository_with_suffix[:-4]
        if repository_with_suffix.lower().endswith(".git")
        else repository_with_suffix
    )
    if not _PATH_SEGMENT_PATTERN.fullmatch(owner) or not _PATH_SEGMENT_PATTERN.fullmatch(
        repository
    ):
        raise ValueError("Repository owner or name is invalid")

    normalized_url = f"https://{hostname}/{owner}/{repository}"
    clone_url = f"{normalized_url}.git"
    provider = "github" if hostname == "github.com" else "git"
    return ValidatedRepositoryUrl(
        clone_url=clone_url,
        normalized_url=normalized_url,
        provider=provider,
        owner=owner,
        repository=repository,
    )


def validate_git_ref(raw_ref: str, *, field_name: str = "Git reference") -> str:
    """Reject ref names that could be interpreted as Git options or refspecs."""
    if (
        not isinstance(raw_ref, str)
        or not raw_ref
        or raw_ref != raw_ref.strip()
        or _GIT_REF_PATTERN.fullmatch(raw_ref) is None
        or raw_ref.startswith("-")
        or raw_ref.startswith("/")
        or raw_ref.endswith("/")
        or ".." in raw_ref
        or "//" in raw_ref
        or "@{" in raw_ref
        or raw_ref.endswith(".lock")
    ):
        raise ValueError(f"{field_name} is invalid")
    return raw_ref
