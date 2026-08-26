from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from app.core.config import settings

logger = logging.getLogger("redis_core")

_clients_by_loop: dict[int, aioredis.Redis] = {}
_pools_by_loop: dict[int, ConnectionPool] = {}
_default_client: aioredis.Redis | None = None


def get_redis_pool() -> ConnectionPool:
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop_id = 0

    if loop_id not in _pools_by_loop:
        _pools_by_loop[loop_id] = ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
    return _pools_by_loop[loop_id]


def get_redis_client() -> aioredis.Redis:
    """
    Returns an async Redis client bound to the current event loop.
    Safe across multiple event loops, background threads, and TestClient runners.
    """
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop_id = 0

    if loop_id not in _clients_by_loop:
        pool = get_redis_pool()
        _clients_by_loop[loop_id] = aioredis.Redis(connection_pool=pool)
    return _clients_by_loop[loop_id]


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """
    FastAPI dependency yielding an async Redis client.
    """
    client = get_redis_client()
    try:
        yield client
    finally:
        pass


async def init_redis() -> None:
    """
    Eagerly initializes and verifies the Redis connection on startup.
    """
    client = get_redis_client()
    try:
        await client.ping()
        logger.info("Redis connection established successfully (%s)", settings.REDIS_URL)
    except Exception as exc:
        logger.error("Failed to connect to Redis on startup: %s", exc)


async def close_redis() -> None:
    """
    Gracefully disconnects all Redis clients and connection pools.
    """
    global _clients_by_loop, _pools_by_loop
    for loop_id, client in list(_clients_by_loop.items()):
        try:
            await client.aclose()
        except Exception as exc:
            logger.debug("Error closing Redis client for loop %s: %s", loop_id, exc)
    _clients_by_loop.clear()

    for loop_id, pool in list(_pools_by_loop.items()):
        try:
            await pool.disconnect()
        except Exception as exc:
            logger.debug("Error disconnecting Redis pool for loop %s: %s", loop_id, exc)
    _pools_by_loop.clear()

    logger.info("Redis connections closed.")
