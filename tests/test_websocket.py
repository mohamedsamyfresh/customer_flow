from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket, status

from app.core.auth import authenticate_websocket
from app.core.permissions import Permission
from app.websocket.manager import ConnectionManager


@pytest.mark.asyncio
async def test_websocket_manager_connect_and_disconnect():
    """
    Test that clients can connect and disconnect safely.
    """
    manager = ConnectionManager()
    ws1 = AsyncMock(spec=WebSocket)
    ws2 = AsyncMock(spec=WebSocket)

    assert manager.active_count == 0

    await manager.connect(ws1)
    await manager.connect(ws2, branch_id="branch-A")

    assert manager.active_count == 2
    assert ws1.accept.called
    assert ws2.accept.called

    manager.disconnect(ws1)
    assert manager.active_count == 1

    manager.disconnect(ws2, branch_id="branch-A")
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_websocket_manager_broadcast_reaches_clients():
    """
    Test broadcast sends messages to all active clients.
    """
    manager = ConnectionManager()
    ws1 = AsyncMock(spec=WebSocket)
    ws2 = AsyncMock(spec=WebSocket)

    await manager.connect(ws1)
    await manager.connect(ws2)

    payload = {"type": "dashboard_update", "data": {"people_in_store": 5}}
    await manager.broadcast(payload)

    assert ws1.send_text.called
    assert ws2.send_text.called


@pytest.mark.asyncio
async def test_broken_client_does_not_break_broadcast():
    """
    Given multiple connected clients:
    If one client raises an error during broadcast (broken pipe / abrupt disconnect),
    it is removed, and all other clients still receive the message.
    """
    manager = ConnectionManager()
    good_ws1 = AsyncMock(spec=WebSocket)
    broken_ws = AsyncMock(spec=WebSocket)
    broken_ws.send_text.side_effect = RuntimeError("Socket disconnected")
    good_ws2 = AsyncMock(spec=WebSocket)

    await manager.connect(good_ws1)
    await manager.connect(broken_ws)
    await manager.connect(good_ws2)

    assert manager.active_count == 3

    payload = {"type": "dashboard_update", "data": {"people_in_store": 12}}
    await manager.broadcast(payload)

    # Good clients received message
    assert good_ws1.send_text.called
    assert good_ws2.send_text.called

    # Broken client was disconnected and removed
    assert manager.active_count == 2
    assert broken_ws not in manager._connections


@pytest.mark.asyncio
async def test_branch_filtering_broadcast():
    """
    Test that branch-specific broadcast reaches branch subscribers and global subscribers,
    but not subscribers of different branches.
    """
    manager = ConnectionManager()
    global_ws = AsyncMock(spec=WebSocket)
    branch_a_ws = AsyncMock(spec=WebSocket)
    branch_b_ws = AsyncMock(spec=WebSocket)

    await manager.connect(global_ws)  # Global subscriber
    await manager.connect(branch_a_ws, branch_id="branch-A")
    await manager.connect(branch_b_ws, branch_id="branch-B")

    payload = {"type": "dashboard_update", "branch_id": "branch-A", "data": {"people_in_store": 8}}
    await manager.broadcast(payload, branch_id="branch-A")

    # branch_a_ws and global_ws must receive
    assert branch_a_ws.send_text.called
    assert global_ws.send_text.called

    # branch_b_ws must NOT receive branch-A update
    assert not branch_b_ws.send_text.called


@pytest.mark.asyncio
async def test_websocket_auth_missing_token():
    """
    WebSocket connection without token is closed with WS_1008_POLICY_VIOLATION.
    """
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {}

    user = await authenticate_websocket(ws, token=None, required_permission=Permission.ANALYTICS_READ)
    assert user is None
    ws.close.assert_called_once_with(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Missing authentication token",
    )


@pytest.mark.asyncio
async def test_websocket_auth_invalid_token():
    """
    WebSocket connection with invalid token is closed with WS_1008_POLICY_VIOLATION.
    """
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {}

    user = await authenticate_websocket(ws, token="invalid.token.here", required_permission=Permission.ANALYTICS_READ)
    assert user is None
    assert ws.close.called
    assert ws.close.call_args[1]["code"] == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_websocket_auth_missing_permission(mint_token):
    """
    WebSocket connection with valid token but missing required permission is rejected.
    """
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {}
    token = mint_token(claims={"permissions": ["entries:read"]})  # missing analytics:read

    user = await authenticate_websocket(ws, token=token, required_permission=Permission.ANALYTICS_READ)
    assert user is None
    assert ws.close.called
    assert ws.close.call_args[1]["code"] == status.WS_1008_POLICY_VIOLATION
    assert "Missing permission" in ws.close.call_args[1]["reason"]


@pytest.mark.asyncio
async def test_websocket_auth_success(mint_token):
    """
    WebSocket connection with valid token and permission is accepted.
    """
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {}
    token = mint_token(claims={"permissions": ["analytics:read"]})

    user = await authenticate_websocket(ws, token=token, required_permission=Permission.ANALYTICS_READ)
    assert user is not None
    assert user.sub == "emp-100"
    assert user.has_permission("analytics:read")
    assert not ws.close.called


@pytest.mark.asyncio
async def test_websocket_auth_via_header(mint_token):
    """
    WebSocket connection authenticated via Authorization header is accepted.
    """
    token = mint_token(claims={"permissions": ["analytics:read"]})
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {"authorization": f"Bearer {token}"}

    user = await authenticate_websocket(ws, token=None, required_permission=Permission.ANALYTICS_READ)
    assert user is not None
    assert user.sub == "emp-100"
    assert not ws.close.called
