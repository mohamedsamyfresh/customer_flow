from __future__ import annotations

from pydantic import BaseModel, Field


class WebSocketTicketResponse(BaseModel):
    ticket: str = Field(description="Opaque, single-use, short-lived WebSocket authentication ticket")


class WebSocketTicketRequest(BaseModel):
    branch_id: str | None = Field(default=None, description="Optional branch ID to scope ticket authorization")
