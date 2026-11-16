"""Recoverable orchestration for one asynchronously queued agent run."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from enterprise_chat_platform.application.tenant_prompting import AgentPromptBuilder
from enterprise_chat_platform.domain.agent_run import AgentRunFailure
from enterprise_chat_platform.domain.chat import MAX_USER_MESSAGE_LENGTH
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.input_policy import InputPolicyEngine
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.domain.prompting import PromptPlan, PromptTurn

PROVIDER_TERMINAL_FAILURE = AgentRunFailure(
    code="provider_rejected_request",
    safe_message="The assistant could not generate a response for this message.",
)
PROVIDER_UNAVAILABLE_FAILURE = AgentRunFailure(
    code="provider_unavailable",
    safe_message="The assistant is temporarily unavailable. Please try again.",
)
PROVIDER_INVALID_RESPONSE_FAILURE = AgentRunFailure(
    code="provider_invalid_response",
    safe_message="The assistant returned an invalid response.",
)
INPUT_POLICY_BLOCKED_FAILURE = AgentRunFailure(
    code="input_policy_blocked",
    safe_message="The request was blocked by conversation policy.",
)
INPUT_POLICY_ESCALATED_FAILURE = AgentRunFailure(
    code="input_policy_escalated",
    safe_message="The request requires human review and was not sent to the assistant.",
)


class TransientChatProviderError(Exception):
    """A provider failure that may succeed when retried later."""


class TerminalChatProviderError(Exception):
    """A provider failure that must not be retried for this request."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatRequest:
    """Provider-neutral input for one agent execution."""

    tenant_id: TenantId
    conversation_id: ConversationId
    agent_run_id: AgentRunId
    input_message_id: MessageId
    input_content: str = field(repr=False)
    prompt: PromptPlan | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatCompletion:
    """Provider-neutral assistant output."""

    content: str


class ChatProvider(Protocol):
    """Generate an assistant response without owning persistence concerns."""

    async def complete(self, *, request: ChatRequest) -> ChatCompletion: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedAgentRun:
    """A leased outbox event and the immutable input needed by a provider."""

    event_id: EventId
    tenant_id: TenantId
    conversation_id: ConversationId
    agent_run_id: AgentRunId
    input_message_id: MessageId
    input_content: str = field(repr=False)
    lease_token: UUID
    attempt_count: int
    context_messages: tuple[PromptTurn, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.attempt_count <= 0:
            raise ValueError("Claimed attempt count must be positive")


class AgentRunJobStore(Protocol):
    """Short transactional operations used around the provider call."""

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedAgentRun | None: ...

    async def apply_input_policy(
        self,
        *,
        job: ClaimedAgentRun,
        assessment: PolicyAssessment,
        assessed_at: datetime,
    ) -> PolicyAssessment | None:
        """Persist the winning decision and atomically apply terminal actions.

        Return the durable first-write-wins assessment, or ``None`` when the
        caller no longer owns a live lease. BLOCK and ESCALATE decisions must
        terminalize the run and publish its queued event in this transaction.
        """
        ...

    async def complete(
        self,
        *,
        job: ClaimedAgentRun,
        output_message_id: MessageId,
        service_author_id: UserId,
        content: str,
        completed_at: datetime,
    ) -> bool: ...

    async def reschedule(
        self,
        *,
        job: ClaimedAgentRun,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool: ...

    async def fail(
        self,
        *,
        job: ClaimedAgentRun,
        failure: AgentRunFailure,
        failed_at: datetime,
    ) -> bool: ...


class AgentRunWorkerClock(Protocol):
    """Supply server-controlled time."""

    def now(self) -> datetime: ...


class AgentRunWorkerIds(Protocol):
    """Generate the identifier for one assistant output message."""

    def new_message_id(self) -> MessageId: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRunWorkerConfig:
    """Bound the provider call, lease, and retry schedule."""

    lease_duration: timedelta = timedelta(seconds=60)
    provider_timeout: timedelta = timedelta(seconds=30)
    max_attempts: int = 5
    initial_backoff: timedelta = timedelta(seconds=1)
    max_backoff: timedelta = timedelta(minutes=1)
    jitter_fraction: float = 0.2

    def __post_init__(self) -> None:
        if self.provider_timeout <= timedelta(0):
            raise ValueError("Provider timeout must be positive")
        if self.lease_duration <= self.provider_timeout:
            raise ValueError("Lease duration must exceed provider timeout")
        if self.max_attempts <= 0:
            raise ValueError("Maximum attempts must be positive")
        if self.initial_backoff <= timedelta(0):
            raise ValueError("Initial backoff must be positive")
        if self.max_backoff < self.initial_backoff:
            raise ValueError("Maximum backoff cannot be shorter than initial backoff")
        if not 0 <= self.jitter_fraction <= 1:
            raise ValueError("Jitter fraction must be between zero and one")


class AgentRunWorkResult(StrEnum):
    """Observable result of one worker poll."""

    NO_WORK = "no_work"
    SUCCEEDED = "succeeded"
    RESCHEDULED = "rescheduled"
    FAILED = "failed"
    POLICY_BLOCKED = "policy_blocked"
    POLICY_ESCALATED = "policy_escalated"
    LEASE_LOST = "lease_lost"


class ProcessAgentRunWorker:
    """Execute one leased run while keeping provider I/O outside DB transactions."""

    def __init__(
        self,
        *,
        store: AgentRunJobStore,
        provider: ChatProvider,
        input_policy: InputPolicyEngine,
        clock: AgentRunWorkerClock,
        ids: AgentRunWorkerIds,
        service_author_id: UserId,
        config: AgentRunWorkerConfig,
        prompt_builder: AgentPromptBuilder | None = None,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._store = store
        self._provider = provider
        self._input_policy = input_policy
        self._clock = clock
        self._ids = ids
        self._service_author_id = service_author_id
        self._config = config
        self._prompt_builder = prompt_builder
        self._random_value = random_value

    async def process_next(self) -> AgentRunWorkResult:
        """Claim and process at most one due outbox event."""
        claimed_at = normalize_utc(
            self._clock.now(),
            field_name="agent_run_worker.claimed_at",
        )
        job = await self._store.claim_next(
            now=claimed_at,
            lease_duration=self._config.lease_duration,
        )
        if job is None:
            return AgentRunWorkResult.NO_WORK

        # Evaluate every claimed run before any provider ceiling or paid call.
        # The store returns the already-persisted decision when this is a retry;
        # only that durable decision is authoritative.
        candidate_assessment = self._input_policy.evaluate(
            content=job.input_content,
        )
        assessed_at = normalize_utc(
            self._clock.now(),
            field_name="agent_run_worker.policy_assessed_at",
        )
        assessment = await self._store.apply_input_policy(
            job=job,
            assessment=candidate_assessment,
            assessed_at=assessed_at,
        )
        if assessment is None:
            return AgentRunWorkResult.LEASE_LOST
        if assessment.action is PolicyAction.BLOCK:
            return AgentRunWorkResult.POLICY_BLOCKED
        if assessment.action is PolicyAction.ESCALATE:
            return AgentRunWorkResult.POLICY_ESCALATED

        # A crash may occur after a provider call but before completion commits.
        # Reclaimed leases still count as attempts, so enforce the paid-call
        # ceiling before invoking the provider again.
        if job.attempt_count > self._config.max_attempts:
            return await self._fail(
                job=job,
                failure=PROVIDER_UNAVAILABLE_FAILURE,
            )

        prompt = (
            self._prompt_builder.build(
                tenant_id=job.tenant_id,
                context_messages=job.context_messages,
            )
            if self._prompt_builder is not None
            else None
        )
        request = ChatRequest(
            tenant_id=job.tenant_id,
            conversation_id=job.conversation_id,
            agent_run_id=job.agent_run_id,
            input_message_id=job.input_message_id,
            input_content=job.input_content,
            prompt=prompt,
        )

        try:
            async with asyncio.timeout(self._config.provider_timeout.total_seconds()):
                completion = await self._provider.complete(request=request)
        except TerminalChatProviderError:
            return await self._fail(
                job=job,
                failure=PROVIDER_TERMINAL_FAILURE,
            )
        except (TimeoutError, TransientChatProviderError):
            return await self._retry_or_fail(job=job)
        except Exception:
            # Unknown provider failures are treated as transient, but their raw
            # text is intentionally never persisted or returned to a client.
            return await self._retry_or_fail(job=job)

        if not isinstance(completion, ChatCompletion) or not _valid_completion_content(
            completion.content
        ):
            return await self._fail(
                job=job,
                failure=PROVIDER_INVALID_RESPONSE_FAILURE,
            )

        completed_at = normalize_utc(
            self._clock.now(),
            field_name="agent_run_worker.completed_at",
        )
        applied = await self._store.complete(
            job=job,
            output_message_id=self._ids.new_message_id(),
            service_author_id=self._service_author_id,
            content=completion.content,
            completed_at=completed_at,
        )
        return AgentRunWorkResult.SUCCEEDED if applied else AgentRunWorkResult.LEASE_LOST

    async def _retry_or_fail(
        self,
        *,
        job: ClaimedAgentRun,
    ) -> AgentRunWorkResult:
        if job.attempt_count >= self._config.max_attempts:
            return await self._fail(
                job=job,
                failure=PROVIDER_UNAVAILABLE_FAILURE,
            )

        attempted_at = normalize_utc(
            self._clock.now(),
            field_name="agent_run_worker.attempted_at",
        )
        delay = _bounded_backoff(
            attempt_count=job.attempt_count,
            config=self._config,
            random_value=self._random_value(),
        )
        applied = await self._store.reschedule(
            job=job,
            available_at=attempted_at + delay,
            attempted_at=attempted_at,
        )
        return AgentRunWorkResult.RESCHEDULED if applied else AgentRunWorkResult.LEASE_LOST

    async def _fail(
        self,
        *,
        job: ClaimedAgentRun,
        failure: AgentRunFailure,
    ) -> AgentRunWorkResult:
        failed_at = normalize_utc(
            self._clock.now(),
            field_name="agent_run_worker.failed_at",
        )
        applied = await self._store.fail(
            job=job,
            failure=failure,
            failed_at=failed_at,
        )
        return AgentRunWorkResult.FAILED if applied else AgentRunWorkResult.LEASE_LOST


def _valid_completion_content(content: object) -> bool:
    return (
        isinstance(content, str)
        and bool(content.strip())
        and "\x00" not in content
        and len(content) <= MAX_USER_MESSAGE_LENGTH
    )


def _bounded_backoff(
    *,
    attempt_count: int,
    config: AgentRunWorkerConfig,
    random_value: float,
) -> timedelta:
    if attempt_count <= 0:
        raise ValueError("Attempt count must be positive")
    if not 0 <= random_value <= 1:
        raise ValueError("Random value must be between zero and one")

    max_seconds = config.max_backoff.total_seconds()
    capped_seconds = config.initial_backoff.total_seconds()
    for _ in range(attempt_count - 1):
        capped_seconds = min(capped_seconds * 2, max_seconds)
        if capped_seconds == max_seconds:
            break
    jitter_multiplier = 1 + config.jitter_fraction * ((2 * random_value) - 1)
    jittered_seconds = min(
        capped_seconds * jitter_multiplier,
        max_seconds,
    )
    return timedelta(seconds=max(0, jittered_seconds))
