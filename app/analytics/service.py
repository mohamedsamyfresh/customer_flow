from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.schemas import (
    DashboardMetrics,
    EmotionTransitions,
    HighestOccupancyPeriod,
    LongestStay,
    OccupancyBucket,
    OccupancyTimelineResponse,
)
from app.models.customer_flow import Entry


class AnalyticsService:
    """
    Analytics service responsible for aggregating metrics from PostgreSQL.
    Provides fast, indexed SQL queries for:
      1. People In Store (live active sessions)
      2. Total Entries Today
      3. Total Exits Today
      4. Emotion Transitions (natural->angry, angry->natural, natural->natural, angry->angry)
      5. Longest Customer Stay (completed sessions)
      6. Occupancy over time & Highest Occupancy Period (configurable time buckets)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ==========================================================
    # CORE DASHBOARD METRICS
    # ==========================================================

    async def get_dashboard_metrics(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
        bucket: str = "1h",
    ) -> DashboardMetrics:
        """
        Calculates all current dashboard metrics in an optimized manner.
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        start_of_day = datetime.combine(target_date, time.min)
        end_of_day = datetime.combine(target_date, time.max)

        # 1. Base counts (people in store, entries today, exits today)
        people_in_store, total_entries, total_exits = await self._get_counts(
            start_of_day, end_of_day, branch_id
        )

        # 2. Emotion transitions
        emotion_transitions = await self.get_emotion_transitions(
            branch_id=branch_id, target_date=target_date
        )

        # 3. Longest stay
        longest_stay = await self.get_longest_stay(
            branch_id=branch_id, target_date=target_date
        )

        # 4. Highest occupancy period
        highest_occupancy_period = await self.get_highest_occupancy_period(
            branch_id=branch_id, target_date=target_date, bucket=bucket
        )

        return DashboardMetrics(
            people_in_store=people_in_store,
            total_entries_today=total_entries,
            total_exits_today=total_exits,
            emotion_transitions=emotion_transitions,
            longest_stay=longest_stay,
            highest_occupancy_period=highest_occupancy_period,
        )

    # ==========================================================
    # 1. COUNTS (PEOPLE IN STORE / ENTRIES / EXITS)
    # ==========================================================

    async def _get_counts(
        self,
        start_of_day: datetime,
        end_of_day: datetime,
        branch_id: str | None = None,
    ) -> tuple[int, int, int]:
        """
        Computes people_in_store, total_entries_today, total_exits_today.
        - people_in_store: active sessions (entry_time IS NOT NULL AND exit_time IS NULL)
        - total_entries_today: entered between start_of_day and end_of_day
        - total_exits_today: exited between start_of_day and end_of_day
        """
        # Active sessions currently inside
        active_conditions = [
            Entry.entry_time.is_not(None),
            Entry.exit_time.is_(None),
        ]
        if branch_id is not None:
            active_conditions.append(Entry.branch_id == branch_id)

        stmt_active = select(func.count()).select_from(Entry).where(and_(*active_conditions))
        res_active = await self.db.execute(stmt_active)
        people_in_store = res_active.scalar() or 0

        # Entries today
        entry_conditions = [
            Entry.entry_time.is_not(None),
            Entry.entry_time >= start_of_day,
            Entry.entry_time <= end_of_day,
        ]
        if branch_id is not None:
            entry_conditions.append(Entry.branch_id == branch_id)

        stmt_entries = select(func.count()).select_from(Entry).where(and_(*entry_conditions))
        res_entries = await self.db.execute(stmt_entries)
        total_entries = res_entries.scalar() or 0

        # Exits today
        exit_conditions = [
            Entry.exit_time.is_not(None),
            Entry.exit_time >= start_of_day,
            Entry.exit_time <= end_of_day,
        ]
        if branch_id is not None:
            exit_conditions.append(Entry.branch_id == branch_id)

        stmt_exits = select(func.count()).select_from(Entry).where(and_(*exit_conditions))
        res_exits = await self.db.execute(stmt_exits)
        total_exits = res_exits.scalar() or 0

        return people_in_store, total_entries, total_exits

    async def get_people_in_store(
        self,
        branch_id: str | None = None,
    ) -> int:
        conditions = [
            Entry.entry_time.is_not(None),
            Entry.exit_time.is_(None),
        ]
        if branch_id is not None:
            conditions.append(Entry.branch_id == branch_id)
        stmt = select(func.count()).select_from(Entry).where(and_(*conditions))
        res = await self.db.execute(stmt)
        return res.scalar() or 0

    async def get_total_entries_today(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
    ) -> int:
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)
        conditions = [
            Entry.entry_time.is_not(None),
            Entry.entry_time >= start,
            Entry.entry_time <= end,
        ]
        if branch_id is not None:
            conditions.append(Entry.branch_id == branch_id)
        stmt = select(func.count()).select_from(Entry).where(and_(*conditions))
        res = await self.db.execute(stmt)
        return res.scalar() or 0

    async def get_total_exits_today(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
    ) -> int:
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)
        conditions = [
            Entry.exit_time.is_not(None),
            Entry.exit_time >= start,
            Entry.exit_time <= end,
        ]
        if branch_id is not None:
            conditions.append(Entry.branch_id == branch_id)
        stmt = select(func.count()).select_from(Entry).where(and_(*conditions))
        res = await self.db.execute(stmt)
        return res.scalar() or 0

    # ==========================================================
    # 2. EMOTION TRANSITIONS
    # ==========================================================

    async def get_emotion_transitions(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
    ) -> EmotionTransitions:
        """
        Computes sentiment transition counts between entry and exit:
          1. natural -> angry
          2. angry -> natural
          3. natural -> natural
          4. angry -> angry
        Normalizes 'neutral' to 'natural' and handles case insensitivity.
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)

        lower_enter = func.lower(Entry.enter_emotion)
        lower_exit = func.lower(Entry.exit_emotion)

        is_enter_natural = lower_enter.in_(["natural", "neutral"])
        is_enter_angry = lower_enter.in_(["angry", "anger"])
        is_exit_natural = lower_exit.in_(["natural", "neutral"])
        is_exit_angry = lower_exit.in_(["angry", "anger"])

        stmt = select(
            func.count().filter(and_(is_enter_natural, is_exit_angry)).label("natural_to_angry"),
            func.count().filter(and_(is_enter_angry, is_exit_natural)).label("angry_to_natural"),
            func.count().filter(and_(is_enter_natural, is_exit_natural)).label("natural_to_natural"),
            func.count().filter(and_(is_enter_angry, is_exit_angry)).label("angry_to_angry"),
        ).where(
            and_(
                Entry.exit_time.is_not(None),
                Entry.enter_emotion.is_not(None),
                Entry.exit_emotion.is_not(None),
                Entry.entry_time >= start,
                Entry.entry_time <= end,
                *( [Entry.branch_id == branch_id] if branch_id is not None else [] ),
            )
        )

        res = await self.db.execute(stmt)
        row = res.one_or_none()
        if row:
            return EmotionTransitions(
                natural_to_angry=row.natural_to_angry or 0,
                angry_to_natural=row.angry_to_natural or 0,
                natural_to_natural=row.natural_to_natural or 0,
                angry_to_angry=row.angry_to_angry or 0,
            )
        return EmotionTransitions()

    # ==========================================================
    # 3. LONGEST CUSTOMER STAY
    # ==========================================================

    async def get_longest_stay(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
    ) -> LongestStay | None:
        """
        Finds the customer session that had the longest duration inside the store.
        Only completed sessions (exit_time IS NOT NULL) are considered.
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)

        conditions = [
            Entry.entry_time.is_not(None),
            Entry.exit_time.is_not(None),
            Entry.exit_time >= Entry.entry_time,
            Entry.entry_time >= start,
            Entry.entry_time <= end,
        ]
        if branch_id is not None:
            conditions.append(Entry.branch_id == branch_id)

        # Order by duration descending
        stmt = (
            select(Entry)
            .where(and_(*conditions))
            .order_by((Entry.exit_time - Entry.entry_time).desc())
            .limit(1)
        )

        res = await self.db.execute(stmt)
        entry = res.scalar_one_or_none()

        if entry is None or entry.entry_time is None or entry.exit_time is None:
            return None

        duration_sec = (entry.exit_time - entry.entry_time).total_seconds()
        return LongestStay(
            entry_time=entry.entry_time.isoformat(),
            exit_time=entry.exit_time.isoformat(),
            duration_seconds=round(duration_sec, 2),
            customer_id=str(entry.uuid),
            entry_count=entry.entry_count,
            branch_id=entry.branch_id,
            camera_id=entry.camera_id,
        )

    # ==========================================================
    # 4. OCCUPANCY OVER TIME & HIGHEST OCCUPANCY PERIOD
    # ==========================================================

    @staticmethod
    def _parse_bucket_interval(bucket: str) -> tuple[str, timedelta]:
        """
        Normalizes bucket string into PostgreSQL interval string and Python timedelta.
        Supported: 5m, 15m, 30m, 1h (and common variations).
        """
        b = bucket.strip().lower()
        if b in ("5m", "5min", "5mins", "5 minutes", "5 minute"):
            return "5 minutes", timedelta(minutes=5)
        elif b in ("15m", "15min", "15mins", "15 minutes", "15 minute"):
            return "15 minutes", timedelta(minutes=15)
        elif b in ("30m", "30min", "30mins", "30 minutes", "30 minute"):
            return "30 minutes", timedelta(minutes=30)
        elif b in ("1h", "1hr", "1hour", "60m", "1 hour", "1 hours"):
            return "1 hour", timedelta(hours=1)
        else:
            # default to 1 hour
            return "1 hour", timedelta(hours=1)

    async def get_occupancy_timeline(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
        bucket: str = "1h",
    ) -> OccupancyTimelineResponse:
        """
        Calculates occupancy per time bucket across the entire day using SQL aggregation.
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        interval_str, delta = self._parse_bucket_interval(bucket)
        start_of_day = datetime.combine(target_date, time.min)
        end_of_day = datetime.combine(target_date, time.max)

        branch_filter = "AND e.branch_id = :branch_id" if branch_id is not None else ""

        query = text(f"""
            SELECT
                ts.slot_start,
                ts.slot_end,
                COUNT(e.uuid) AS occupancy
            FROM (
                SELECT
                    s AS slot_start,
                    s + INTERVAL '{interval_str}' AS slot_end
                FROM generate_series(
                    CAST(:start_dt AS timestamp),
                    CAST(:end_dt AS timestamp) - INTERVAL '{interval_str}',
                    INTERVAL '{interval_str}'
                ) AS s
            ) ts
            LEFT JOIN entries e
                ON e.entry_time < ts.slot_end
               AND (e.exit_time IS NULL OR e.exit_time >= ts.slot_start)
               AND e.entry_time >= CAST(:start_dt AS timestamp) - INTERVAL '1 day'
               {branch_filter}
            GROUP BY ts.slot_start, ts.slot_end
            ORDER BY ts.slot_start ASC;
        """)

        params: dict[str, Any] = {
            "start_dt": start_of_day,
            "end_dt": end_of_day,
        }
        if branch_id is not None:
            params["branch_id"] = branch_id

        result = await self.db.execute(query, params)
        rows = result.fetchall()

        timeline: list[OccupancyBucket] = []
        peak_period: HighestOccupancyPeriod | None = None
        max_occupancy = -1

        for row in rows:
            slot_start = row.slot_start
            slot_end = row.slot_end
            occupancy = row.occupancy or 0

            start_iso = slot_start.isoformat() if hasattr(slot_start, "isoformat") else str(slot_start)
            end_iso = slot_end.isoformat() if hasattr(slot_end, "isoformat") else str(slot_end)

            bucket_item = OccupancyBucket(
                start=start_iso,
                end=end_iso,
                occupancy=occupancy,
            )
            timeline.append(bucket_item)

            if occupancy > max_occupancy and occupancy > 0:
                max_occupancy = occupancy
                peak_period = HighestOccupancyPeriod(
                    start=start_iso,
                    end=end_iso,
                    occupancy=occupancy,
                )

        return OccupancyTimelineResponse(
            bucket=bucket,
            date=target_date.isoformat(),
            branch_id=branch_id,
            peak_period=peak_period,
            timeline=timeline,
        )

    async def get_highest_occupancy_period(
        self,
        branch_id: str | None = None,
        target_date: date | None = None,
        bucket: str = "1h",
    ) -> HighestOccupancyPeriod | None:
        """
        Quickly queries the single peak occupancy period for today using SQL aggregation.
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        interval_str, delta = self._parse_bucket_interval(bucket)
        start_of_day = datetime.combine(target_date, time.min)
        end_of_day = datetime.combine(target_date, time.max)

        branch_filter = "AND e.branch_id = :branch_id" if branch_id is not None else ""

        query = text(f"""
            SELECT
                ts.slot_start,
                ts.slot_end,
                COUNT(e.uuid) AS occupancy
            FROM (
                SELECT
                    s AS slot_start,
                    s + INTERVAL '{interval_str}' AS slot_end
                FROM generate_series(
                    CAST(:start_dt AS timestamp),
                    CAST(:end_dt AS timestamp) - INTERVAL '{interval_str}',
                    INTERVAL '{interval_str}'
                ) AS s
            ) ts
            LEFT JOIN entries e
                ON e.entry_time < ts.slot_end
               AND (e.exit_time IS NULL OR e.exit_time >= ts.slot_start)
               AND e.entry_time >= CAST(:start_dt AS timestamp) - INTERVAL '1 day'
               {branch_filter}
            GROUP BY ts.slot_start, ts.slot_end
            HAVING COUNT(e.uuid) > 0
            ORDER BY occupancy DESC, ts.slot_start ASC
            LIMIT 1;
        """)

        params: dict[str, Any] = {
            "start_dt": start_of_day,
            "end_dt": end_of_day,
        }
        if branch_id is not None:
            params["branch_id"] = branch_id

        result = await self.db.execute(query, params)
        row = result.first()

        if row is None or row.occupancy == 0:
            return None

        slot_start = row.slot_start
        slot_end = row.slot_end
        start_iso = slot_start.isoformat() if hasattr(slot_start, "isoformat") else str(slot_start)
        end_iso = slot_end.isoformat() if hasattr(slot_end, "isoformat") else str(slot_end)

        return HighestOccupancyPeriod(
            start=start_iso,
            end=end_iso,
            occupancy=row.occupancy,
        )
