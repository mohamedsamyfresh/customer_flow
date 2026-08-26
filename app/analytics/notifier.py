from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.schemas import DashboardEvent
from app.analytics.service import AnalyticsService
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.websocket.manager import manager

logger = logging.getLogger("analytics_notifier")
CHANNEL_NAME = "dashboard_updates"


async def notify_db_dashboard_update(
    db: AsyncSession,
    branch_id: str | None = None,
) -> None:
    """
    Emits a PostgreSQL NOTIFY event on the 'dashboard_updates' channel.
    This enables multi-process/multi-container real-time broadcasting without external brokers.
    """
    payload = json.dumps(
        {
            "branch_id": branch_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        await db.execute(
            text(f"SELECT pg_notify('{CHANNEL_NAME}', :payload)"),
            {"payload": payload},
        )
    except Exception as e:
        logger.warning("Failed to emit pg_notify (non-critical): %s", e)


async def broadcast_dashboard_update(
    db: AsyncSession,
    branch_id: str | None = None,
) -> None:
    """
    Computes current metrics and directly broadcasts to in-process WebSocket clients.
    """
    if manager.active_count == 0:
        return

    try:
        analytics = AnalyticsService(db)
        metrics = await analytics.get_dashboard_metrics(branch_id=branch_id)
        event = DashboardEvent(
            type="dashboard_update",
            timestamp=datetime.now(timezone.utc).isoformat(),
            branch_id=branch_id,
            data=metrics,
        )
        await manager.broadcast(event.model_dump(), branch_id=branch_id)
    except Exception as e:
        logger.warning("In-process dashboard broadcast failed (non-critical): %s", e)


def _get_asyncpg_dsn(database_url: str) -> str:
    """
    Converts SQLAlchemy database URL (e.g. postgresql+asyncpg://...) into asyncpg-compatible DSN.
    """
    url = database_url
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif url.startswith("postgresql+psycopg2://"):
        url = url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


async def run_postgres_notification_listener(
    stop_event: asyncio.Event,
) -> None:
    """
    Long-running background task that listens for PostgreSQL notifications
    and broadcasts updated dashboard states to connected WebSocket clients.
    Automatically reconnects on connection loss.
    """
    dsn = _get_asyncpg_dsn(settings.DATABASE_URL)
    logger.info("Starting PostgreSQL notification listener on channel '%s'...", CHANNEL_NAME)

    while not stop_event.is_set():
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn)

            async def _on_notification(
                connection: asyncpg.Connection,
                pid: int,
                channel: str,
                payload: str,
            ) -> None:
                try:
                    data = json.loads(payload)
                    branch_id = data.get("branch_id")
                except Exception:
                    branch_id = None

                # Fetch fresh metrics and broadcast
                try:
                    async with AsyncSessionLocal() as session:
                        analytics = AnalyticsService(session)
                        metrics = await analytics.get_dashboard_metrics(branch_id=branch_id)
                        event = DashboardEvent(
                            type="dashboard_update",
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            branch_id=branch_id,
                            data=metrics,
                        )
                        await manager.broadcast(event.model_dump(), branch_id=branch_id)
                except Exception as b_err:
                    logger.warning("Error computing metrics for broadcast: %s", b_err)

            await conn.add_listener(CHANNEL_NAME, _on_notification)
            logger.info("PostgreSQL notification listener active on channel '%s'", CHANNEL_NAME)

            # Keep listening until stopped or disconnected
            while not stop_event.is_set():
                await asyncio.sleep(1.0)

            await conn.remove_listener(CHANNEL_NAME, _on_notification)
            await conn.close()
            break

        except asyncio.CancelledError:
            break
        except Exception as err:
            logger.warning("PostgreSQL notification listener error: %s. Reconnecting in 3s...", err)
            if conn:
                try:
                    await conn.close()
                except Exception:
                    pass
            await asyncio.sleep(3.0)

    logger.info("PostgreSQL notification listener stopped.")
