from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from enterprise_chat_platform.application.process_terminal_notifications import (
    ProcessTerminalNotificationRelay,
    TerminalNotificationRelayConfig,
    TerminalNotificationRelayResult,
    _bounded_backoff,
)
from enterprise_chat_platform.application.realtime_events import AgentRunTerminalEvent
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.infrastructure.persistence.notification_relay_store import (
    ClaimedTerminalNotification,
)

NOW = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)


def _event() -> AgentRunTerminalEvent:
    return AgentRunTerminalEvent(
        id=EventId.parse("11111111-1111-4111-8111-111111111111"),
        tenant_id=TenantId.parse("22222222-2222-4222-8222-222222222222"),
        conversation_id=ConversationId.parse("33333333-3333-4333-8333-333333333333"),
        agent_run_id=AgentRunId.parse("44444444-4444-4444-8444-444444444444"),
        status=AgentRunStatus.SUCCEEDED,
        output_message_id=MessageId.parse("55555555-5555-4555-8555-555555555555"),
        conversation_version=4,
        message_sequence=3,
        occurred_at=NOW,
    )


def _claim(*, attempt_count: int = 1) -> ClaimedTerminalNotification:
    return ClaimedTerminalNotification(
        event=_event(),
        lease_token=UUID("66666666-6666-4666-8666-666666666666"),
        attempt_count=attempt_count,
    )


class FakeClock:
    def __init__(self, *values: datetime) -> None:
        self._values = deque(values)

    def now(self) -> datetime:
        return self._values.popleft()


class FakeStore:
    def __init__(
        self,
        notification: ClaimedTerminalNotification | None,
        *,
        applied: bool = True,
    ) -> None:
        self.notification = notification
        self.applied = applied
        self.claims: list[tuple[datetime, timedelta]] = []
        self.published: list[tuple[ClaimedTerminalNotification, datetime]] = []
        self.retries: list[tuple[ClaimedTerminalNotification, datetime, datetime]] = []

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedTerminalNotification | None:
        self.claims.append((now, lease_duration))
        return self.notification

    async def mark_published(
        self,
        *,
        notification: ClaimedTerminalNotification,
        published_at: datetime,
    ) -> bool:
        self.published.append((notification, published_at))
        return self.applied

    async def reschedule(
        self,
        *,
        notification: ClaimedTerminalNotification,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool:
        self.retries.append((notification, available_at, attempted_at))
        return self.applied


class FakePublisher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.events: list[AgentRunTerminalEvent] = []

    async def publish(self, event: AgentRunTerminalEvent) -> None:
        self.events.append(event)
        if self.error is not None:
            raise self.error


def _relay(
    *,
    store: FakeStore,
    publisher: FakePublisher,
    clock: FakeClock,
) -> ProcessTerminalNotificationRelay:
    return ProcessTerminalNotificationRelay(
        store=store,
        publisher=publisher,
        clock=clock,
        config=TerminalNotificationRelayConfig(
            lease_duration=timedelta(seconds=30),
            initial_backoff=timedelta(seconds=2),
            max_backoff=timedelta(seconds=20),
        ),
    )


@pytest.mark.asyncio
async def test_success_publishes_then_marks_the_leased_event() -> None:
    notification = _claim()
    store = FakeStore(notification)
    publisher = FakePublisher()
    relay = _relay(
        store=store,
        publisher=publisher,
        clock=FakeClock(NOW, NOW + timedelta(seconds=1)),
    )

    result = await relay.process_next()

    assert result is TerminalNotificationRelayResult.PUBLISHED
    assert publisher.events == [notification.event]
    assert store.published == [(notification, NOW + timedelta(seconds=1))]
    assert store.retries == []


@pytest.mark.asyncio
async def test_no_due_event_does_not_publish() -> None:
    store = FakeStore(None)
    publisher = FakePublisher()
    relay = _relay(store=store, publisher=publisher, clock=FakeClock(NOW))

    assert await relay.process_next() is TerminalNotificationRelayResult.NO_WORK
    assert publisher.events == []


@pytest.mark.asyncio
async def test_publish_failure_reschedules_with_bounded_backoff() -> None:
    notification = _claim(attempt_count=3)
    store = FakeStore(notification)
    relay = _relay(
        store=store,
        publisher=FakePublisher(error=ConnectionError("redis unavailable")),
        clock=FakeClock(NOW, NOW + timedelta(seconds=1)),
    )

    result = await relay.process_next()

    assert result is TerminalNotificationRelayResult.RESCHEDULED
    assert store.published == []
    assert store.retries == [
        (
            notification,
            NOW + timedelta(seconds=9),
            NOW + timedelta(seconds=1),
        )
    ]


@pytest.mark.asyncio
async def test_fencing_reports_lost_lease_after_publish() -> None:
    relay = _relay(
        store=FakeStore(_claim(), applied=False),
        publisher=FakePublisher(),
        clock=FakeClock(NOW, NOW + timedelta(seconds=1)),
    )

    assert await relay.process_next() is TerminalNotificationRelayResult.LEASE_LOST


def test_backoff_is_exponential_and_capped() -> None:
    assert _bounded_backoff(
        attempt_count=1,
        initial=timedelta(seconds=2),
        maximum=timedelta(seconds=10),
    ) == timedelta(seconds=2)
    assert _bounded_backoff(
        attempt_count=9,
        initial=timedelta(seconds=2),
        maximum=timedelta(seconds=10),
    ) == timedelta(seconds=10)


def test_config_rejects_invalid_timings() -> None:
    with pytest.raises(ValueError, match="lease duration"):
        TerminalNotificationRelayConfig(lease_duration=timedelta(0))
    with pytest.raises(ValueError, match="maximum backoff"):
        TerminalNotificationRelayConfig(
            initial_backoff=timedelta(seconds=2),
            max_backoff=timedelta(seconds=1),
        )
