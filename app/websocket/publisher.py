from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.analytics.schemas import DashboardEvent
from app.analytics.service import AnalyticsService
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.redis import get_redis_client
from app.websocket.manager import manager

logger = logging.getLogger("websocket_publisher")


def get_channel_name(resource_id: str | None = None) -> str:
    """
    Returns the isolated Redis Pub/Sub channel for a given resource / branch.
    """
    if resource_id and resource_id not in ("global", "dashboard", "_global"):
        return f"{settings.REDIS_KEY_NAMESPACE}:events:branch:{resource_id}"
    return f"{settings.REDIS_KEY_NAMESPACE}:events:global"


def get_global_channel_name() -> str:
    return f"{settings.REDIS_KEY_NAMESPACE}:events:global"


async def publish_dashboard_event(
    branch_id: str | None = None,
    event: DashboardEvent | dict[str, Any] | None = None,
) -> None:
    """
    Publishes a real-time dashboard event to the isolated Redis channel and broadcasts in-memory.
    
    If event is not supplied, loads latest metrics using a short-lived DB session
    that is immediately closed before publishing.
    """
    if event is None:
        try:
            async with AsyncSessionLocal() as db:
                analytics = AnalyticsService(db)
                metrics = await analytics.get_dashboard_metrics(
                    branch_id=branch_id if branch_id not in ("global", "dashboard", "_global") else None
                )
                event = DashboardEvent(
                    type="dashboard_update",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    branch_id=branch_id if branch_id not in ("global", "dashboard", "_global") else None,
                    data=metrics,
                )
        except Exception as exc:
            logger.error("Failed to compute dashboard metrics for publishing: %s", exc)
            return

    # Serialize event
    if isinstance(event, DashboardEvent):
        payload_str = event.model_dump_json()
        payload_dict = event.model_dump()
    elif isinstance(event, dict):
        payload_str = json.dumps(event)
        payload_dict = event
    else:
        payload_str = str(event)
        payload_dict = {"raw": str(event)}

    # Publish to Redis Pub/Sub
    try:
        redis = get_redis_client()
        if branch_id and branch_id not in ("global", "dashboard", "_global"):
            branch_channel = get_channel_name(branch_id)
            global_channel = get_global_channel_name()
            # Publish to branch-specific subscribers and global subscribers
            await redis.publish(branch_channel, payload_str)
            await redis.publish(global_channel, payload_str)
            logger.debug("Published event to channels '%s' and '%s'", branch_channel, global_channel)
        else:
            global_channel = get_global_channel_name()
            await redis.publish(global_channel, payload_str)
            logger.debug("Published event to global channel '%s'", global_channel)
    except Exception as redis_err:
        logger.warning("Redis Pub/Sub publish failed (non-critical): %s", redis_err)

    # In-process broadcast fallback for local manager clients
    try:
        await manager.broadcast(
            payload_dict,
            branch_id=branch_id if branch_id not in ("global", "dashboard", "_global") else None,
        )
    except Exception as broadcast_err:
        logger.debug("In-process broadcast error: %s", broadcast_err)
