from __future__ import annotations

from dataclasses import dataclass

import pytest

from enterprise_chat_platform.application.tenant_catalog import DEMO_TENANTS
from enterprise_chat_platform.domain.identifiers import UserId
from enterprise_chat_platform.infrastructure.realtime.tenant_service import RedisTenantService

USER_ID = UserId.parse("22222222-2222-4222-8222-222222222222")
TENANT = DEMO_TENANTS[0]


@dataclass(frozen=True, slots=True)
class _EvalCall:
    script: str
    numkeys: int
    keys_and_args: tuple[object, ...]


class _FakeRedis:
    def __init__(self, *, eval_result: object = 1) -> None:
        self.eval_result = eval_result
        self.get_result: object = None
        self.zcard_result = 0
        self.eval_calls: list[_EvalCall] = []
        self.zadd_calls: list[tuple[str, dict[str, float], bool]] = []
        self.zrem_calls: list[tuple[str, tuple[str, ...]]] = []
        self.zremrange_calls: list[tuple[str, object, object]] = []

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object:
        self.eval_calls.append(
            _EvalCall(
                script=script,
                numkeys=numkeys,
                keys_and_args=keys_and_args,
            )
        )
        return self.eval_result

    async def get(self, name: str) -> object:
        return self.get_result

    async def zadd(
        self,
        name: str,
        mapping: dict[str, float],
        *,
        xx: bool = False,
    ) -> object:
        self.zadd_calls.append((name, mapping, xx))
        return 1

    async def zcard(self, name: str) -> int:
        return self.zcard_result

    async def zrem(self, name: str, *values: str) -> object:
        self.zrem_calls.append((name, values))
        return 1

    async def zremrangebyscore(
        self,
        name: str,
        minimum: object,
        maximum: object,
    ) -> object:
        self.zremrange_calls.append((name, minimum, maximum))
        return 0


def _service(client: _FakeRedis) -> RedisTenantService:
    return RedisTenantService(
        client,
        per_user_messages_per_minute=10,
        connection_lease_seconds=90,
    )


def test_tenant_service_requires_positive_runtime_limits() -> None:
    client = _FakeRedis()

    with pytest.raises(ValueError, match="message limit must be positive"):
        RedisTenantService(
            client,
            per_user_messages_per_minute=0,
            connection_lease_seconds=90,
        )

    with pytest.raises(ValueError, match="Connection lease must be positive"):
        RedisTenantService(
            client,
            per_user_messages_per_minute=10,
            connection_lease_seconds=0,
        )


@pytest.mark.asyncio
async def test_message_quota_parses_atomic_reply_and_hashes_idempotency_key() -> None:
    client = _FakeRedis(eval_result=[b"1", b"12", "3", b"reserved"])
    service = _service(client)

    decision = await service.reserve_message_quota(
        tenant=TENANT,
        user_id=USER_ID,
        idempotency_key="sensitive-idempotency-key",
    )

    assert decision.allowed is True
    assert decision.daily_messages_used == 12
    assert decision.rate_messages_used == 3
    assert decision.reason == "reserved"
    assert len(client.eval_calls) == 1
    call = client.eval_calls[0]
    assert call.numkeys == 3
    assert "reserve_message_quota:v2" in call.script
    assert "sensitive-idempotency-key" not in repr(call.keys_and_args)
    assert call.keys_and_args[3:5] == (TENANT.daily_message_limit, 10)


@pytest.mark.parametrize(
    "result",
    [
        None,
        [1, 2, 3],
        [2, 1, 1, "reserved"],
        [1, -1, 1, "reserved"],
        [1, 1, 1, "daily_limit_exceeded"],
        [0, 1, 1, "unexpected_reason"],
        [1, 1, 1, b"\xff"],
        [True, 1, 1, "reserved"],
    ],
)
@pytest.mark.asyncio
async def test_message_quota_rejects_malformed_redis_results(result: object) -> None:
    service = _service(_FakeRedis(eval_result=result))

    with pytest.raises(RuntimeError, match="Redis returned an invalid"):
        await service.reserve_message_quota(
            tenant=TENANT,
            user_id=USER_ID,
            idempotency_key="request-1",
        )


@pytest.mark.parametrize(("result", "expected"), [(1, True), (b"0", False)])
@pytest.mark.asyncio
async def test_connection_reservation_accepts_only_binary_decisions(
    result: object,
    expected: bool,
) -> None:
    client = _FakeRedis(eval_result=result)

    reserved = await _service(client).reserve_connection(
        tenant=TENANT,
        connection_id="conn_123",
    )

    assert reserved is expected
    assert client.eval_calls[0].numkeys == 1
    assert client.eval_calls[0].keys_and_args[-1] == "conn_123"


@pytest.mark.asyncio
async def test_connection_reservation_rejects_malformed_reply() -> None:
    service = _service(_FakeRedis(eval_result="2"))

    with pytest.raises(RuntimeError, match="connection reservation"):
        await service.reserve_connection(tenant=TENANT, connection_id="conn_123")


@pytest.mark.asyncio
async def test_connection_lifecycle_and_quota_snapshot_use_expiring_leases() -> None:
    client = _FakeRedis()
    client.zcard_result = 4
    client.get_result = b"27"
    service = _service(client)

    await service.refresh_connection(tenant=TENANT, connection_id="conn_123")
    snapshot = await service.quota_snapshot(tenant=TENANT)
    await service.release_connection(tenant=TENANT, connection_id="conn_123")

    assert client.zadd_calls[0][1].keys() == {"conn_123"}
    assert client.zadd_calls[0][2] is True
    assert client.zremrange_calls[0][1] == "-inf"
    assert client.zrem_calls[0][1] == ("conn_123",)
    assert snapshot.current_connections == 4
    assert snapshot.max_connections == TENANT.max_connections
    assert snapshot.daily_messages_used == 27
    assert snapshot.daily_message_limit == TENANT.daily_message_limit
    assert snapshot.context_size_limit == TENANT.context_size_limit


@pytest.mark.asyncio
async def test_quota_snapshot_rejects_corrupt_counter() -> None:
    client = _FakeRedis()
    client.get_result = "not-an-integer"

    with pytest.raises(RuntimeError, match="invalid integer"):
        await _service(client).quota_snapshot(tenant=TENANT)
