from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.schemas import (
    DashboardMetrics,
    EmotionTransitions,
    OccupancyTimelineResponse,
)
from app.analytics.service import AnalyticsService
from app.core.auth import CurrentUser, auth
from app.core.db import get_db
from app.core.permissions import Permission

router = APIRouter(prefix="", tags=["Analytics"])


def parse_date_str(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


@router.get(
    "/api/dashboard/metrics",
    response_model=DashboardMetrics,
    dependencies=[Depends(auth.require_permission(Permission.ANALYTICS_READ))],
)
@router.get(
    "/api/v1/analytics/summary",
    response_model=DashboardMetrics,
    dependencies=[Depends(auth.require_permission(Permission.ANALYTICS_READ))],
)
async def get_dashboard_metrics(
    user: CurrentUser,
    branch_id: str | None = Query(default=None, description="Optional branch ID to filter by"),
    target_date: str | None = Query(default=None, alias="date", description="Target date in YYYY-MM-DD format"),
    bucket: str = Query(default="1h", description="Time bucket for peak occupancy: 5m, 15m, 30m, 1h"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns current or historical dashboard metrics:
      - people_in_store
      - total_entries_today
      - total_exits_today
      - emotion_transitions (natural->angry, angry->natural, natural->natural, angry->angry)
      - longest_stay (completed session)
      - highest_occupancy_period
    Requires 'analytics:read' permission.
    """
    parsed_date = parse_date_str(target_date)
    analytics = AnalyticsService(db)
    return await analytics.get_dashboard_metrics(
        branch_id=branch_id,
        target_date=parsed_date,
        bucket=bucket,
    )


@router.get(
    "/api/v1/analytics/occupancy",
    response_model=OccupancyTimelineResponse,
    dependencies=[Depends(auth.require_permission(Permission.ANALYTICS_READ))],
)
async def get_occupancy_timeline(
    user: CurrentUser,
    branch_id: str | None = Query(default=None, description="Optional branch ID to filter by"),
    target_date: str | None = Query(default=None, alias="date", description="Target date in YYYY-MM-DD format"),
    bucket: str = Query(default="1h", description="Bucket size (5m, 15m, 30m, 1h)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns occupancy timeline across time slots for a given day,
    along with the peak period and peak occupancy.
    Requires 'analytics:read' permission.
    """
    parsed_date = parse_date_str(target_date)
    analytics = AnalyticsService(db)
    return await analytics.get_occupancy_timeline(
        branch_id=branch_id,
        target_date=parsed_date,
        bucket=bucket,
    )


@router.get(
    "/api/v1/analytics/emotions",
    response_model=EmotionTransitions,
    dependencies=[Depends(auth.require_permission(Permission.ANALYTICS_READ))],
)
async def get_emotion_transitions(
    user: CurrentUser,
    branch_id: str | None = Query(default=None, description="Optional branch ID"),
    target_date: str | None = Query(default=None, alias="date", description="Target date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns emotion sentiment transition counts from entry to exit.
    Requires 'analytics:read' permission.
    """
    parsed_date = parse_date_str(target_date)
    analytics = AnalyticsService(db)
    return await analytics.get_emotion_transitions(
        branch_id=branch_id,
        target_date=parsed_date,
    )
