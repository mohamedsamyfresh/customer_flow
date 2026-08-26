from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.analytics.schemas import DashboardEvent
from app.analytics.service import AnalyticsService
from app.core.auth import authenticate_websocket
from app.core.db import AsyncSessionLocal
from app.core.permissions import Permission
from app.websocket.manager import manager

logger = logging.getLogger("websocket_router")
router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/dashboard")
async def websocket_dashboard(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    branch_id: str | None = Query(default=None),
    bucket: str = Query(default="1h"),
):
    """
    Real-time WebSocket dashboard endpoint.
    Requires authentication via token query param or Authorization header
    with 'analytics:read' permission.
    
    On connection, immediately streams current metrics state, then listens for
    ongoing real-time broadcast updates.
    """
    user = await authenticate_websocket(
        websocket=websocket,
        token=token,
        required_permission=Permission.ANALYTICS_READ,
    )
    if user is None:
        return

    await manager.connect(websocket, branch_id=branch_id)

    # Send initial state immediately upon connection
    try:
        async with AsyncSessionLocal() as db:
            analytics = AnalyticsService(db)
            metrics = await analytics.get_dashboard_metrics(
                branch_id=branch_id,
                bucket=bucket,
            )
            initial_event = DashboardEvent(
                type="dashboard_update",
                timestamp=datetime.now(timezone.utc).isoformat(),
                branch_id=branch_id,
                data=metrics,
            )
            await manager.send_personal_message(
                initial_event.model_dump(),
                websocket,
            )
    except Exception as e:
        logger.warning("Failed to send initial WebSocket state: %s", e)

    # Listen loop to detect disconnection and handle client pings
    try:
        while True:
            # Keep reading incoming frames (e.g. ping/pong, client messages)
            data = await websocket.receive_text()
            # Optional echo / query response if needed
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, branch_id=branch_id)
    except Exception as e:
        logger.debug("WebSocket connection terminated: %s", e)
        manager.disconnect(websocket, branch_id=branch_id)


@router.websocket("/ws/dashboard/{branch_id}")
async def websocket_dashboard_branch(
    websocket: WebSocket,
    branch_id: str,
    token: str | None = Query(default=None),
    bucket: str = Query(default="1h"),
):
    """
    Branch-specific WebSocket dashboard endpoint.
    Requires authentication with 'analytics:read' permission.
    """
    await websocket_dashboard(
        websocket=websocket,
        token=token,
        branch_id=branch_id,
        bucket=bucket,
    )
