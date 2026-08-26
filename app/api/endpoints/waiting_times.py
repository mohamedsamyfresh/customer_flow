from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, auth
from app.core.db import get_db
from app.core.permissions import Permission
from app.models.waiting_time import WaitingTime

router = APIRouter(prefix="/api/v1/waiting-times", tags=["Waiting Times"])


class WaitingTimeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: str
    id: int | None = None
    entry_frame: int | None = None
    exit_frame: int | None = None
    entry_time: str | None = None
    exit_time: str | None = None
    duration: str | None = None
    duration_s: float | None = None


class PaginatedWaitingTimesResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: list[WaitingTimeResponse]


@router.get(
    "",
    response_model=PaginatedWaitingTimesResponse,
    dependencies=[Depends(auth.require_permission(Permission.WAITING_TIMES_READ))],
)
async def list_waiting_times(
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    min_duration_s: float | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Paginated list of queue waiting times.
    Requires 'waiting_times:read' permission.
    """
    conditions = []
    if min_duration_s is not None:
        conditions.append(WaitingTime.duration_s >= min_duration_s)

    count_stmt = select(func.count()).select_from(WaitingTime)
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    offset = (page - 1) * limit
    stmt = (
        select(WaitingTime)
        .offset(offset)
        .limit(limit)
    )
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    items = result.scalars().all()

    data = [
        WaitingTimeResponse(
            uuid=str(item.uuid),
            id=item.id,
            entry_frame=item.entry_frame,
            exit_frame=item.exit_frame,
            entry_time=item.entry_time,
            exit_time=item.exit_time,
            duration=item.duration,
            duration_s=float(item.duration_s) if item.duration_s is not None else None,
        )
        for item in items
    ]

    return PaginatedWaitingTimesResponse(
        total=total,
        page=page,
        limit=limit,
        data=data,
    )
