"""Process-local authenticated WebSocket registry and bounded fan-out."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import WebSocket

from enterprise_chat_platform.application.accept_user_turn import ActorContext
from enterprise_chat_platform.application.tenant_catalog import TenantDefinition
from enterprise_chat_platform.infrastructure.realtime.protocol import TerminalNotification


@dataclass(slots=True)
class _Connection:
    websocket: WebSocket
    actor: ActorContext
    tenant: TenantDefinition
    subscriptions: set[UUID] = field(default_factory=set)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RealtimeConnectionManager:
    """Own local sockets while Redis owns only expiring distributed presence."""

    def __init__(self, *, send_timeout_seconds: float = 2.0) -> None:
        self._connections: dict[str, _Connection] = {}
        self._lock = asyncio.Lock()
        self._send_timeout_seconds = send_timeout_seconds

    async def register(
        self,
        *,
        connection_id: str,
        websocket: WebSocket,
        actor: ActorContext,
        tenant: TenantDefinition,
    ) -> None:
        async with self._lock:
            if connection_id in self._connections:
                raise RuntimeError("Duplicate server connection identifier")
            self._connections[connection_id] = _Connection(
                websocket=websocket,
                actor=actor,
                tenant=tenant,
            )

    async def unregister(self, connection_id: str) -> None:
        async with self._lock:
            self._connections.pop(connection_id, None)

    async def subscribe(self, *, connection_id: str, conversation_id: UUID) -> None:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is not None:
                connection.subscriptions.add(conversation_id)

    async def unsubscribe(self, *, connection_id: str, conversation_id: UUID) -> None:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is not None:
                connection.subscriptions.discard(conversation_id)

    async def send(self, *, connection_id: str, payload: dict[str, object]) -> bool:
        async with self._lock:
            connection = self._connections.get(connection_id)
        if connection is None:
            return False
        return await self._send_or_remove(
            connection_id=connection_id,
            connection=connection,
            payload=payload,
        )

    async def broadcast(self, notification: TerminalNotification) -> None:
        """Send to authorized local subscribers without one slow socket blocking all."""
        async with self._lock:
            recipients = [
                (connection_id, connection)
                for connection_id, connection in self._connections.items()
                if connection.tenant.id.value == notification.tenant_id
                and notification.conversation_id in connection.subscriptions
            ]

        if not recipients:
            return

        await asyncio.gather(
            *(
                self._send_or_remove(
                    connection_id=connection_id,
                    connection=connection,
                    payload=notification.websocket_payload(),
                )
                for connection_id, connection in recipients
            )
        )

    async def close_all(self) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()

        await asyncio.gather(
            *(self._close_quietly(connection.websocket) for connection in connections)
        )

    async def _send_or_remove(
        self,
        *,
        connection_id: str,
        connection: _Connection,
        payload: dict[str, object],
    ) -> bool:
        try:
            async with connection.send_lock:
                async with asyncio.timeout(self._send_timeout_seconds):
                    await connection.websocket.send_json(payload)
        except Exception:
            await self.unregister(connection_id)
            await self._close_quietly(connection.websocket)
            return False
        return True

    @staticmethod
    async def _close_quietly(websocket: WebSocket) -> None:
        try:
            await websocket.close(code=1012)
        except Exception:
            pass
