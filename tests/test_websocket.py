from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as aioredis
from fastapi import WebSocket, status
from httpx import AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.analytics.schemas import DashboardEvent, DashboardMetrics
from app.core.config import settings
from app.core.permissions import Permission
from app.core.redis import get_redis_client
from app.main import app
from app.websocket.auth import (
    authenticate_websocket_handshake,
    is_origin_allowed,
    validate_websocket_origin,
)
from app.websocket.manager import ConnectionManager
from app.websocket.publisher import (
    get_channel_name,
    get_global_channel_name,
    publish_dashboard_event,
)
from app.websocket.tickets import WebSocketTicketManager, ticket_manager


# ==========================================================
# 1. TICKET GENERATION, STORAGE, TTL & ATOMIC REDEMPTION TESTS
# ==========================================================

@pytest.mark.asyncio
async def test_01_ticket_generation_and_storage():
    """1. Ticket generation produces cryptographically random, opaque token and stores in Redis."""
    redis = get_redis_client()
    mgr = WebSocketTicketManager(redis)
    
    ticket = await mgr.create_ticket(resource_id="branch-100", user_id="user-1")
    assert isinstance(ticket, str)
    assert len(ticket) >= 32
    
    key = f"{settings.REDIS_KEY_NAMESPACE}:ws:ticket:{ticket}"
    raw = await redis.get(key)
    assert raw is not None
    data = json.loads(raw)
    assert data["resource_id"] == "branch-100"
    assert data["user_id"] == "user-1"
    
    # Clean up
    await redis.delete(key)


@pytest.mark.asyncio
async def test_02_ticket_ttl():
    """2. Ticket in Redis respects configured TTL."""
    redis = get_redis_client()
    mgr = WebSocketTicketManager(redis)
    
    ticket = await mgr.create_ticket(resource_id="branch-100", ttl_seconds=15)
    key = f"{settings.REDIS_KEY_NAMESPACE}:ws:ticket:{ticket}"
    
    ttl = await redis.ttl(key)
    assert 0 < ttl <= 15
    await redis.delete(key)


@pytest.mark.asyncio
async def test_03_atomic_getdel_single_use_and_replay_rejection():
    """3. Atomic GETDEL consumes ticket; second attempt (replay) returns None."""
    redis = get_redis_client()
    mgr = WebSocketTicketManager(redis)
    
    ticket = await mgr.create_ticket(resource_id="branch-A", user_id="user-abc")
    key = f"{settings.REDIS_KEY_NAMESPACE}:ws:ticket:{ticket}"
    
    # First redemption succeeds
    redeemed = await mgr.redeem_ticket(ticket)
    assert redeemed is not None
    assert redeemed["resource_id"] == "branch-A"
    assert redeemed["user_id"] == "user-abc"
    
    # Key is atomically deleted from Redis
    assert await redis.get(key) is None
    
    # Replay attempt fails immediately
    replay = await mgr.redeem_ticket(ticket)
    assert replay is None


@pytest.mark.asyncio
async def test_04_expired_ticket_rejected():
    """4. Expired ticket cannot be redeemed."""
    redis = get_redis_client()
    mgr = WebSocketTicketManager(redis)
    
    ticket = await mgr.create_ticket(resource_id="branch-exp", ttl_seconds=1)
    await asyncio.sleep(1.2)
    
    redeemed = await mgr.redeem_ticket(ticket)
    assert redeemed is None


@pytest.mark.asyncio
async def test_05_missing_or_invalid_ticket_redemption():
    """5. None, empty string, or non-existent ticket returns None."""
    mgr = WebSocketTicketManager(get_redis_client())
    
    assert await mgr.redeem_ticket(None) is None
    assert await mgr.redeem_ticket("") is None
    assert await mgr.redeem_ticket("   ") is None
    assert await mgr.redeem_ticket("non-existent-ticket-xyz") is None


# ==========================================================
# 2. ORIGIN VALIDATION TESTS
# ==========================================================

def test_06_origin_validation_rules():
    """6. Origin validation correctly handles allowed, disallowed, and wildcard origins."""
    assert is_origin_allowed("http://localhost:3000") is True
    assert is_origin_allowed("http://localhost:3000/") is True
    assert is_origin_allowed("http://127.0.0.1:3000") is True
    assert is_origin_allowed("http://localhost:5173") is True
    assert is_origin_allowed("https://evil-hacker.com") is False
    assert is_origin_allowed("http://malicious-site.org") is False


@pytest.mark.asyncio
async def test_07_origin_validation_websocket_rejection():
    """7. Disallowed origin closes connection with 1008 Policy Violation."""
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {"origin": "http://unauthorized-domain.com"}
    
    allowed = await validate_websocket_origin(ws)
    assert allowed is False
    ws.close.assert_called_once_with(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Policy Violation: Disallowed Origin",
    )


# ==========================================================
# 3. HANDSHAKE PROTOCOL & POLICY VIOLATION CLOSE CODES (1008)
# ==========================================================

@pytest.mark.asyncio
async def test_08_handshake_missing_ticket_closes_1008():
    """8. Handshake without ticket parameter closes with 1008 Policy Violation."""
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {"origin": "http://localhost:3000"}
    
    result = await authenticate_websocket_handshake(ws, ticket=None, expected_resource_id="branch-1")
    assert result is None
    assert ws.close.called
    assert ws.close.call_args[1]["code"] == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_09_handshake_invalid_ticket_closes_1008():
    """9. Handshake with invalid/already-used ticket closes with 1008 Policy Violation."""
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {"origin": "http://localhost:3000"}
    
    result = await authenticate_websocket_handshake(ws, ticket="bogus-token-123", expected_resource_id="branch-1")
    assert result is None
    assert ws.close.called
    assert ws.close.call_args[1]["code"] == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_10_handshake_resource_mismatch_closes_1008():
    """10. Ticket for resource A cannot connect to resource B (closes with 1008)."""
    ticket = await ticket_manager.create_ticket(resource_id="branch-A", user_id="user-1")
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {"origin": "http://localhost:3000"}
    
    result = await authenticate_websocket_handshake(ws, ticket=ticket, expected_resource_id="branch-B")
    assert result is None
    assert ws.close.called
    assert ws.close.call_args[1]["code"] == status.WS_1008_POLICY_VIOLATION
    assert "Ticket does not authorize requested resource" in ws.close.call_args[1]["reason"]


@pytest.mark.asyncio
async def test_11_handshake_success():
    """11. Valid ticket with matching resource and origin succeeds."""
    ticket = await ticket_manager.create_ticket(resource_id="branch-XYZ", user_id="user-42")
    ws = AsyncMock(spec=WebSocket)
    ws.headers = {"origin": "http://localhost:3000"}
    
    payload = await authenticate_websocket_handshake(ws, ticket=ticket, expected_resource_id="branch-XYZ")
    assert payload is not None
    assert payload["resource_id"] == "branch-XYZ"
    assert payload["user_id"] == "user-42"
    assert not ws.close.called


# ==========================================================
# 4. HTTP TICKET ENDPOINT AUTHENTICATION (PHASE 3)
# ==========================================================

@pytest.mark.asyncio
async def test_12_ticket_endpoint_requires_auth(unauthed_client: AsyncClient):
    """12. Unauthenticated request to mint ticket returns 401 Unauthorized."""
    res = await unauthed_client.post("/api/v1/dashboard/ws/ticket")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_13_ticket_endpoint_requires_analytics_permission(unauthed_client: AsyncClient, mint_token):
    """13. User lacking analytics:read permission cannot mint ticket (403 Forbidden)."""
    token = mint_token(claims={"permissions": ["entries:read"]})
    res = await unauthed_client.post(
        "/api/v1/dashboard/ws/ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_14_ticket_endpoint_success(client: AsyncClient):
    """14. Authenticated user with permission successfully mints opaque ticket (201 Created)."""
    res = await client.post("/api/v1/dashboard/branch-1/ws/ticket")
    assert res.status_code == 201
    data = res.json()
    assert "ticket" in data
    assert len(data["ticket"]) >= 32


@pytest.mark.asyncio
async def test_15_ticket_endpoint_aliases(client: AsyncClient):
    """15. Ticket minting endpoint aliases all function properly."""
    r1 = await client.post("/api/v1/dashboard/ws/ticket", json={"branch_id": "branch-2"})
    assert r1.status_code == 201
    assert "ticket" in r1.json()

    r2 = await client.post("/api/v1/branches/branch-3/ws/ticket")
    assert r2.status_code == 201
    assert "ticket" in r2.json()

    r3 = await client.post("/dashboard/branch-4/ws/ticket")
    assert r3.status_code == 201
    assert "ticket" in r3.json()


# ==========================================================
# 5. FULL END-TO-END WEBSOCKET & REDIS PUB/SUB STREAMING
# ==========================================================

@pytest.mark.asyncio
async def test_16_end_to_end_websocket_snapshot_and_pubsub_stream():
    """
    16. End-to-End WebSocket stream:
      - Valid ticket handshake
      - Initial snapshot received immediately upon connection
      - Ping / Pong client frames handled
      - Real-time Redis Pub/Sub events received immediately
      - Replay with same ticket is rejected (1008)
    """
    ticket = await ticket_manager.create_ticket(resource_id="branch-stream-1", user_id="user-10")
    tc = TestClient(app)
    
    with tc.websocket_connect(
        f"/ws/dashboard/branch-stream-1?ticket={ticket}",
        headers={"Origin": "http://localhost:3000"},
    ) as ws:
        # 1. Initial snapshot received
        snapshot = ws.receive_json()
        assert snapshot["type"] == "dashboard_snapshot"
        assert "data" in snapshot
        assert "people_in_store" in snapshot["data"]

        # 2. Client ping -> pong
        ws.send_text("ping")
        pong = ws.receive_text()
        assert pong == "pong"

        # 3. Real-time event published to Redis Pub/Sub
        redis = get_redis_client()
        channel = get_channel_name("branch-stream-1")
        update_event = {
            "type": "dashboard_update",
            "branch_id": "branch-stream-1",
            "data": {"people_in_store": 42, "total_entries_today": 100},
        }
        await redis.publish(channel, json.dumps(update_event))

        # 4. WebSocket receives the real-time event
        received = ws.receive_json()
        assert received["type"] == "dashboard_update"
        assert received["data"]["people_in_store"] == 42

    # 5. Replay attempt with same consumed ticket fails with 1008
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with tc.websocket_connect(
            f"/ws/dashboard/branch-stream-1?ticket={ticket}",
            headers={"Origin": "http://localhost:3000"},
        ):
            pass
    assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_17_multi_client_and_channel_isolation():
    """
    17. Channel isolation and multi-client subscription:
      - Client A on branch-A receives branch-A events
      - Client B on branch-B does NOT receive branch-A events
    """
    ticket_a = await ticket_manager.create_ticket(resource_id="branch-A")
    ticket_b = await ticket_manager.create_ticket(resource_id="branch-B")
    
    tc = TestClient(app)
    
    with tc.websocket_connect(f"/ws/dashboard/branch-A?ticket={ticket_a}", headers={"Origin": "http://localhost:3000"}) as ws_a:
        # Consume initial snapshots
        _ = ws_a.receive_json()

        with tc.websocket_connect(f"/ws/dashboard/branch-B?ticket={ticket_b}", headers={"Origin": "http://localhost:3000"}) as ws_b:
            _ = ws_b.receive_json()

            # Publish event to branch-A only
            redis = get_redis_client()
            event_a = {"type": "dashboard_update", "branch_id": "branch-A", "data": {"people_in_store": 15}}
            await redis.publish(get_channel_name("branch-A"), json.dumps(event_a))

            # ws_a receives event_a
            msg_a = ws_a.receive_json()
            assert msg_a["data"]["people_in_store"] == 15

            # ws_b sends ping and receives pong without receiving event_a
            ws_b.send_text("ping")
            assert ws_b.receive_text() == "pong"


@pytest.mark.asyncio
async def test_18_websocket_infrastructure_failure_closes_1013():
    """
    18. Unexpected Redis Pub/Sub failure during streaming results in 1013 Try Again Later.
    """
    ticket = await ticket_manager.create_ticket(resource_id="branch-err")
    
    tc = TestClient(app)
    # Simulate Redis get_message raising a ConnectionError during streaming
    with patch("redis.asyncio.client.PubSub.get_message", side_effect=aioredis.ConnectionError("Redis down")):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect(
                f"/ws/dashboard/branch-err?ticket={ticket}",
                headers={"Origin": "http://localhost:3000"},
            ) as ws:
                _ = ws.receive_json()  # Snapshot received
                # Wait for pubsub loop exception
                ws.receive_json()
        assert exc_info.value.code == status.WS_1013_TRY_AGAIN_LATER


# ==========================================================
# 6. IN-PROCESS CONNECTION MANAGER (BACKWARD COMPATIBILITY)
# ==========================================================

@pytest.mark.asyncio
async def test_19_in_process_connection_manager():
    """19. ConnectionManager registers, broadcasts, and discards broken connections safely."""
    mgr = ConnectionManager()
    ws1 = AsyncMock(spec=WebSocket)
    ws2 = AsyncMock(spec=WebSocket)
    
    await mgr.connect(ws1)
    await mgr.connect(ws2, branch_id="b1")
    assert mgr.active_count == 2
    
    payload = {"type": "dashboard_update", "data": {"people_in_store": 5}}
    await mgr.broadcast(payload)
    assert ws1.send_text.called
    assert ws2.send_text.called
    
    mgr.disconnect(ws1)
    mgr.disconnect(ws2, branch_id="b1")
    assert mgr.active_count == 0


# ==========================================================
# 7. ADDITIONAL SPECIALIZED ARCHITECTURE TESTS
# ==========================================================

@pytest.mark.asyncio
async def test_20_invalid_or_oversized_resource_rejected(client: AsyncClient):
    """20. Ticket minting rejects invalid, empty, or oversized resource IDs (400 Bad Request)."""
    r = await client.post("/api/v1/dashboard/ws/ticket", json={"branch_id": "x" * 200})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_21_multiple_clients_same_resource_receive_broadcast():
    """21. Multiple WebSocket clients connected to the same branch both receive broadcast events."""
    ticket1 = await ticket_manager.create_ticket(resource_id="branch-multi")
    ticket2 = await ticket_manager.create_ticket(resource_id="branch-multi")

    tc = TestClient(app)
    with tc.websocket_connect(f"/ws/dashboard/branch-multi?ticket={ticket1}", headers={"Origin": "http://localhost:3000"}) as ws1:
        _ = ws1.receive_json()

        with tc.websocket_connect(f"/ws/dashboard/branch-multi?ticket={ticket2}", headers={"Origin": "http://localhost:3000"}) as ws2:
            _ = ws2.receive_json()

            # Publish event to branch-multi
            redis = get_redis_client()
            evt = {"type": "dashboard_update", "branch_id": "branch-multi", "data": {"people_in_store": 77}}
            await redis.publish(get_channel_name("branch-multi"), json.dumps(evt))

            # Both ws1 and ws2 receive the event
            assert ws1.receive_json()["data"]["people_in_store"] == 77
            assert ws2.receive_json()["data"]["people_in_store"] == 77


@pytest.mark.asyncio
async def test_22_db_session_released_before_streaming():
    """
    22. Proves that SQLAlchemy database session is not held open during long-running Pub/Sub streaming.
    """
    session_opened = False
    session_closed = False

    from app.core import db as db_module

    original_sessionmaker = db_module.AsyncSessionLocal

    class MonitoredSession:
        def __init__(self):
            self._real_session = original_sessionmaker()

        async def __aenter__(self):
            nonlocal session_opened
            session_opened = True
            return await self._real_session.__aenter__()

        async def __aexit__(self, *args):
            nonlocal session_closed
            session_closed = True
            return await self._real_session.__aexit__(*args)

    ticket = await ticket_manager.create_ticket(resource_id="branch-dbsess")
    tc = TestClient(app)

    with patch("app.websocket.router.AsyncSessionLocal", side_effect=MonitoredSession):
        with tc.websocket_connect(f"/ws/dashboard/branch-dbsess?ticket={ticket}", headers={"Origin": "http://localhost:3000"}) as ws:
            _ = ws.receive_json()
            # At this point, the connection is open in the streaming loop.
            # Verify DB session was opened and ALREADY closed!
            assert session_opened is True
            assert session_closed is True
            ws.send_text("ping")
            assert ws.receive_text() == "pong"


@pytest.mark.asyncio
async def test_23_database_commit_precedes_redis_event_publishing(db_session):
    """
    23. Verifies that real-time event publication reflects committed database state.
    """
    from app.models.customer_flow import Entry
    import uuid
    from datetime import datetime, timezone

    # Insert committed entry
    entry = Entry(
        uuid=uuid.uuid4(),
        branch_id="branch-commit-check",
        entry_time=datetime.now(timezone.utc).replace(tzinfo=None),
        entry_count=999,
        enter_emotion="natural",
    )
    db_session.add(entry)
    await db_session.commit()

    # Publish dashboard event
    redis = get_redis_client()
    pubsub = redis.pubsub()
    channel = get_channel_name("branch-commit-check")
    await pubsub.subscribe(channel)

    # Trigger event publication
    await publish_dashboard_event(branch_id="branch-commit-check")

    # Read from pubsub
    msg = None
    for _ in range(5):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if msg is not None:
            break
        await asyncio.sleep(0.1)

    assert msg is not None
    data = json.loads(msg["data"])
    assert data["type"] == "dashboard_update"
    assert data["branch_id"] == "branch-commit-check"
    assert data["data"]["people_in_store"] >= 1

    await pubsub.unsubscribe(channel)
    await pubsub.aclose()
