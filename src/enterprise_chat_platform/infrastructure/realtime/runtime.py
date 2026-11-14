"""Lifespan-owned Redis, presence, and notification subscriber resources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import FastAPI
from redis.asyncio import Redis
from starlette.types import Lifespan

from enterprise_chat_platform.infrastructure.realtime.connection_manager import (
    RealtimeConnectionManager,
)
from enterprise_chat_platform.infrastructure.realtime.subscriber import (
    RealtimeNotificationSubscriber,
    RedisSubscriberClient,
)
from enterprise_chat_platform.infrastructure.realtime.tenant_service import (
    RedisCoordinationClient,
    RedisTenantService,
)
from enterprise_chat_platform.settings import Settings

type RedisClientFactory = Callable[[Settings], Redis]


@dataclass(frozen=True, slots=True)
class RealtimeResources:
    """Process-scoped realtime resources; none contain durable chat state."""

    client: Redis
    tenant_service: RedisTenantService
    connection_manager: RealtimeConnectionManager


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        health_check_interval=30,
    )


def wrap_with_realtime_lifespan(
    database_lifespan: Lifespan[FastAPI],
    settings: Settings,
    *,
    redis_client_factory: RedisClientFactory = create_redis_client,
) -> Lifespan[FastAPI]:
    """Layer optional Redis resources around the existing database lifespan."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with database_lifespan(application):
            if not settings.realtime_enabled:
                yield
                return

            client = redis_client_factory(settings)
            manager = RealtimeConnectionManager()
            tenant_service = RedisTenantService(
                cast(RedisCoordinationClient, client),
                per_user_messages_per_minute=settings.per_user_messages_per_minute,
                connection_lease_seconds=settings.connection_lease_seconds,
            )
            subscriber = RealtimeNotificationSubscriber(
                client=cast(RedisSubscriberClient, client),
                manager=manager,
            )
            stop_event = asyncio.Event()
            subscriber_task: asyncio.Task[None] | None = None

            try:
                async with asyncio.timeout(2):
                    await cast(Awaitable[bool], client.ping())
                application.state.realtime_resources = RealtimeResources(
                    client=client,
                    tenant_service=tenant_service,
                    connection_manager=manager,
                )
                subscriber_task = asyncio.create_task(
                    subscriber.run(stop_event),
                    name="realtime-notification-subscriber",
                )
                yield
            finally:
                stop_event.set()
                if subscriber_task is not None:
                    subscriber_task.cancel()
                    try:
                        await subscriber_task
                    except asyncio.CancelledError:
                        pass
                await manager.close_all()
                await client.aclose()
                if hasattr(application.state, "realtime_resources"):
                    del application.state.realtime_resources

    return lifespan


def get_realtime_resources(application: FastAPI) -> RealtimeResources:
    resources: object = getattr(application.state, "realtime_resources", None)
    if not isinstance(resources, RealtimeResources):
        raise RuntimeError("Realtime resources are unavailable")
    return resources
