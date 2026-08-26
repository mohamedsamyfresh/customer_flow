from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis import get_redis_client

logger = logging.getLogger("websocket_tickets")


class WebSocketTicketManager:
    """
    Manages short-lived, cryptographically random, single-use WebSocket tickets.
    
    Architecture & Security Guarantees:
    - Tickets are generated via secrets.token_urlsafe(32) (opaque & URL-safe).
    - Tickets are stored in Redis under the key namespace: `<namespace>:ws:ticket:<ticket>`.
    - Tickets have a strict TTL (configured via WEBSOCKET_TICKET_TTL_SECONDS, default 30s).
    - Redemption uses atomic Redis GETDEL, ensuring single-use and race-condition immunity.
    - Sensitive token values and headers are NEVER logged.
    """

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis_client = redis_client

    def _get_redis(self) -> aioredis.Redis:
        if self._redis_client is not None:
            return self._redis_client
        return get_redis_client()

    def _ticket_key(self, ticket: str) -> str:
        return f"{settings.REDIS_KEY_NAMESPACE}:ws:ticket:{ticket}"

    async def create_ticket(
        self,
        resource_id: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        """
        Generates an opaque, cryptographically random ticket, associates it with the
        specified resource/project ID and user, and stores it in Redis with the configured TTL.
        """
        ticket = secrets.token_urlsafe(32)
        ttl = ttl_seconds if ttl_seconds is not None else settings.WEBSOCKET_TICKET_TTL_SECONDS
        key = self._ticket_key(ticket)

        payload = {
            "resource_id": str(resource_id),
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }

        redis = self._get_redis()
        await redis.set(key, json.dumps(payload), ex=ttl)

        logger.info(
            "WebSocket ticket issued (resource_id=%s, ttl_seconds=%d)",
            resource_id,
            ttl,
        )
        return ticket

    async def redeem_ticket(
        self,
        ticket: str | None,
    ) -> dict[str, Any] | None:
        """
        Atomically redeems and consumes a WebSocket ticket using Redis GETDEL.
        
        Returns:
            dict containing ticket payload (including 'resource_id', 'user_id') if valid.
            None if ticket is missing, expired, invalid, or already consumed (replay).
        """
        if not ticket or not isinstance(ticket, str) or len(ticket.strip()) == 0:
            logger.warning("WebSocket ticket redemption failed: empty or missing ticket")
            return None

        key = self._ticket_key(ticket.strip())
        redis = self._get_redis()

        try:
            raw_value = await redis.getdel(key)
        except Exception as exc:
            logger.error("Redis GETDEL error during ticket redemption: %s", exc)
            return None

        if raw_value is None:
            logger.warning("WebSocket ticket redemption failed: ticket not found, expired, or already used (replay attempt)")
            return None

        try:
            if isinstance(raw_value, bytes):
                raw_value = raw_value.decode("utf-8")
            data = json.loads(raw_value)
            resource_id = data.get("resource_id")
            logger.info("WebSocket ticket redeemed successfully (resource_id=%s)", resource_id)
            return data
        except Exception as parse_err:
            # Fallback in case raw string resource_id was stored directly
            logger.debug("Parsing ticket payload as fallback raw string: %s", parse_err)
            return {"resource_id": str(raw_value), "user_id": None}


# Global singleton manager instance
ticket_manager = WebSocketTicketManager()
