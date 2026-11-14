from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from enterprise_chat_platform.application.process_agent_run import (
    PROVIDER_INVALID_RESPONSE_FAILURE,
    PROVIDER_TERMINAL_FAILURE,
    PROVIDER_UNAVAILABLE_FAILURE,
    AgentRunWorkerConfig,
    AgentRunWorkResult,
    ChatCompletion,
    ChatRequest,
    ClaimedAgentRun,
    ProcessAgentRunWorker,
    TerminalChatProviderError,
    TransientChatProviderError,
    _bounded_backoff,
    _valid_completion_content,
)
from enterprise_chat_platform.domain.agent_run import AgentRunFailure
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
CONVERSATION_ID = ConversationId.parse("22222222-2222-4222-8222-222222222222")
RUN_ID = AgentRunId.parse("33333333-3333-4333-8333-333333333333")
INPUT_MESSAGE_ID = MessageId.parse("44444444-4444-4444-8444-444444444444")
OUTPUT_MESSAGE_ID = MessageId.parse("55555555-5555-4555-8555-555555555555")
EVENT_ID = EventId.parse("66666666-6666-4666-8666-666666666666")
SERVICE_AUTHOR_ID = UserId.parse("77777777-7777-4777-8777-777777777777")
LEASE_TOKEN = UUID("88888888-8888-4888-8888-888888888888")
STARTED_AT = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=1)


def _job(*, attempt_count: int = 1) -> ClaimedAgentRun:
    return ClaimedAgentRun(
        event_id=EVENT_ID,
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        agent_run_id=RUN_ID,
        input_message_id=INPUT_MESSAGE_ID,
        input_content="How does a transactional outbox work?",
        lease_token=LEASE_TOKEN,
        attempt_count=attempt_count,
    )


class FakeClock:
    def __init__(self, *values: datetime) -> None:
        self._values = deque(values)

    def now(self) -> datetime:
        return self._values.popleft()


class FixedIds:
    def new_message_id(self) -> MessageId:
        return OUTPUT_MESSAGE_ID


class FakeStore:
    def __init__(
        self,
        *,
        job: ClaimedAgentRun | None,
        apply_result: bool = True,
    ) -> None:
        self.job = job
        self.apply_result = apply_result
        self.claim_calls: list[tuple[datetime, timedelta]] = []
        self.completed: list[tuple[ClaimedAgentRun, MessageId, UserId, str, datetime]] = []
        self.rescheduled: list[tuple[ClaimedAgentRun, datetime, datetime]] = []
        self.failed: list[tuple[ClaimedAgentRun, AgentRunFailure, datetime]] = []

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedAgentRun | None:
        self.claim_calls.append((now, lease_duration))
        return self.job

    async def complete(
        self,
        *,
        job: ClaimedAgentRun,
        output_message_id: MessageId,
        service_author_id: UserId,
        content: str,
        completed_at: datetime,
    ) -> bool:
        self.completed.append((job, output_message_id, service_author_id, content, completed_at))
        return self.apply_result

    async def reschedule(
        self,
        *,
        job: ClaimedAgentRun,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool:
        self.rescheduled.append((job, available_at, attempted_at))
        return self.apply_result

    async def fail(
        self,
        *,
        job: ClaimedAgentRun,
        failure: AgentRunFailure,
        failed_at: datetime,
    ) -> bool:
        self.failed.append((job, failure, failed_at))
        return self.apply_result


class StaticProvider:
    def __init__(self, completion: ChatCompletion) -> None:
        self.completion = completion
        self.requests: list[ChatRequest] = []

    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        self.requests.append(request)
        return self.completion


class ErrorProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        del request
        raise self.error


class SlowProvider:
    async def complete(self, *, request: ChatRequest) -> ChatCompletion:
        del request
        await asyncio.sleep(60)
        raise AssertionError("Provider timeout did not cancel the call")


def _config(
    *,
    provider_timeout: timedelta = timedelta(seconds=5),
    max_attempts: int = 5,
) -> AgentRunWorkerConfig:
    return AgentRunWorkerConfig(
        lease_duration=timedelta(seconds=10),
        provider_timeout=provider_timeout,
        max_attempts=max_attempts,
        initial_backoff=timedelta(seconds=2),
        max_backoff=timedelta(seconds=20),
        jitter_fraction=0.25,
    )


def _worker(
    *,
    store: FakeStore,
    provider: StaticProvider | ErrorProvider | SlowProvider,
    clock: FakeClock,
    config: AgentRunWorkerConfig | None = None,
    random_value: float = 0.5,
) -> ProcessAgentRunWorker:
    return ProcessAgentRunWorker(
        store=store,
        provider=provider,
        clock=clock,
        ids=FixedIds(),
        service_author_id=SERVICE_AUTHOR_ID,
        config=config or _config(),
        random_value=lambda: random_value,
    )


@pytest.mark.asyncio
async def test_success_calls_provider_between_claim_and_atomic_completion() -> None:
    store = FakeStore(job=_job())
    provider = StaticProvider(ChatCompletion(content="Use one database transaction."))
    worker = _worker(
        store=store,
        provider=provider,
        clock=FakeClock(STARTED_AT, FINISHED_AT),
    )

    result = await worker.process_next()

    assert result is AgentRunWorkResult.SUCCEEDED
    assert provider.requests == [
        ChatRequest(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
            agent_run_id=RUN_ID,
            input_message_id=INPUT_MESSAGE_ID,
            input_content="How does a transactional outbox work?",
        )
    ]
    assert store.completed == [
        (
            _job(),
            OUTPUT_MESSAGE_ID,
            SERVICE_AUTHOR_ID,
            "Use one database transaction.",
            FINISHED_AT,
        )
    ]
    assert store.rescheduled == []
    assert store.failed == []


@pytest.mark.asyncio
async def test_no_due_job_does_not_call_provider() -> None:
    store = FakeStore(job=None)
    provider = StaticProvider(ChatCompletion(content="unused"))
    worker = _worker(
        store=store,
        provider=provider,
        clock=FakeClock(STARTED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.NO_WORK
    assert provider.requests == []
    assert store.completed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TransientChatProviderError("secret upstream details"),
        RuntimeError("secret unexpected details"),
    ],
)
async def test_transient_or_unknown_error_reschedules_without_storing_raw_error(
    error: Exception,
) -> None:
    store = FakeStore(job=_job(attempt_count=2))
    worker = _worker(
        store=store,
        provider=ErrorProvider(error),
        clock=FakeClock(STARTED_AT, FINISHED_AT),
        random_value=0.5,
    )

    result = await worker.process_next()

    assert result is AgentRunWorkResult.RESCHEDULED
    assert store.rescheduled == [
        (
            _job(attempt_count=2),
            FINISHED_AT + timedelta(seconds=4),
            FINISHED_AT,
        )
    ]
    assert store.failed == []


@pytest.mark.asyncio
async def test_provider_timeout_is_transient_and_releases_for_retry() -> None:
    store = FakeStore(job=_job())
    worker = _worker(
        store=store,
        provider=SlowProvider(),
        clock=FakeClock(STARTED_AT, FINISHED_AT),
        config=_config(provider_timeout=timedelta(milliseconds=1)),
    )

    assert await worker.process_next() is AgentRunWorkResult.RESCHEDULED
    assert len(store.rescheduled) == 1
    assert store.failed == []


@pytest.mark.asyncio
async def test_last_transient_attempt_fails_with_constant_safe_message() -> None:
    store = FakeStore(job=_job(attempt_count=3))
    worker = _worker(
        store=store,
        provider=ErrorProvider(TransientChatProviderError("raw API key=secret")),
        clock=FakeClock(STARTED_AT, FINISHED_AT),
        config=_config(max_attempts=3),
    )

    assert await worker.process_next() is AgentRunWorkResult.FAILED
    assert store.failed == [(_job(attempt_count=3), PROVIDER_UNAVAILABLE_FAILURE, FINISHED_AT)]
    assert "secret" not in store.failed[0][1].safe_message


@pytest.mark.asyncio
async def test_reclaimed_job_over_attempt_limit_never_calls_provider() -> None:
    job = _job(attempt_count=4)
    store = FakeStore(job=job)
    provider = StaticProvider(ChatCompletion(content="must not be used"))
    worker = _worker(
        store=store,
        provider=provider,
        clock=FakeClock(STARTED_AT, FINISHED_AT),
        config=_config(max_attempts=3),
    )

    assert await worker.process_next() is AgentRunWorkResult.FAILED
    assert provider.requests == []
    assert store.failed == [(job, PROVIDER_UNAVAILABLE_FAILURE, FINISHED_AT)]
    assert store.completed == []
    assert store.rescheduled == []


@pytest.mark.asyncio
async def test_terminal_provider_error_fails_without_retry() -> None:
    store = FakeStore(job=_job())
    worker = _worker(
        store=store,
        provider=ErrorProvider(TerminalChatProviderError("raw request details")),
        clock=FakeClock(STARTED_AT, FINISHED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.FAILED
    assert store.failed == [(_job(), PROVIDER_TERMINAL_FAILURE, FINISHED_AT)]
    assert store.rescheduled == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   ", "bad\x00content"])
async def test_invalid_provider_content_is_terminal(content: str) -> None:
    store = FakeStore(job=_job())
    worker = _worker(
        store=store,
        provider=StaticProvider(ChatCompletion(content=content)),
        clock=FakeClock(STARTED_AT, FINISHED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.FAILED
    assert store.failed == [(_job(), PROVIDER_INVALID_RESPONSE_FAILURE, FINISHED_AT)]


@pytest.mark.parametrize("content", [None, 7, object()])
def test_non_string_provider_content_is_invalid(content: object) -> None:
    assert _valid_completion_content(content) is False


@pytest.mark.asyncio
async def test_lost_lease_prevents_late_completion_from_reporting_success() -> None:
    store = FakeStore(job=_job(), apply_result=False)
    worker = _worker(
        store=store,
        provider=StaticProvider(ChatCompletion(content="late response")),
        clock=FakeClock(STARTED_AT, FINISHED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.LEASE_LOST


def test_backoff_is_exponential_jittered_and_bounded() -> None:
    config = _config()

    assert _bounded_backoff(
        attempt_count=1,
        config=config,
        random_value=0,
    ) == timedelta(seconds=1.5)
    assert _bounded_backoff(
        attempt_count=2,
        config=config,
        random_value=0.5,
    ) == timedelta(seconds=4)
    assert _bounded_backoff(
        attempt_count=20,
        config=config,
        random_value=1,
    ) == timedelta(seconds=20)


@pytest.mark.parametrize(
    "config",
    [
        AgentRunWorkerConfig(
            lease_duration=timedelta(seconds=2),
            provider_timeout=timedelta(seconds=1),
        ),
        AgentRunWorkerConfig(max_attempts=1),
    ],
)
def test_valid_worker_config(config: AgentRunWorkerConfig) -> None:
    assert config.max_attempts > 0


def test_config_rejects_lease_that_cannot_cover_provider_timeout() -> None:
    with pytest.raises(ValueError, match="Lease duration must exceed provider timeout"):
        AgentRunWorkerConfig(
            lease_duration=timedelta(seconds=5),
            provider_timeout=timedelta(seconds=5),
        )
