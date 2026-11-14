"""Reliable publication of durable agent-run terminal notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from enterprise_chat_platform.application.realtime_events import (
    AgentRunTerminalEvent,
)
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.persistence.notification_relay_store import (
    ClaimedTerminalNotification,
)


class TerminalNotificationStore(Protocol):
    """Lease and finish durable notification records."""

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedTerminalNotification | None: ...

    async def mark_published(
        self,
        *,
        notification: ClaimedTerminalNotification,
        published_at: datetime,
    ) -> bool: ...

    async def reschedule(
        self,
        *,
        notification: ClaimedTerminalNotification,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool: ...


class TerminalNotificationPublisher(Protocol):
    """Publish one content-free terminal notification."""

    async def publish(self, event: AgentRunTerminalEvent) -> None: ...


class TerminalNotificationClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalNotificationRelayConfig:
    lease_duration: timedelta = timedelta(seconds=30)
    initial_backoff: timedelta = timedelta(seconds=1)
    max_backoff: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0):
            raise ValueError("Notification lease duration must be positive")
        if self.initial_backoff <= timedelta(0):
            raise ValueError("Notification initial backoff must be positive")
        if self.max_backoff < self.initial_backoff:
            raise ValueError("Notification maximum backoff cannot be shorter than initial backoff")


class TerminalNotificationRelayResult(StrEnum):
    NO_WORK = "no_work"
    PUBLISHED = "published"
    RESCHEDULED = "rescheduled"
    LEASE_LOST = "lease_lost"


class ProcessTerminalNotificationRelay:
    """Publish one leased event with at-least-once delivery semantics."""

    def __init__(
        self,
        *,
        store: TerminalNotificationStore,
        publisher: TerminalNotificationPublisher,
        clock: TerminalNotificationClock,
        config: TerminalNotificationRelayConfig,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._clock = clock
        self._config = config

    async def process_next(self) -> TerminalNotificationRelayResult:
        claimed_at = normalize_utc(
            self._clock.now(),
            field_name="notification_relay.claimed_at",
        )
        notification = await self._store.claim_next(
            now=claimed_at,
            lease_duration=self._config.lease_duration,
        )
        if notification is None:
            return TerminalNotificationRelayResult.NO_WORK

        try:
            await self._publisher.publish(notification.event)
        except Exception:
            attempted_at = normalize_utc(
                self._clock.now(),
                field_name="notification_relay.attempted_at",
            )
            delay = _bounded_backoff(
                attempt_count=notification.attempt_count,
                initial=self._config.initial_backoff,
                maximum=self._config.max_backoff,
            )
            rescheduled = await self._store.reschedule(
                notification=notification,
                available_at=attempted_at + delay,
                attempted_at=attempted_at,
            )
            return (
                TerminalNotificationRelayResult.RESCHEDULED
                if rescheduled
                else TerminalNotificationRelayResult.LEASE_LOST
            )

        published_at = normalize_utc(
            self._clock.now(),
            field_name="notification_relay.published_at",
        )
        marked = await self._store.mark_published(
            notification=notification,
            published_at=published_at,
        )
        return (
            TerminalNotificationRelayResult.PUBLISHED
            if marked
            else TerminalNotificationRelayResult.LEASE_LOST
        )


def _bounded_backoff(
    *,
    attempt_count: int,
    initial: timedelta,
    maximum: timedelta,
) -> timedelta:
    if attempt_count <= 0:
        raise ValueError("Notification attempt count must be positive")
    seconds = initial.total_seconds()
    maximum_seconds = maximum.total_seconds()
    for _ in range(attempt_count - 1):
        seconds = min(seconds * 2, maximum_seconds)
        if seconds == maximum_seconds:
            break
    return timedelta(seconds=seconds)
