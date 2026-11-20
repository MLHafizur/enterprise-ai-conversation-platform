"""Fail-closed GitHub webhook authentication and bounded event parsing."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    validate_git_ref,
)

MAX_GITHUB_WEBHOOK_BYTES = 1_048_576
_GITHUB_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_WEBHOOK_KEY_CONTEXT = b"enterprise-chat:github-webhook:v1\x00"


@dataclass(frozen=True, slots=True, kw_only=True)
class GitHubWebhookScanRequest:
    """Allowlisted fields extracted from a signed push or PR event."""

    event_type: str
    clone_url: str
    branch: str
    base_branch: str | None = None
    head_branch: str | None = None
    pull_request_number: int | None = None


def derive_repository_webhook_secret(
    *,
    master_secret: str,
    tenant_id: UUID,
    repository_id: UUID,
) -> str:
    """Derive one GitHub webhook key for exactly one tenant repository.

    The deployment secret remains server-only. GitHub receives only this derived
    key, so a key disclosed for one repository cannot authenticate requests for
    another repository or tenant.
    """
    if not master_secret:
        raise ValueError("Webhook master secret is required")
    binding = f"{tenant_id}:{repository_id}".encode("ascii")
    return hmac.new(
        master_secret.encode("utf-8"),
        _WEBHOOK_KEY_CONTEXT + binding,
        hashlib.sha256,
    ).hexdigest()


def verify_github_signature(*, body: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub's exact ``sha256=<hex>`` signature in constant time."""
    if (
        not body
        or len(body) > MAX_GITHUB_WEBHOOK_BYTES
        or not secret
        or not signature.startswith("sha256=")
        or len(signature) != 71
    ):
        return False
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature.lower())


def parse_github_scan_event(*, event_type: str, body: bytes) -> GitHubWebhookScanRequest | None:
    """Parse only push and opened/synchronized pull-request scan triggers."""
    if len(body) > MAX_GITHUB_WEBHOOK_BYTES:
        raise ValueError("Webhook payload is too large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Webhook payload is invalid") from None
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload is invalid")
    document = cast(dict[str, object], payload)

    if event_type == "push":
        raw_ref = document.get("ref")
        repository = _mapping(document.get("repository"), "repository")
        clone_url = _text(repository.get("clone_url"), "repository clone URL")
        if not isinstance(raw_ref, str) or not raw_ref.startswith("refs/heads/"):
            raise ValueError("Webhook push reference is invalid")
        # Validate the human-readable source ref, but queue the immutable commit.
        validate_git_ref(raw_ref.removeprefix("refs/heads/"))
        if document.get("deleted") is True or document.get("after") == "0" * 40:
            return None
        commit_sha = _commit_sha(document.get("after"), "push commit")
        return GitHubWebhookScanRequest(
            event_type="push",
            clone_url=clone_url,
            branch=commit_sha,
        )

    if event_type == "pull_request":
        action = document.get("action")
        if action not in {"opened", "reopened", "synchronize"}:
            return None
        pull_request = _mapping(document.get("pull_request"), "pull request")
        base = _mapping(pull_request.get("base"), "pull request base")
        head = _mapping(pull_request.get("head"), "pull request head")
        base_repository = _mapping(base.get("repo"), "pull request base repository")
        head_repository = _mapping(head.get("repo"), "pull request repository")
        base_clone_url = _text(base_repository.get("clone_url"), "base repository clone URL")
        head_clone_url = _text(head_repository.get("clone_url"), "head repository clone URL")
        # Fork pull requests need a separate, explicitly authorized fetch design.
        # Until then, fail closed instead of allowing a signed payload to switch
        # the repository boundary used by the scanner.
        if not hmac.compare_digest(base_clone_url, head_clone_url):
            raise ValueError("Cross-repository pull requests are not supported")
        validate_git_ref(_text(base.get("ref"), "base reference"))
        validate_git_ref(_text(head.get("ref"), "head reference"))
        base_sha = _commit_sha(base.get("sha"), "base commit")
        head_sha = _commit_sha(head.get("sha"), "head commit")
        number = document.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError("Pull request number is invalid")
        return GitHubWebhookScanRequest(
            event_type="pull_request",
            clone_url=base_clone_url,
            branch=head_sha,
            base_branch=base_sha,
            head_branch=head_sha,
            pull_request_number=number,
        )

    return None


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"Webhook {field_name} is invalid")
    return cast(dict[str, object], value)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"Webhook {field_name} is invalid")
    return value


def _commit_sha(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if _GITHUB_COMMIT_SHA.fullmatch(text) is None:
        raise ValueError(f"Webhook {field_name} is invalid")
    return text
