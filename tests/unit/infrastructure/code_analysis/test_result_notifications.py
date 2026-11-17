from dataclasses import replace
from uuid import UUID

import pytest

from enterprise_chat_platform.domain.code_security import (
    CodeScanSource,
    GateDecision,
    SecurityReviewDecision,
)
from enterprise_chat_platform.domain.identifiers import (
    CodeRepositoryId,
    CodeScanId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    RepositoryScanResult,
)
from enterprise_chat_platform.infrastructure.code_analysis.result_notifications import (
    ExternalSecurityResultNotifier,
    GitHubSecurityStatusPublisher,
    NotificationDeliveryError,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    ClaimedCodeScan,
)


class _Response:
    status_code = 201


class _Client:
    def __init__(self, *, statuses: tuple[int, ...] = ()) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.statuses = list(statuses)

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, object],
        timeout: float,  # noqa: ASYNC109 - mirrors adapter protocol
    ) -> _Response:
        del headers, timeout
        self.calls.append((url, json))
        response = _Response()
        if self.statuses:
            response.status_code = self.statuses.pop(0)
        return response


class _EmailSender:
    def __init__(self) -> None:
        self.calls = 0

    async def send_security_gate_result(self, **_: object) -> None:
        self.calls += 1


def _job() -> ClaimedCodeScan:
    return ClaimedCodeScan(
        tenant_id=TenantId.parse("11111111-1111-4111-8111-111111111111"),
        scan_id=CodeScanId.parse("22222222-2222-4222-8222-222222222222"),
        repository_id=CodeRepositoryId.parse("33333333-3333-4333-8333-333333333333"),
        source=CodeScanSource.REPOSITORY,
        clone_url="https://github.com/acme/app.git",
        branch="main",
        base_branch=None,
        head_branch=None,
        lease_token=UUID("44444444-4444-4444-8444-444444444444"),
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_notifier_posts_safe_github_status_and_slack_message() -> None:
    client = _Client()
    notifier = ExternalSecurityResultNotifier(
        client=client,
        github_token="github-token",
        github_api_base_url="https://api.github.com",
        slack_webhook_url="https://hooks.slack.com/services/test/value",
    )

    await notifier.notify(
        job=_job(),
        result=RepositoryScanResult(
            commit_sha="a" * 40,
            files_analyzed=2,
            bytes_analyzed=200,
            findings=(),
        ),
        gate_decision=GateDecision.APPROVE,
    )

    assert len(client.calls) == 2
    assert client.calls[0][0].endswith("/repos/acme/app/statuses/" + "a" * 40)
    assert client.calls[0][1]["state"] == "success"
    assert client.calls[1][0].startswith("https://hooks.slack.com/")


@pytest.mark.asyncio
async def test_slack_notification_honors_configured_non_github_host() -> None:
    client = _Client()
    notifier = ExternalSecurityResultNotifier(
        client=client,
        github_token=None,
        github_api_base_url="https://api.github.com",
        slack_webhook_url="https://hooks.slack.com/services/test/value",
        allowed_git_hosts=frozenset({"gitlab.com"}),
    )

    await notifier.notify(
        job=replace(_job(), clone_url="https://gitlab.com/acme/app.git"),
        result=RepositoryScanResult(
            commit_sha="b" * 40,
            files_analyzed=1,
            bytes_analyzed=100,
            findings=(),
        ),
        gate_decision=GateDecision.APPROVE,
    )

    assert len(client.calls) == 1
    assert client.calls[0][0].startswith("https://hooks.slack.com/")


@pytest.mark.asyncio
async def test_rejected_github_output_does_not_suppress_slack_or_email() -> None:
    client = _Client(statuses=(500, 200))
    email = _EmailSender()
    notifier = ExternalSecurityResultNotifier(
        client=client,
        github_token="github-token",
        github_api_base_url="https://api.github.com",
        slack_webhook_url="https://hooks.slack.com/services/test/value",
        email_sender=email,
    )

    with pytest.raises(NotificationDeliveryError, match="outputs failed") as error:
        await notifier.notify(
            job=_job(),
            result=RepositoryScanResult(
                commit_sha="c" * 40,
                files_analyzed=1,
                bytes_analyzed=100,
                findings=(),
            ),
            gate_decision=GateDecision.BLOCK,
        )

    assert len(client.calls) == 2
    assert client.calls[1][0].startswith("https://hooks.slack.com/")
    assert email.calls == 1
    assert error.value.failed_channels == ("github",)


@pytest.mark.parametrize(
    ("decision", "state"),
    [
        (SecurityReviewDecision.APPROVED, "success"),
        (SecurityReviewDecision.BLOCKED, "failure"),
    ],
)
@pytest.mark.asyncio
async def test_github_review_status_replaces_pending_gate_with_final_decision(
    decision: SecurityReviewDecision,
    state: str,
) -> None:
    client = _Client()
    publisher = GitHubSecurityStatusPublisher(
        client=client,
        github_token="github-token",
        github_api_base_url="https://api.github.com",
    )

    await publisher.publish_review_decision(
        clone_url="https://github.com/acme/app.git",
        commit_sha="d" * 40,
        decision=decision,
    )

    assert len(client.calls) == 1
    url, payload = client.calls[0]
    assert url.endswith("/repos/acme/app/statuses/" + "d" * 40)
    assert payload == {
        "state": state,
        "context": "enterprise/security-gate",
        "description": f"Security review {decision.value}",
    }
