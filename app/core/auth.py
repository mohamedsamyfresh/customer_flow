from __future__ import annotations

import logging
from typing import Any

from fastapi import Query, WebSocket, status
from freshfamily_auth import AuthKit, TokenPayload

from app.core.config import settings

logger = logging.getLogger("auth")

# Instantiate AuthKit with application settings
auth = AuthKit(settings)

# CurrentUser dependency annotation
CurrentUser = auth.current_user


def require_permission(permission: str | Any):
    """
    Shortcut helper for permission checks.
    """
    return auth.require_permission(permission)


def require_role(role: str | Any):
    """
    Shortcut helper for role checks.
    """
    return auth.require_role(role)


async def authenticate_websocket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    required_permission: str | None = None,
) -> TokenPayload | None:
    """
    Authenticate a WebSocket connection during the handshake.
    
    Extracts the JWT from query parameter `?token=<jwt>`, or from the `Authorization` header,
    or from the `Sec-WebSocket-Protocol` header.
    
    Verifies token with `auth.decode_async(token)`.
    If valid and holding the required permission (or dev bypass applies), returns TokenPayload.
    Otherwise closes the connection with status 1008 (Policy Violation) and returns None.
    """
    jwt_token = token

    # Fallback to Authorization header if not in query param
    if not jwt_token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            jwt_token = auth_header[7:].strip()

    # Fallback to Sec-WebSocket-Protocol header
    if not jwt_token:
        protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
        for p in protocols:
            p_strip = p.strip()
            if p_strip.startswith("Bearer "):
                jwt_token = p_strip[7:].strip()
                break

    if not jwt_token:
        logger.warning("WebSocket connection rejected: Missing authentication token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing authentication token")
        return None

    try:
        user = await auth.decode_async(jwt_token)
    except Exception as exc:
        logger.warning("WebSocket authentication failed: %s", exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
        return None

    # Check permission if required
    if required_permission is not None:
        is_dev_bypass = (
            settings.AUTH_APP_ENV == "development"
            and settings.AUTH_DEV_BYPASS_ROLE is not None
            and user.has_role(settings.AUTH_DEV_BYPASS_ROLE)
        )
        if not is_dev_bypass and not user.has_permission(required_permission):
            logger.warning(
                "WebSocket rejected: User '%s' missing required permission '%s'",
                user.sub,
                required_permission,
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=f"Missing permission: {required_permission}")
            return None

    return user
