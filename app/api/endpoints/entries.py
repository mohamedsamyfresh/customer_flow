from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, auth
from app.core.db import get_db
from app.core.permissions import Permission
from app.models.customer_flow import Entry

router = APIRouter(prefix="/api/v1/entries", tags=["Entries"])


class CustomerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: str
    entry_time: datetime | None = None
    entry_count: int | None = None
    age_class: str | None = None
    gender: str | None = None
    gender_conf: float | None = None
    enter_emotion: str | None = None
    enter_emotion_conf: float | None = None
    entry_face_box: str | None = None
    entry_face_vector: str | None = None
    exit_time: datetime | None = None
    exit_count: int | None = None
    exit_emotion: str | None = None
    exit_emotion_conf: float | None = None
    exit_face_box: str | None = None
    exit_face_vector: str | None = None
    face_match_score: float | None = None
    branch_id: str | None = None
    camera_id: str | None = None


class PaginatedEntriesResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: list[CustomerEntryResponse]


@router.get(
    "",
    response_model=PaginatedEntriesResponse,
    dependencies=[Depends(auth.require_permission(Permission.ENTRIES_READ))],
)
async def list_entries(
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all", description="'inside', 'exited', or 'all'"),
    gender: str | None = Query(default=None),
    branch_id: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Paginated list of customer entries with status, gender, and date filtering.
    Requires 'entries:read' permission.
    """
    conditions = []

    if status == "inside":
        conditions.append(Entry.entry_time.is_not(None))
        conditions.append(Entry.exit_time.is_(None))
    elif status == "exited":
        conditions.append(Entry.exit_time.is_not(None))

    if gender:
        conditions.append(func.lower(Entry.gender) == gender.lower())

    if branch_id:
        conditions.append(Entry.branch_id == branch_id)

    if date_from:
        conditions.append(Entry.entry_time >= date_from)

    if date_to:
        conditions.append(Entry.entry_time <= date_to)

    # Total count
    count_stmt = select(func.count()).select_from(Entry)
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    # Data fetch
    offset = (page - 1) * limit
    stmt = (
        select(Entry)
        .order_by(Entry.entry_time.desc().nulls_last())
        .offset(offset)
        .limit(limit)
    )
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    entries = result.scalars().all()

    data = [
        CustomerEntryResponse(
            uuid=str(e.uuid),
            entry_time=e.entry_time,
            entry_count=e.entry_count,
            age_class=e.age_class,
            gender=e.gender,
            gender_conf=float(e.gender_conf) if e.gender_conf is not None else None,
            enter_emotion=e.enter_emotion,
            enter_emotion_conf=float(e.enter_emotion_conf) if e.enter_emotion_conf is not None else None,
            entry_face_box=e.entry_face_box,
            entry_face_vector=e.entry_face_vector,
            exit_time=e.exit_time,
            exit_count=e.exit_count,
            exit_emotion=e.exit_emotion,
            exit_emotion_conf=float(e.exit_emotion_conf) if e.exit_emotion_conf is not None else None,
            exit_face_box=e.exit_face_box,
            exit_face_vector=e.exit_face_vector,
            face_match_score=float(e.face_match_score) if e.face_match_score is not None else None,
            branch_id=e.branch_id,
            camera_id=e.camera_id,
        )
        for e in entries
    ]

    return PaginatedEntriesResponse(
        total=total,
        page=page,
        limit=limit,
        data=data,
    )
