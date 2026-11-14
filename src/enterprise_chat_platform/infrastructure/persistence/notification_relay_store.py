"""PostgreSQL leases for durable terminal-event publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from enterprise_chat_platform.application.realtime_events import (
    AGENT_RUN_TERMINAL_EVENT_TYPE,
    AGENT_RUN_TERMINAL_PAYLOAD_FIELDS,
    AGENT_RUN_TERMINAL_SCHEMA_VERSION,
    AgentRunTerminalEvent,
)
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    OutboxEventRecord,
)
from enterprise_chat_platform.infrastructure.persistence.models.outbox_event import (
    OUTBOX_AGGREGATE_TYPE,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedTerminalNotification:
    """One terminal event fenced by a short-lived publication lease."""

    event: AgentRunTerminalEvent
    lease_token: UUID
    attempt_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.event, AgentRunTerminalEvent):
            raise ValueError("Claimed notification requires a terminal event")
        if not isinstance(self.lease_token, UUID) or self.lease_token.int == 0:
            raise ValueError("Claimed notification requires a non-nil lease token")
        if (
            not isinstance(self.attempt_count, int)
            or isinstance(self.attempt_count, bool)
            or self.attempt_count <= 0
        ):
            raise ValueError("Claimed notification attempt count must be positive")


class SqlAlchemyNotificationRelayStore:
    """Claim, publish, and retry terminal notifications with lease fencing."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedTerminalNotification | None:
        claimed_at = normalize_utc(
            now,
            field_name="notification_claim.claimed_at",
        )
        if lease_duration <= timedelta(0):
            raise ValueError("Notification lease duration must be positive")

        async with self._session_factory() as session:
            async with session.begin():
                record = await _select_due_notification_for_update(
                    session=session,
                    now=claimed_at,
                )
                if record is None:
                    return None

                lease_token = uuid4()
                record.attempt_count += 1
                record.last_attempt_at = claimed_at
                record.lease_token = lease_token
                record.lease_expires_at = claimed_at + lease_duration

                return ClaimedTerminalNotification(
                    event=_terminal_event_from_record(record),
                    lease_token=lease_token,
                    attempt_count=record.attempt_count,
                )

    async def mark_published(
        self,
        *,
        notification: ClaimedTerminalNotification,
        published_at: datetime,
    ) -> bool:
        publication_time = normalize_utc(
            published_at,
            field_name="notification_publication.published_at",
        )

        async with self._session_factory() as session:
            async with session.begin():
                record = await _lock_owned_notification(
                    session=session,
                    notification=notification,
                    at=publication_time,
                )
                if record is None:
                    return False

                record.published_at = max(
                    publication_time,
                    record.last_attempt_at or record.occurred_at,
                )
                _clear_lease(record)
                return True

    async def reschedule(
        self,
        *,
        notification: ClaimedTerminalNotification,
        available_at: datetime,
        attempted_at: datetime,
    ) -> bool:
        attempt_time = normalize_utc(
            attempted_at,
            field_name="notification_retry.attempted_at",
        )
        next_available_at = normalize_utc(
            available_at,
            field_name="notification_retry.available_at",
        )
        if next_available_at < attempt_time:
            raise ValueError("Notification retry cannot precede the attempt")

        async with self._session_factory() as session:
            async with session.begin():
                record = await _lock_owned_notification(
                    session=session,
                    notification=notification,
                    at=attempt_time,
                )
                if record is None:
                    return False

                record.available_at = max(next_available_at, record.occurred_at)
                _clear_lease(record)
                return True


def _pending_terminal_notification_statement(
    *,
    now: datetime,
) -> Select[OutboxEventRecord]:
    """Build the relay's nonblocking PostgreSQL claim query."""
    return (
        select(OutboxEventRecord)
        .where(
            OutboxEventRecord.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE,
            OutboxEventRecord.published_at.is_(None),
            OutboxEventRecord.available_at <= now,
            or_(
                OutboxEventRecord.lease_token.is_(None),
                OutboxEventRecord.lease_expires_at <= now,
            ),
        )
        .order_by(
            OutboxEventRecord.available_at,
            OutboxEventRecord.occurred_at,
            OutboxEventRecord.tenant_id,
            OutboxEventRecord.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def _select_due_notification_for_update(
    *,
    session: AsyncSession,
    now: datetime,
) -> OutboxEventRecord | None:
    result = await session.execute(_pending_terminal_notification_statement(now=now))
    return result.scalar_one_or_none()


async def _lock_owned_notification(
    *,
    session: AsyncSession,
    notification: ClaimedTerminalNotification,
    at: datetime,
) -> OutboxEventRecord | None:
    statement = (
        select(OutboxEventRecord)
        .where(
            OutboxEventRecord.tenant_id == notification.event.tenant_id.value,
            OutboxEventRecord.id == notification.event.id.value,
            OutboxEventRecord.event_type == AGENT_RUN_TERMINAL_EVENT_TYPE,
        )
        .with_for_update()
    )
    result = await session.execute(statement)
    record = result.scalar_one_or_none()

    if record is None or record.published_at is not None:
        return None
    if record.lease_token != notification.lease_token:
        return None
    if record.lease_expires_at is None or record.lease_expires_at <= at:
        return None
    return record


def _terminal_event_from_record(record: OutboxEventRecord) -> AgentRunTerminalEvent:
    if record.aggregate_type != OUTBOX_AGGREGATE_TYPE:
        raise RuntimeError("Unsupported terminal-event aggregate type")
    if record.event_type != AGENT_RUN_TERMINAL_EVENT_TYPE:
        raise RuntimeError("Unsupported terminal-event type")
    if record.schema_version != AGENT_RUN_TERMINAL_SCHEMA_VERSION:
        raise RuntimeError("Unsupported terminal-event schema version")

    payload = record.payload
    if set(payload) != AGENT_RUN_TERMINAL_PAYLOAD_FIELDS:
        raise RuntimeError("Terminal-event payload has an unsupported shape")

    try:
        return AgentRunTerminalEvent(
            id=EventId(record.id),
            tenant_id=TenantId(record.tenant_id),
            conversation_id=ConversationId(record.aggregate_id),
            agent_run_id=AgentRunId(_required_payload_uuid(payload, "agent_run_id")),
            status=_payload_status(payload),
            output_message_id=_optional_message_id(payload),
            conversation_version=_payload_positive_int(payload, "conversation_version"),
            message_sequence=_payload_optional_positive_int(payload, "message_sequence"),
            occurred_at=record.occurred_at,
        )
    except DomainValidationError as error:
        raise RuntimeError("Terminal-event payload violates its contract") from error


def _payload_uuid(
    payload: dict[str, object],
    key: str,
    *,
    nullable: bool,
) -> UUID | None:
    value = payload.get(key)
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise RuntimeError(f"Terminal-event payload field {key} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeError(f"Terminal-event payload field {key} must be a UUID string") from error


def _required_payload_uuid(payload: dict[str, object], key: str) -> UUID:
    value = _payload_uuid(payload, key, nullable=False)
    if value is None:  # Defensive narrowing for the nullable parser contract.
        raise RuntimeError(f"Terminal-event payload field {key} must be a UUID string")
    return value


def _payload_status(payload: dict[str, object]) -> AgentRunStatus:
    value = payload.get("status")
    if not isinstance(value, str):
        raise RuntimeError("Terminal-event payload status is invalid")
    try:
        return AgentRunStatus(value)
    except ValueError as error:
        raise RuntimeError("Terminal-event payload status is invalid") from error


def _optional_message_id(payload: dict[str, object]) -> MessageId | None:
    value = _payload_uuid(payload, "output_message_id", nullable=True)
    return MessageId(value) if value is not None else None


def _payload_positive_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RuntimeError(f"Terminal-event payload field {key} must be positive")
    return value


def _payload_optional_positive_int(
    payload: dict[str, object],
    key: str,
) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    return _payload_positive_int(payload, key)


def _clear_lease(record: OutboxEventRecord) -> None:
    record.lease_token = None
    record.lease_expires_at = None
