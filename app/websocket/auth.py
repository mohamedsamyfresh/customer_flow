from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket, status

from app.core.config import settings
from app.websocket.tickets import ticket_manager

logger = logging.getLogger("websocket_auth")


def is_origin_allowed(origin: str | None) -> bool:
    """
    Validates whether the Origin header matches the configured WEBSOCKET_ALLOWED_ORIGINS.
    """
    if not origin:
        # Non-browser or direct clients without Origin header
        return True

    allowed_list = settings.WEBSOCKET_ALLOWED_ORIGINS
    if "*" in allowed_list:
        return True

    origin_normalized = origin.strip().rstrip("/")
    for allowed in allowed_list:
        if origin_normalized == allowed.strip().rstrip("/"):
            return True
    return False


async def validate_websocket_origin(websocket: WebSocket) -> bool:
    """
    Inspects websocket origin header and validates against allowed origins.
    Rejects with WS_1008_POLICY_VIOLATION if invalid.
    """
    origin = websocket.headers.get("origin")
    if origin is not None and not is_origin_allowed(origin):
        logger.warning(
            "WebSocket connection rejected: Origin '%s' is not authorized",
            origin,
        )
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Policy Violation: Disallowed Origin",
        )
        return False
    return True


async def authenticate_websocket_handshake(
    websocket: WebSocket,
    ticket: str | None,
    expected_resource_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Full pre-acceptance WebSocket authentication and authorization protocol:
    1. Origin validation
    2. Ticket existence validation
    3. Atomic single-use ticket consumption via Redis GETDEL
    4. Replay and expiration protection
    5. Resource ID matching validation
    
    Returns:
        dict containing ticket metadata if fully authorized.
        None if validation failed and connection was closed with 1008 Policy Violation.
    """
    # 1. Validate Origin
    if not await validate_websocket_origin(websocket):
        return None

    # 2. Validate ticket parameter presence
    if not ticket or not isinstance(ticket, str) or len(ticket.strip()) == 0:
        logger.warning("WebSocket connection rejected: Missing ticket query parameter")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Policy Violation: Missing authentication ticket",
        )
        return None

    # 3. Atomically consume ticket using Redis GETDEL
    ticket_payload = await ticket_manager.redeem_ticket(ticket.strip())
    if ticket_payload is None:
        logger.warning("WebSocket connection rejected: Invalid, expired, or already used ticket")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Policy Violation: Invalid or expired ticket",
        )
        return None

    # 4. Verify ticket belongs to requested resource
    ticket_resource = ticket_payload.get("resource_id")
    target_expected = expected_resource_id if expected_resource_id not in (None, "", "global", "dashboard", "_global") else "global"
    target_ticket = ticket_resource if ticket_resource not in (None, "", "global", "dashboard", "_global") else "global"

    if target_expected != target_ticket:
        logger.warning(
            "WebSocket connection rejected: Ticket resource mismatch (ticket minted for '%s', URL requested '%s')",
            ticket_resource,
            expected_resource_id,
        )
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Policy Violation: Ticket does not authorize requested resource",
        )
        return None

    return ticket_payload
