"""Optional GitHub and Slack outputs for committed security gate results."""

from __future__ import annotations

from typing import Protocol

from enterprise_chat_platform.application.process_code_scan import (
    CodeScanResultNotifier,
)
from enterprise_chat_platform.domain.code_security import (
    GateDecision,
    SecurityReviewDecision,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    RepositoryScanResult,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    validate_repository_url,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    ClaimedCodeScan,
)


class AsyncNotificationResponse(Protocol):
    status_code: int


class AsyncNotificationClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, object],
        timeout: float,  # noqa: ASYNC109 - mirrors httpx
    ) -> AsyncNotificationResponse: ...


class SecurityEmailSender(Protocol):
    """Deployment-owned email port; source and finding snippets are excluded."""

    async def send_security_gate_result(
        self,
        *,
        repository: str,
        commit_sha: str,
        gate_decision: str,
        total_findings: int,
    ) -> None: ...


class NotificationDeliveryError(RuntimeError):
    """Content-free signal that one or more optional outputs failed."""

    def __init__(self, message: str, *, failed_channels: tuple[str, ...]) -> None:
        super().__init__(message)
        self.failed_channels = failed_channels


class SecurityReviewStatusPublisher(Protocol):
    """Publish the final human decision without exposing source or findings."""

    async def publish_review_decision(
        self,
        *,
        clone_url: str,
        commit_sha: str,
        decision: SecurityReviewDecision,
    ) -> None: ...


class GitHubSecurityStatusPublisher(SecurityReviewStatusPublisher):
    """Publish content-free security gate states to one GitHub commit."""

    def __init__(
        self,
        *,
        client: AsyncNotificationClient,
        github_token: str,
        github_api_base_url: str,
        allowed_git_hosts: frozenset[str] = frozenset({"github.com"}),
        timeout_seconds: float = 5,
    ) -> None:
        token = github_token.strip()
        if not token:
            raise ValueError("GitHub token cannot be blank")
        if not github_api_base_url.startswith("https://"):
            raise ValueError("GitHub API base URL must use HTTPS")
        if not allowed_git_hosts:
            raise ValueError("Notification repository host allowlist cannot be empty")
        self._client = client
        self._github_token = token
        self._github_api_base_url = github_api_base_url.rstrip("/")
        self._allowed_git_hosts = allowed_git_hosts
        self._timeout_seconds = timeout_seconds

    async def publish_gate_result(
        self,
        *,
        clone_url: str,
        commit_sha: str,
        gate_decision: GateDecision,
        total_findings: int,
    ) -> None:
        await self._publish(
            clone_url=clone_url,
            commit_sha=commit_sha,
            state=_github_state(gate_decision),
            description=_description(
                gate_decision=gate_decision,
                total_findings=total_findings,
            ),
        )

    async def publish_review_decision(
        self,
        *,
        clone_url: str,
        commit_sha: str,
        decision: SecurityReviewDecision,
    ) -> None:
        state = {
            SecurityReviewDecision.APPROVED: "success",
            SecurityReviewDecision.BLOCKED: "failure",
        }[decision]
        await self._publish(
            clone_url=clone_url,
            commit_sha=commit_sha,
            state=state,
            description=f"Security review {decision.value}",
        )

    async def _publish(
        self,
        *,
        clone_url: str,
        commit_sha: str,
        state: str,
        description: str,
    ) -> None:
        repository = validate_repository_url(
            clone_url,
            allowed_hosts=self._allowed_git_hosts,
        )
        if repository.provider != "github":
            return
        if len(commit_sha) != 40 or any(
            character not in "0123456789abcdef" for character in commit_sha
        ):
            raise ValueError("Commit SHA must be 40 lowercase hexadecimal characters")
        response = await self._client.post(
            (
                f"{self._github_api_base_url}/repos/{repository.owner}/"
                f"{repository.repository}/statuses/{commit_sha}"
            ),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "state": state,
                "context": "enterprise/security-gate",
                "description": description[:140],
            },
            timeout=self._timeout_seconds,
        )
        if not 200 <= response.status_code < 300:
            raise NotificationDeliveryError(
                "Security result output was rejected",
                failed_channels=("github",),
            )


class ExternalSecurityResultNotifier(CodeScanResultNotifier):
    """Best-effort outputs; durable PostgreSQL state remains authoritative."""

    def __init__(
        self,
        *,
        client: AsyncNotificationClient,
        github_token: str | None,
        github_api_base_url: str,
        slack_webhook_url: str | None,
        allowed_git_hosts: frozenset[str] = frozenset({"github.com"}),
        email_sender: SecurityEmailSender | None = None,
        timeout_seconds: float = 5,
    ) -> None:
        if not github_api_base_url.startswith("https://"):
            raise ValueError("GitHub API base URL must use HTTPS")
        if slack_webhook_url and not slack_webhook_url.startswith("https://hooks.slack.com/"):
            raise ValueError("Slack webhook must use the official HTTPS endpoint")
        if not allowed_git_hosts:
            raise ValueError("Notification repository host allowlist cannot be empty")
        self._client = client
        self._github_token = github_token.strip() if github_token else None
        self._github_api_base_url = github_api_base_url.rstrip("/")
        self._slack_webhook_url = slack_webhook_url
        self._allowed_git_hosts = allowed_git_hosts
        self._email_sender = email_sender
        self._timeout_seconds = timeout_seconds
        self._github_status_publisher = (
            GitHubSecurityStatusPublisher(
                client=client,
                github_token=self._github_token,
                github_api_base_url=github_api_base_url,
                allowed_git_hosts=allowed_git_hosts,
                timeout_seconds=timeout_seconds,
            )
            if self._github_token is not None
            else None
        )

    async def notify(
        self,
        *,
        job: ClaimedCodeScan,
        result: RepositoryScanResult,
        gate_decision: GateDecision,
    ) -> None:
        repository = validate_repository_url(
            job.clone_url,
            allowed_hosts=self._allowed_git_hosts,
        )
        failures: list[str] = []
        if self._github_status_publisher is not None and repository.provider == "github":
            try:
                await self._github_status_publisher.publish_gate_result(
                    clone_url=job.clone_url,
                    commit_sha=result.commit_sha,
                    gate_decision=gate_decision,
                    total_findings=len(result.findings),
                )
            except Exception:
                failures.append("github")
        if self._slack_webhook_url:
            try:
                await self._post(
                    self._slack_webhook_url,
                    json={
                        "text": (
                            f"Security gate `{gate_decision.value}` for "
                            f"{repository.owner}/{repository.repository} at "
                            f"{result.commit_sha[:12]}: {len(result.findings)} finding(s)."
                        )
                    },
                )
            except Exception:
                failures.append("slack")
        if self._email_sender is not None:
            try:
                await self._email_sender.send_security_gate_result(
                    repository=f"{repository.owner}/{repository.repository}",
                    commit_sha=result.commit_sha,
                    gate_decision=gate_decision.value,
                    total_findings=len(result.findings),
                )
            except Exception:
                failures.append("email")
        if failures:
            raise NotificationDeliveryError(
                "One or more security result outputs failed",
                failed_channels=tuple(failures),
            )

    async def _post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> None:
        response = await self._client.post(
            url,
            headers=headers,
            json=json,
            timeout=self._timeout_seconds,
        )
        if not 200 <= response.status_code < 300:
            raise NotificationDeliveryError(
                "Security result output was rejected",
                failed_channels=(),
            )


def _github_state(gate_decision: GateDecision) -> str:
    return {
        GateDecision.APPROVE: "success",
        GateDecision.REVIEW_REQUIRED: "pending",
        GateDecision.BLOCK: "failure",
    }[gate_decision]


def _description(*, gate_decision: GateDecision, total_findings: int) -> str:
    label = {
        GateDecision.APPROVE: "approved",
        GateDecision.REVIEW_REQUIRED: "requires review",
        GateDecision.BLOCK: "blocked",
    }[gate_decision]
    return f"Security gate {label}: {total_findings} finding(s)"[:140]
