from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from enterprise_chat_platform.application.process_agent_run import (
    INPUT_POLICY_BLOCKED_FAILURE,
    INPUT_POLICY_ESCALATED_FAILURE,
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
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.governance import (
    PolicyAction,
    PolicyAssessment,
    PolicyFinding,
    PolicySeverity,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    AssistantProfileId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.prompting import (
    ENTERPRISE_CHAT_PROMPT_VERSION,
    PromptPlan,
    PromptRole,
    PromptTurn,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = TenantId.parse("99999999-9999-4999-8999-999999999999")
CONVERSATION_ID = ConversationId.parse("22222222-2222-4222-8222-222222222222")
RUN_ID = AgentRunId.parse("33333333-3333-4333-8333-333333333333")
INPUT_MESSAGE_ID = MessageId.parse("44444444-4444-4444-8444-444444444444")
OUTPUT_MESSAGE_ID = MessageId.parse("55555555-5555-4555-8555-555555555555")
EVENT_ID = EventId.parse("66666666-6666-4666-8666-666666666666")
SERVICE_AUTHOR_ID = UserId.parse("77777777-7777-4777-8777-777777777777")
LEASE_TOKEN = UUID("88888888-8888-4888-8888-888888888888")
STARTED_AT = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
ASSESSED_AT = STARTED_AT + timedelta(milliseconds=500)
FINISHED_AT = STARTED_AT + timedelta(seconds=1)
ASSISTANT_PROFILE = AssistantProfile(
    tenant_id=TENANT_ID,
    ref=AssistantProfileRef(
        id=AssistantProfileId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        version=3,
    ),
    prompt_template_version=ENTERPRISE_CHAT_PROMPT_VERSION,
    assistant_name="Acme Assistant",
    organization_name="Acme Corp",
    response_tone=PromptResponseTone.PROFESSIONAL,
    expertise=PromptExpertiseLevel.SENIOR_ENGINEER,
    communication_style=PromptCommunicationStyle.DIRECT_HELPFUL,
    context_character_limit=4_096,
    created_at=STARTED_AT - timedelta(days=1),
    created_by=SERVICE_AUTHOR_ID,
)
ALLOW_ASSESSMENT = PolicyAssessment(
    action=PolicyAction.ALLOW,
    risk_score=0,
    policy_version="conversation-input.v1",
)
BLOCK_ASSESSMENT = PolicyAssessment(
    action=PolicyAction.BLOCK,
    risk_score=0.99,
    policy_version="conversation-input.v1",
    findings=(
        PolicyFinding(
            code="security.blocked",
            severity=PolicySeverity.CRITICAL,
            safe_message="The request violates the input policy",
        ),
    ),
)
ESCALATE_ASSESSMENT = PolicyAssessment(
    action=PolicyAction.ESCALATE,
    risk_score=0.75,
    policy_version="conversation-input.v1",
    findings=(
        PolicyFinding(
            code="conversation.review",
            severity=PolicySeverity.HIGH,
            safe_message="The request requires human review",
        ),
    ),
)


def _job(
    *,
    attempt_count: int = 1,
    context_messages: tuple[PromptTurn, ...] = (),
    assistant_profile: AssistantProfile = ASSISTANT_PROFILE,
) -> ClaimedAgentRun:
    return ClaimedAgentRun(
        event_id=EVENT_ID,
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        agent_run_id=RUN_ID,
        input_message_id=INPUT_MESSAGE_ID,
        input_content="How does a transactional outbox work?",
        assistant_profile=assistant_profile,
        lease_token=LEASE_TOKEN,
        attempt_count=attempt_count,
        context_messages=context_messages,
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
        policy_applied: bool = True,
        authoritative_assessment: PolicyAssessment | None = None,
    ) -> None:
        self.job = job
        self.apply_result = apply_result
        self.policy_applied = policy_applied
        self.authoritative_assessment = authoritative_assessment
        self.claim_calls: list[tuple[datetime, timedelta]] = []
        self.policy_applications: list[tuple[ClaimedAgentRun, PolicyAssessment, datetime]] = []
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

    async def apply_input_policy(
        self,
        *,
        job: ClaimedAgentRun,
        assessment: PolicyAssessment,
        assessed_at: datetime,
    ) -> PolicyAssessment | None:
        self.policy_applications.append((job, assessment, assessed_at))
        if not self.policy_applied:
            return None
        return self.authoritative_assessment or assessment

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


class StaticInputPolicy:
    def __init__(self, assessment: PolicyAssessment = ALLOW_ASSESSMENT) -> None:
        self.assessment = assessment
        self.contents: list[str] = []

    @property
    def policy_version(self) -> str:
        return self.assessment.policy_version

    def evaluate(self, *, content: str) -> PolicyAssessment:
        self.contents.append(content)
        return self.assessment


class RecordingPromptBuilder:
    def __init__(self, plan: PromptPlan) -> None:
        self.plan = plan
        self.calls: list[tuple[AssistantProfile, tuple[PromptTurn, ...]]] = []

    def build(
        self,
        *,
        profile: AssistantProfile,
        context_messages: tuple[PromptTurn, ...],
    ) -> PromptPlan:
        self.calls.append((profile, context_messages))
        return self.plan


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


def test_provider_request_and_claimed_job_repr_hide_message_content() -> None:
    job = _job()
    request = ChatRequest(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        agent_run_id=RUN_ID,
        input_message_id=INPUT_MESSAGE_ID,
        input_content="private-current-message",
    )

    assert "How does a transactional outbox work?" not in repr(job)
    assert "private-current-message" not in repr(request)


def test_claimed_job_rejects_cross_tenant_assistant_profile() -> None:
    with pytest.raises(ValueError, match="profile must belong to the job tenant"):
        _job(
            assistant_profile=replace(
                ASSISTANT_PROFILE,
                tenant_id=OTHER_TENANT_ID,
            )
        )


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
    input_policy: StaticInputPolicy | None = None,
    prompt_builder: RecordingPromptBuilder | None = None,
    config: AgentRunWorkerConfig | None = None,
    random_value: float = 0.5,
) -> ProcessAgentRunWorker:
    return ProcessAgentRunWorker(
        store=store,
        provider=provider,
        input_policy=input_policy or StaticInputPolicy(),
        clock=clock,
        ids=FixedIds(),
        service_author_id=SERVICE_AUTHOR_ID,
        config=config or _config(),
        prompt_builder=prompt_builder,
        random_value=lambda: random_value,
    )


@pytest.mark.asyncio
async def test_success_calls_provider_between_claim_and_atomic_completion() -> None:
    store = FakeStore(job=_job())
    provider = StaticProvider(ChatCompletion(content="Use one database transaction."))
    worker = _worker(
        store=store,
        provider=provider,
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
    assert store.policy_applications == [(_job(), ALLOW_ASSESSMENT, ASSESSED_AT)]


@pytest.mark.asyncio
async def test_versioned_prompt_is_built_from_claimed_context_with_current_input_separate() -> None:
    context = (
        PromptTurn(
            role=PromptRole.USER,
            content="Earlier question",
            sequence=1,
        ),
        PromptTurn(
            role=PromptRole.ASSISTANT,
            content="Earlier answer",
            sequence=2,
        ),
    )
    job = _job(context_messages=context)
    prompt = PromptPlan(
        system_instruction="Follow the trusted policy.",
        context_messages=context,
        selected_message_count=2,
        context_character_count=sum(len(message.content) for message in context),
    )
    prompt_builder = RecordingPromptBuilder(prompt)
    store = FakeStore(job=job)
    provider = StaticProvider(ChatCompletion(content="Current answer"))
    worker = _worker(
        store=store,
        provider=provider,
        prompt_builder=prompt_builder,
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.SUCCEEDED
    assert prompt_builder.calls == [(ASSISTANT_PROFILE, context)]
    assert provider.requests == [
        ChatRequest(
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
            agent_run_id=RUN_ID,
            input_message_id=INPUT_MESSAGE_ID,
            input_content="How does a transactional outbox work?",
            prompt=prompt,
        )
    ]
    assert provider.requests[0].prompt is not None
    assert all(
        message.content != provider.requests[0].input_content
        for message in provider.requests[0].prompt.context_messages
    )


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
    ("assessment", "expected_result", "expected_failure"),
    [
        (
            BLOCK_ASSESSMENT,
            AgentRunWorkResult.POLICY_BLOCKED,
            INPUT_POLICY_BLOCKED_FAILURE,
        ),
        (
            ESCALATE_ASSESSMENT,
            AgentRunWorkResult.POLICY_ESCALATED,
            INPUT_POLICY_ESCALATED_FAILURE,
        ),
    ],
)
async def test_non_allow_policy_never_calls_provider(
    assessment: PolicyAssessment,
    expected_result: AgentRunWorkResult,
    expected_failure: AgentRunFailure,
) -> None:
    job = _job(attempt_count=99)
    store = FakeStore(job=job)
    provider = StaticProvider(ChatCompletion(content="must not be used"))
    input_policy = StaticInputPolicy(assessment)
    worker = _worker(
        store=store,
        provider=provider,
        input_policy=input_policy,
        clock=FakeClock(STARTED_AT, ASSESSED_AT),
        config=_config(max_attempts=1),
    )

    assert await worker.process_next() is expected_result
    assert input_policy.contents == [job.input_content]
    assert store.policy_applications == [(job, assessment, ASSESSED_AT)]
    assert provider.requests == []
    assert store.failed == []
    assert expected_failure.code in {"input_policy_blocked", "input_policy_escalated"}
    assert expected_failure.safe_message.isprintable()


@pytest.mark.asyncio
async def test_durable_policy_decision_wins_over_fresh_candidate() -> None:
    job = _job()
    store = FakeStore(
        job=job,
        authoritative_assessment=BLOCK_ASSESSMENT,
    )
    provider = StaticProvider(ChatCompletion(content="must not be used"))
    worker = _worker(
        store=store,
        provider=provider,
        input_policy=StaticInputPolicy(ALLOW_ASSESSMENT),
        clock=FakeClock(STARTED_AT, ASSESSED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.POLICY_BLOCKED
    assert store.policy_applications == [(job, ALLOW_ASSESSMENT, ASSESSED_AT)]
    assert provider.requests == []


@pytest.mark.asyncio
async def test_durable_allow_is_not_replaced_by_fresh_block() -> None:
    job = _job()
    store = FakeStore(
        job=job,
        authoritative_assessment=ALLOW_ASSESSMENT,
    )
    provider = StaticProvider(ChatCompletion(content="Previously authorized response"))
    worker = _worker(
        store=store,
        provider=provider,
        input_policy=StaticInputPolicy(BLOCK_ASSESSMENT),
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.SUCCEEDED
    assert store.policy_applications == [(job, BLOCK_ASSESSMENT, ASSESSED_AT)]
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_lost_lease_while_applying_policy_stops_before_provider() -> None:
    store = FakeStore(job=_job(), policy_applied=False)
    provider = StaticProvider(ChatCompletion(content="must not be used"))
    worker = _worker(
        store=store,
        provider=provider,
        clock=FakeClock(STARTED_AT, ASSESSED_AT),
    )

    assert await worker.process_next() is AgentRunWorkResult.LEASE_LOST
    assert provider.requests == []
    assert store.completed == []
    assert store.failed == []


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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
        clock=FakeClock(STARTED_AT, ASSESSED_AT, FINISHED_AT),
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
