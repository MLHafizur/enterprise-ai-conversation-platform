"""Authenticated notification-only WebSocket transport for the dashboard."""

from __future__ import annotations

import asyncio
import re
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from enterprise_chat_platform.api.queries import SqlAlchemyChatQueries
from enterprise_chat_platform.api.security import authenticate_access_token
from enterprise_chat_platform.application.tenant_catalog import find_tenant
from enterprise_chat_platform.infrastructure.realtime.protocol import (
    AuthenticateFrame,
    HeartbeatFrame,
    SubscribeFrame,
    UnsubscribeFrame,
    authenticated_client_frame_adapter,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import get_realtime_resources
from enterprise_chat_platform.runtime import get_application_resources

router = APIRouter(tags=["realtime"])

_CLIENT_CONNECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@router.websocket("/ws/{client_connection_id}")
async def realtime_websocket(websocket: WebSocket, client_connection_id: str) -> None:
    """Authenticate, authorize subscriptions, and deliver content-free signals."""
    application = cast(FastAPI, websocket.app)
    resources = get_application_resources(application)
    settings = resources.settings
    server_connection_id: str | None = None
    connection_reserved = False
    tenant = None

    await websocket.accept()

    if _CLIENT_CONNECTION_ID.fullmatch(client_connection_id) is None:
        await _close_with_error(websocket, code=4400, message="Invalid connection identifier")
        return

    origin = websocket.headers.get("origin", "").rstrip("/")
    allowed_origins = settings.allowed_websocket_origins
    if allowed_origins and "*" not in allowed_origins and origin not in allowed_origins:
        await _close_with_error(websocket, code=4403, message="Origin is not allowed")
        return

    try:
        realtime = get_realtime_resources(application)
    except RuntimeError:
        await _close_with_error(websocket, code=1013, message="Realtime service is unavailable")
        return

    try:
        try:
            async with asyncio.timeout(settings.websocket_auth_timeout_seconds):
                authentication_text = await _receive_limited_text(
                    websocket,
                    maximum_bytes=settings.websocket_max_message_bytes,
                )
            authentication = AuthenticateFrame.model_validate_json(authentication_text)
            principal = authenticate_access_token(
                settings=settings,
                token=authentication.token,
            )
            tenant = find_tenant(principal.actor.tenant_id)
            if tenant is None:
                raise ValueError("Unknown tenant")
        except (TimeoutError, ValidationError, HTTPException, ValueError):
            await _close_with_error(websocket, code=4401, message="Authentication failed")
            return

        server_connection_id = f"conn_{uuid4().hex}"
        reserved = await realtime.tenant_service.reserve_connection(
            tenant=tenant,
            connection_id=server_connection_id,
        )
        if not reserved:
            await _close_with_error(websocket, code=4429, message="Connection quota exceeded")
            return
        connection_reserved = True

        await realtime.connection_manager.register(
            connection_id=server_connection_id,
            websocket=websocket,
            actor=principal.actor,
            tenant=tenant,
        )
        snapshot = await realtime.tenant_service.quota_snapshot(tenant=tenant)
        if not await realtime.connection_manager.send(
            connection_id=server_connection_id,
            payload={
                "type": "authenticated",
                "connection_id": server_connection_id,
                "tenant_id": str(tenant.id),
                "active_connections": snapshot.current_connections,
            },
        ):
            return

        queries = SqlAlchemyChatQueries(resources.session_factory)
        while True:
            raw_frame = await _receive_limited_text(
                websocket,
                maximum_bytes=settings.websocket_max_message_bytes,
            )
            try:
                frame = authenticated_client_frame_adapter.validate_json(raw_frame)
            except ValidationError:
                if not await realtime.connection_manager.send(
                    connection_id=server_connection_id,
                    payload={"type": "error", "code": "invalid_frame", "message": "Invalid event"},
                ):
                    return
                continue

            if isinstance(frame, SubscribeFrame):
                conversation = await queries.get_conversation(
                    actor=principal.actor,
                    conversation_id=frame.conversation_id,
                )
                if conversation is None:
                    if not await realtime.connection_manager.send(
                        connection_id=server_connection_id,
                        payload={
                            "type": "error",
                            "code": "conversation_not_found",
                            "message": "Conversation was not found",
                        },
                    ):
                        return
                    continue
                await realtime.connection_manager.subscribe(
                    connection_id=server_connection_id,
                    conversation_id=frame.conversation_id,
                )
                if not await realtime.connection_manager.send(
                    connection_id=server_connection_id,
                    payload={
                        "type": "subscribed",
                        "conversation_id": str(frame.conversation_id),
                    },
                ):
                    return
            elif isinstance(frame, UnsubscribeFrame):
                await realtime.connection_manager.unsubscribe(
                    connection_id=server_connection_id,
                    conversation_id=frame.conversation_id,
                )
                if not await realtime.connection_manager.send(
                    connection_id=server_connection_id,
                    payload={
                        "type": "unsubscribed",
                        "conversation_id": str(frame.conversation_id),
                    },
                ):
                    return
            elif isinstance(frame, HeartbeatFrame):
                await realtime.tenant_service.refresh_connection(
                    tenant=tenant,
                    connection_id=server_connection_id,
                )
                snapshot = await realtime.tenant_service.quota_snapshot(tenant=tenant)
                if not await realtime.connection_manager.send(
                    connection_id=server_connection_id,
                    payload={
                        "type": "heartbeat_ack",
                        "active_connections": snapshot.current_connections,
                    },
                ):
                    return
    except WebSocketDisconnect:
        pass
    except (RedisError, RuntimeError, SQLAlchemyError):
        await _close_with_error(websocket, code=1013, message="Realtime service is unavailable")
    finally:
        if server_connection_id is not None and tenant is not None:
            await realtime.connection_manager.unregister(server_connection_id)
            if connection_reserved:
                try:
                    await realtime.tenant_service.release_connection(
                        tenant=tenant,
                        connection_id=server_connection_id,
                    )
                except Exception:
                    pass


async def _receive_limited_text(websocket: WebSocket, *, maximum_bytes: int) -> str:
    data = await websocket.receive_text()
    if len(data.encode("utf-8")) > maximum_bytes:
        await websocket.close(code=1009)
        raise WebSocketDisconnect(code=1009)
    return data


async def _close_with_error(websocket: WebSocket, *, code: int, message: str) -> None:
    try:
        await websocket.send_json({"type": "error", "message": message})
    except Exception:
        pass
    try:
        await websocket.close(code=code)
    except Exception:
        pass
