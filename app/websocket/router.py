from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from app.analytics.schemas import DashboardEvent
from app.analytics.service import AnalyticsService
from app.core.auth import CurrentUser, auth
from app.core.db import AsyncSessionLocal
from app.core.permissions import Permission
from app.core.redis import get_redis_client
from app.websocket.auth import authenticate_websocket_handshake
from app.websocket.publisher import get_channel_name
from app.websocket.schemas import WebSocketTicketRequest, WebSocketTicketResponse
from app.websocket.tickets import ticket_manager

logger = logging.getLogger("websocket_router")
router = APIRouter(tags=["WebSocket & Real-Time Streaming"])


# ==========================================================
# PHASE 3: AUTHENTICATED TICKET CREATION ENDPOINTS
# ==========================================================
@router.post(
    "/ws/{branch_id}/ticket",
    response_model=WebSocketTicketResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth.require_permission(Permission.ANALYTICS_READ))],
    summary="Mint WebSocket streaming ticket",
)
async def create_websocket_ticket(
    user: CurrentUser,
    branch_id
) -> WebSocketTicketResponse:
    """
    Step 1 of WebSocket connection flow:
    Authenticates the client using existing JWT/JWKS infrastructure,
    verifies permissions, validates resource eligibility,
    and returns a short-lived, single-use, cryptographically secure opaque ticket.
    """
    # Resolve target resource / branch identifier
    target_resource = branch_id 
    target_resource = target_resource.strip()

    # Validate resource eligibility
    if not target_resource or len(target_resource) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid resource or branch identifier",
        )

    ticket = await ticket_manager.create_ticket(
        resource_id=target_resource,
        user_id=getattr(user, "sub", None),
        metadata={"roles": getattr(user, "roles", [])},
    )

    return WebSocketTicketResponse(ticket=ticket)


# ==========================================================
# PHASE 4 & 7 & 10: WEBSOCKET STREAMING ENDPOINTS
# ==========================================================

async def _handle_websocket_stream(
    websocket: WebSocket,
    ticket: str | None,
    branch_id: str | None = None,
    bucket: str = "1h",
) -> None:
    """
    Core WebSocket stream handler enforcing full validation before accept():
      1. Origin validation
      2. Ticket existence check
      3. Atomic single-use GETDEL redemption
      4. Resource match verification
      5. Resource eligibility check
      6. Initial snapshot query (DB session closed immediately after)
      7. websocket.accept()
      8. Send initial snapshot
      9. Long-running Redis Pub/Sub stream without open DB connection
    """
    target_resource = branch_id.strip() if branch_id else "global"

    # Step 1-5: Validate origin, ticket, single-use atomic GETDEL, resource match
    ticket_payload = await authenticate_websocket_handshake(
        websocket=websocket,
        ticket=ticket,
        expected_resource_id=target_resource,
    )
    if ticket_payload is None:
        # Rejection with WS_1008_POLICY_VIOLATION already performed
        return

    # Step 6-8: Load initial snapshot in short-lived DB session and close it immediately
    target_branch = target_resource if target_resource not in ("global", "dashboard", "_global") else None
    try:
        async with AsyncSessionLocal() as db:
            analytics = AnalyticsService(db)
            metrics = await analytics.get_dashboard_metrics(
                branch_id=target_branch,
                bucket=bucket,
            )
            snapshot_event = DashboardEvent(
                type="dashboard_snapshot",
                timestamp=datetime.now(timezone.utc).isoformat(),
                branch_id=target_branch,
                data=metrics,
            )
    except Exception as snapshot_err:
        logger.error("Failed to load initial snapshot from DB: %s", snapshot_err)
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Policy Violation: Unable to load initial resource state",
        )
        return

    # Step 7: Only after all validations and DB session release, accept the WebSocket
    await websocket.accept()
    logger.info("WebSocket accepted (resource=%s)", target_resource)

    # Step 8: Send initial snapshot immediately upon connection
    try:
        await websocket.send_text(snapshot_event.model_dump_json())
    except Exception as send_err:
        logger.warning("Failed to send initial snapshot to WebSocket client: %s", send_err)
        return

    # Step 9: Long-running Redis Pub/Sub streaming loop
    channel_name = get_channel_name(target_branch)
    redis_client = get_redis_client()
    pubsub = redis_client.pubsub()

    try:
        await pubsub.subscribe(channel_name)
        logger.debug("Subscribed to Redis channel '%s' for WebSocket client", channel_name)
    except Exception as sub_err:
        logger.error("Failed to subscribe to Redis channel '%s': %s", channel_name, sub_err)
        try:
            await websocket.close(
                code=status.WS_1013_TRY_AGAIN_LATER,
                reason="Try Again Later: Real-time event broker connection failed",
            )
        except Exception:
            pass
        return

    # Concurrent task for client incoming frames (pings, disconnect detection)
    client_disconnect_event = asyncio.Event()

    async def _client_reader() -> None:
        try:
            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")
        except (WebSocketDisconnect, asyncio.CancelledError):
            client_disconnect_event.set()
        except Exception as read_exc:
            logger.debug("WebSocket client read error: %s", read_exc)
            client_disconnect_event.set()

    reader_task = asyncio.create_task(_client_reader())

    try:
        while not client_disconnect_event.is_set():
            # Wait for pubsub message with timeout to allow periodic disconnect checks
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=1.5,
                )
            except asyncio.TimeoutError:
                continue

            if message is not None and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                await websocket.send_text(data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected normally by client (resource=%s)", target_resource)
    except (aioredis.RedisError, aioredis.ConnectionError) as redis_err:
        logger.error("Redis Pub/Sub failure during active streaming: %s", redis_err)
        try:
            await websocket.close(
                code=status.WS_1013_TRY_AGAIN_LATER,
                reason="Try Again Later: Real-time event broker failure",
            )
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Unexpected error in WebSocket stream loop: %s", exc)
        try:
            await websocket.close(
                code=status.WS_1013_TRY_AGAIN_LATER,
                reason="Try Again Later: Stream connection error",
            )
        except Exception:
            pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass

        try:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()
        except Exception as cleanup_err:
            logger.debug("Error cleaning up pubsub subscriber: %s", cleanup_err)

        logger.info("WebSocket streaming finalized (resource=%s)", target_resource)


@router.websocket("/ws")
async def websocket_dashboard(
    websocket: WebSocket,
    ticket: str | None = Query(default=None),
    branch_id: str | None = Query(default=None),
    bucket: str = Query(default="1h"),
):
    """
    Secure WebSocket dashboard endpoint (global or optional branch_id query param).
    Requires a valid single-use ticket minted via POST /api/v1/dashboard/ws/ticket.
    """
    await _handle_websocket_stream(
        websocket=websocket,
        ticket=ticket,
        branch_id=branch_id,
        bucket=bucket,
    )


@router.websocket("/ws/{branch_id}")
async def websocket_dashboard_branch(
    websocket: WebSocket,
    branch_id: str,
    ticket: str | None = Query(default=None),
    bucket: str = Query(default="1h"),
):
    """
    Branch-specific WebSocket dashboard endpoint.
    Requires a valid single-use ticket minted for the specified branch_id.
    """
    await _handle_websocket_stream(
        websocket=websocket,
        ticket=ticket,
        branch_id=branch_id,
        bucket=bucket,
    )

