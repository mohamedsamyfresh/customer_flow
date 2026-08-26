from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("websocket_manager")


class ConnectionManager:
    """
    Manages active WebSocket connections for the real-time dashboard.
    Supports global and branch-specific subscription channels, concurrent
    broadcasting, and resilient error recovery for dropped connections.
    """

    def __init__(self) -> None:
        # Global set of all active WebSocket connections
        self._connections: set[WebSocket] = set()
        # Per-branch mapping: branch_id -> set of WebSockets
        self._branch_connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return len(self._connections)

    async def connect(
        self,
        websocket: WebSocket,
        branch_id: str | None = None,
    ) -> None:
        """
        Accepts the WebSocket connection and registers it in the manager.
        """
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
            if branch_id:
                if branch_id not in self._branch_connections:
                    self._branch_connections[branch_id] = set()
                self._branch_connections[branch_id].add(websocket)

        logger.info(
            "WebSocket client connected (branch_id=%s, total_active=%d)",
            branch_id,
            len(self._connections),
        )

    def disconnect(
        self,
        websocket: WebSocket,
        branch_id: str | None = None,
    ) -> None:
        """
        Removes the WebSocket connection from active registrations.
        """
        self._connections.discard(websocket)
        if branch_id and branch_id in self._branch_connections:
            self._branch_connections[branch_id].discard(websocket)
            if not self._branch_connections[branch_id]:
                del self._branch_connections[branch_id]
        else:
            # Check all branches just in case branch_id wasn't passed
            for b_id, b_set in list(self._branch_connections.items()):
                b_set.discard(websocket)
                if not b_set:
                    del self._branch_connections[b_id]

        logger.info(
            "WebSocket client disconnected (remaining_active=%d)",
            len(self._connections),
        )

    async def send_personal_message(
        self,
        message: dict[str, Any] | str,
        websocket: WebSocket,
    ) -> bool:
        """
        Sends a JSON message to a single client. Returns True if successful, False otherwise.
        """
        try:
            if isinstance(message, dict):
                text_data = json.dumps(message)
            else:
                text_data = message
            await websocket.send_text(text_data)
            return True
        except Exception as err:
            logger.warning("Failed to send message to client: %s", err)
            self.disconnect(websocket)
            return False

    async def broadcast(
        self,
        message: dict[str, Any] | str,
        branch_id: str | None = None,
    ) -> None:
        """
        Broadcasts a message to all relevant connected clients.
        If a client socket is broken or disconnected, it is safely removed
        without interrupting delivery to other connected clients.
        """
        if not self._connections:
            return

        if isinstance(message, dict):
            payload_str = json.dumps(message)
        else:
            payload_str = message

        # Determine target clients
        async with self._lock:
            if branch_id:
                # Target branch clients plus global subscribers (not assigned to any specific branch)
                branch_subs = self._branch_connections.get(branch_id, set())
                # Global subscribers are those in self._connections but not in any branch_connections
                all_branch_members = {ws for s in self._branch_connections.values() for ws in s}
                global_subs = self._connections - all_branch_members
                targets = list(branch_subs | global_subs)
            else:
                targets = list(self._connections)

        if not targets:
            return

        dead_connections: list[WebSocket] = []

        async def _safe_send(ws: WebSocket) -> None:
            try:
                await ws.send_text(payload_str)
            except Exception as e:
                logger.debug("Broadcast send error for socket: %s", e)
                dead_connections.append(ws)

        await asyncio.gather(*[_safe_send(ws) for ws in targets], return_exceptions=True)

        if dead_connections:
            async with self._lock:
                for dead_ws in dead_connections:
                    self.disconnect(dead_ws, branch_id)


# Global singleton instance for in-process WebSocket connection management
manager = ConnectionManager()
