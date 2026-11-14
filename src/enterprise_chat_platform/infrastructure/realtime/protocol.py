"""Strict, content-free protocol for realtime completion notifications."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _StrictFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AuthenticateFrame(_StrictFrame):
    type: Literal["authenticate"]
    token: str = Field(min_length=16, max_length=8192)


class SubscribeFrame(_StrictFrame):
    type: Literal["subscribe"]
    conversation_id: UUID


class UnsubscribeFrame(_StrictFrame):
    type: Literal["unsubscribe"]
    conversation_id: UUID


class HeartbeatFrame(_StrictFrame):
    type: Literal["heartbeat"]


AuthenticatedClientFrame = Annotated[
    SubscribeFrame | UnsubscribeFrame | HeartbeatFrame,
    Field(discriminator="type"),
]

authenticated_client_frame_adapter = TypeAdapter(AuthenticatedClientFrame)


class TerminalNotification(_StrictFrame):
    """Internal event published through a tenant-specific Redis channel."""

    type: Literal["agent_run.terminal.v1"] = "agent_run.terminal.v1"
    event_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    agent_run_id: UUID
    status: Literal["succeeded", "failed", "cancelled"]
    output_message_id: UUID | None
    conversation_version: int = Field(ge=0)
    message_sequence: int | None = Field(default=None, gt=0)
    occurred_at: datetime

    def websocket_payload(self) -> dict[str, object]:
        """Exclude tenant routing data before sending to an authenticated browser."""
        return self.model_dump(mode="json", exclude={"tenant_id"})
