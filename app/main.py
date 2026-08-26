from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analytics.notifier import run_postgres_notification_listener
from app.api.router import api_router
from app.core.auth import auth
from app.core.db import close_db
from app.websocket.router import router as websocket_router

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eager JWKS prefetch on startup
    await auth.prefetch()

    # Background event listener for PostgreSQL real-time notifications
    stop_event = asyncio.Event()
    listener_task = asyncio.create_task(
        run_postgres_notification_listener(stop_event)
    )

    yield

    # Graceful shutdown
    stop_event.set()
    listener_task.cancel()
    try:
        await listener_task
    except (asyncio.CancelledError, Exception):
        pass

    # Clean httpx client shutdown
    await auth.aclose()
    await close_db()


app = FastAPI(
    title="Customer Flow & Analytics API",
    version="1.0.0",
    lifespan=lifespan,
)

# Install freshfamily-auth exception handlers (maps AuthError -> 401/403 JSON)
auth.install_exception_handlers(app)

# CORS middleware to support browser & frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include REST API & WebSocket routes
app.include_router(api_router)
app.include_router(websocket_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}