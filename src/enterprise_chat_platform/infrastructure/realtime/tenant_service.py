"""Redis-backed presence and quota coordination for the browser experience."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from enterprise_chat_platform.application.tenant_catalog import TenantDefinition
from enterprise_chat_platform.domain.identifiers import UserId

_MESSAGE_QUOTA_SCRIPT = """
-- enterprise_chat:reserve_message_quota:v2
if redis.call('EXISTS', KEYS[3]) == 1 then
  return {1, redis.call('GET', KEYS[1]) or '0', redis.call('GET', KEYS[2]) or '0', 'replay'}
end
local daily = tonumber(redis.call('GET', KEYS[1]) or '0')
local rate = tonumber(redis.call('GET', KEYS[2]) or '0')
if daily >= tonumber(ARGV[1]) then
  return {0, tostring(daily), tostring(rate), 'daily_limit_exceeded'}
end
if rate >= tonumber(ARGV[2]) then
  return {0, tostring(daily), tostring(rate), 'rate_limit_exceeded'}
end
daily = redis.call('INCR', KEYS[1])
rate = redis.call('INCR', KEYS[2])
if daily == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3])) end
if rate == 1 then redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4])) end
redis.call('SET', KEYS[3], '1', 'EX', tonumber(ARGV[3]), 'NX')
return {1, tostring(daily), tostring(rate), 'reserved'}
"""

_CONNECTION_RESERVATION_SCRIPT = """
-- enterprise_chat:reserve_connection:v1
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', tonumber(ARGV[1]))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[3]), ARGV[4])
return 1
"""

_QUOTA_REASONS = frozenset(
    {
        "replay",
        "reserved",
        "daily_limit_exceeded",
        "rate_limit_exceeded",
    }
)


class RedisCoordinationClient(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...

    async def get(self, name: str) -> object: ...

    async def zadd(
        self,
        name: str,
        mapping: dict[str, float],
        *,
        xx: bool = False,
    ) -> object: ...

    async def zcard(self, name: str) -> int: ...

    async def zrem(self, name: str, *values: str) -> object: ...

    async def zremrangebyscore(self, name: str, minimum: object, maximum: object) -> object: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageQuotaDecision:
    allowed: bool
    daily_messages_used: int
    rate_messages_used: int
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TenantQuotaSnapshot:
    current_connections: int
    max_connections: int
    daily_messages_used: int
    daily_message_limit: int
    context_size_limit: int


class RedisTenantService:
    """Coordinate only disposable counters and leases in Redis."""

    def __init__(
        self,
        client: RedisCoordinationClient,
        *,
        per_user_messages_per_minute: int,
        connection_lease_seconds: int,
    ) -> None:
        if per_user_messages_per_minute <= 0:
            raise ValueError("Per-user message limit must be positive")
        if connection_lease_seconds <= 0:
            raise ValueError("Connection lease must be positive")

        self._client = client
        self._per_user_messages_per_minute = per_user_messages_per_minute
        self._connection_lease_seconds = connection_lease_seconds

    async def reserve_message_quota(
        self,
        *,
        tenant: TenantDefinition,
        user_id: UserId,
        idempotency_key: str,
    ) -> MessageQuotaDecision:
        """Atomically reserve daily/rate capacity, replaying the same key for free."""
        ttl = _seconds_until_next_utc_day()
        key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        result = await self._client.eval(
            _MESSAGE_QUOTA_SCRIPT,
            3,
            _daily_message_key(tenant),
            f"chat:user:{tenant.id}:{user_id}:rate",
            f"chat:quota-receipt:{tenant.id}:{user_id}:{key_digest}",
            tenant.daily_message_limit,
            self._per_user_messages_per_minute,
            ttl,
            60,
        )
        values = _result_items(result, expected=4)
        allowed_integer = _nonnegative_integer(values[0])
        if allowed_integer not in {0, 1}:
            raise RuntimeError("Redis returned an invalid quota result")

        daily_messages_used = _nonnegative_integer(values[1])
        rate_messages_used = _nonnegative_integer(values[2])
        reason = values[3]
        if reason not in _QUOTA_REASONS or (allowed_integer == 1) != (
            reason in {"replay", "reserved"}
        ):
            raise RuntimeError("Redis returned an invalid quota result")

        return MessageQuotaDecision(
            allowed=bool(allowed_integer),
            daily_messages_used=daily_messages_used,
            rate_messages_used=rate_messages_used,
            reason=reason,
        )

    async def reserve_connection(
        self,
        *,
        tenant: TenantDefinition,
        connection_id: str,
    ) -> bool:
        now = time.time()
        result = await self._client.eval(
            _CONNECTION_RESERVATION_SCRIPT,
            1,
            _connection_key(tenant),
            now,
            tenant.max_connections,
            now + self._connection_lease_seconds,
            connection_id,
        )
        reservation = _nonnegative_integer(result)
        if reservation not in {0, 1}:
            raise RuntimeError("Redis returned an invalid connection reservation result")
        return bool(reservation)

    async def refresh_connection(
        self,
        *,
        tenant: TenantDefinition,
        connection_id: str,
    ) -> None:
        await self._client.zadd(
            _connection_key(tenant),
            {connection_id: time.time() + self._connection_lease_seconds},
            xx=True,
        )

    async def release_connection(
        self,
        *,
        tenant: TenantDefinition,
        connection_id: str,
    ) -> None:
        await self._client.zrem(_connection_key(tenant), connection_id)

    async def quota_snapshot(self, *, tenant: TenantDefinition) -> TenantQuotaSnapshot:
        connection_key = _connection_key(tenant)
        await self._client.zremrangebyscore(connection_key, "-inf", time.time())
        current_connections = await self._client.zcard(connection_key)
        raw_daily = await self._client.get(_daily_message_key(tenant))
        normalized_connections = _nonnegative_integer(current_connections)
        daily_messages = 0 if raw_daily is None else _nonnegative_integer(raw_daily)
        return TenantQuotaSnapshot(
            current_connections=normalized_connections,
            max_connections=tenant.max_connections,
            daily_messages_used=daily_messages,
            daily_message_limit=tenant.daily_message_limit,
            context_size_limit=tenant.context_size_limit,
        )


def _connection_key(tenant: TenantDefinition) -> str:
    return f"chat:tenant:{tenant.id}:connection-leases"


def _daily_message_key(tenant: TenantDefinition) -> str:
    return f"chat:tenant:{tenant.id}:messages:{datetime.now(UTC):%Y-%m-%d}"


def _seconds_until_next_utc_day() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(1, int((tomorrow - now).total_seconds()) + 60)


def _result_items(result: object, *, expected: int) -> tuple[str, ...]:
    if not isinstance(result, (list, tuple)) or len(result) != expected:
        raise RuntimeError("Redis returned an invalid quota result")

    normalized: list[str] = []
    for value in result:
        if isinstance(value, bytes):
            try:
                normalized.append(value.decode("utf-8"))
            except UnicodeDecodeError:
                raise RuntimeError("Redis returned an invalid quota result") from None
        elif isinstance(value, bool):
            raise RuntimeError("Redis returned an invalid quota result")
        elif isinstance(value, (str, int)):
            normalized.append(str(value))
        else:
            raise RuntimeError("Redis returned an invalid quota result")
    return tuple(normalized)


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (bytes, str, int)):
        raise RuntimeError("Redis returned an invalid integer result")

    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise RuntimeError("Redis returned an invalid integer result") from None

    if normalized < 0:
        raise RuntimeError("Redis returned an invalid integer result")
    return normalized
