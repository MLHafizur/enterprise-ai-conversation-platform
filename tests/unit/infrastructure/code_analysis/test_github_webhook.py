import hashlib
import hmac
import json
from uuid import UUID

import pytest

from enterprise_chat_platform.infrastructure.code_analysis.github_webhook import (
    derive_repository_webhook_secret,
    parse_github_scan_event,
    verify_github_signature,
)

SECRET = "a-long-local-webhook-secret"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40


def _signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_signature_verification_is_exact_and_constant_time_compatible() -> None:
    body = b'{"ref":"refs/heads/main"}'

    assert verify_github_signature(body=body, signature=_signature(body), secret=SECRET)
    assert not verify_github_signature(
        body=body + b" ",
        signature=_signature(body),
        secret=SECRET,
    )
    assert not verify_github_signature(body=body, signature="sha256=bad", secret=SECRET)


def test_derived_webhook_secret_is_stable_and_bound_to_tenant_repository() -> None:
    tenant_id = UUID("11111111-1111-4111-8111-111111111111")
    repository_id = UUID("22222222-2222-4222-8222-222222222222")

    first = derive_repository_webhook_secret(
        master_secret=SECRET,
        tenant_id=tenant_id,
        repository_id=repository_id,
    )
    repeated = derive_repository_webhook_secret(
        master_secret=SECRET,
        tenant_id=tenant_id,
        repository_id=repository_id,
    )
    other_repository = derive_repository_webhook_secret(
        master_secret=SECRET,
        tenant_id=tenant_id,
        repository_id=UUID("33333333-3333-4333-8333-333333333333"),
    )

    assert first == repeated
    assert len(first) == 64
    assert first != other_repository


def test_push_parser_extracts_only_bounded_scan_fields() -> None:
    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "after": HEAD_SHA,
            "repository": {"clone_url": "https://github.com/acme/app.git"},
            "ignored": {"secret": "never returned"},
        }
    ).encode()

    event = parse_github_scan_event(event_type="push", body=body)

    assert event is not None
    assert event.event_type == "push"
    assert event.branch == HEAD_SHA
    assert event.clone_url == "https://github.com/acme/app.git"


def test_deleted_push_is_ignored() -> None:
    body = json.dumps(
        {
            "ref": "refs/heads/old",
            "after": "0" * 40,
            "deleted": True,
            "repository": {"clone_url": "https://github.com/acme/app.git"},
        }
    ).encode()

    assert parse_github_scan_event(event_type="push", body=body) is None


def test_pull_request_parser_ignores_non_scan_actions() -> None:
    body = json.dumps({"action": "closed"}).encode()

    assert parse_github_scan_event(event_type="pull_request", body=body) is None


def test_pull_request_parser_rejects_option_like_ref() -> None:
    body = json.dumps(
        {
            "action": "synchronize",
            "number": 7,
            "pull_request": {
                "base": {
                    "ref": "main",
                    "sha": BASE_SHA,
                    "repo": {"clone_url": "https://github.com/acme/app.git"},
                },
                "head": {
                    "ref": "--upload-pack=evil",
                    "sha": HEAD_SHA,
                    "repo": {"clone_url": "https://github.com/acme/app.git"},
                },
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="invalid"):
        parse_github_scan_event(event_type="pull_request", body=body)


def test_pull_request_parser_queues_immutable_shas() -> None:
    body = json.dumps(
        {
            "action": "synchronize",
            "number": 7,
            "pull_request": {
                "base": {
                    "ref": "main",
                    "sha": BASE_SHA,
                    "repo": {"clone_url": "https://github.com/acme/app.git"},
                },
                "head": {
                    "ref": "feature/security",
                    "sha": HEAD_SHA,
                    "repo": {"clone_url": "https://github.com/acme/app.git"},
                },
            },
        }
    ).encode()

    event = parse_github_scan_event(event_type="pull_request", body=body)

    assert event is not None
    assert event.branch == HEAD_SHA
    assert event.base_branch == BASE_SHA
    assert event.head_branch == HEAD_SHA


def test_pull_request_parser_rejects_fork_repository() -> None:
    body = json.dumps(
        {
            "action": "opened",
            "number": 8,
            "pull_request": {
                "base": {
                    "ref": "main",
                    "sha": BASE_SHA,
                    "repo": {"clone_url": "https://github.com/acme/app.git"},
                },
                "head": {
                    "ref": "feature/security",
                    "sha": HEAD_SHA,
                    "repo": {"clone_url": "https://github.com/attacker/app.git"},
                },
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="not supported"):
        parse_github_scan_event(event_type="pull_request", body=body)


@pytest.mark.parametrize("commit_sha", ["main", "A" * 40, "a" * 39, "g" * 40])
def test_push_parser_rejects_noncanonical_commit_sha(commit_sha: str) -> None:
    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "after": commit_sha,
            "repository": {"clone_url": "https://github.com/acme/app.git"},
        }
    ).encode()

    with pytest.raises(ValueError, match="invalid"):
        parse_github_scan_event(event_type="push", body=body)
