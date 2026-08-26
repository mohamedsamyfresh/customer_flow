from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.endpoints.analytics import router as analytics_router
from app.api.endpoints.entries import router as entries_router
from app.api.endpoints.waiting_times import router as waiting_times_router
from app.core.auth import auth

api_router = APIRouter(dependencies=[Depends(auth._bearer)])
api_router.include_router(analytics_router)
api_router.include_router(entries_router)
api_router.include_router(waiting_times_router)

__all__ = ["api_router"]
