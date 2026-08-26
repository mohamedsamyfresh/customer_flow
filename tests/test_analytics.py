from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import AnalyticsService
from app.grpc import detection_pb2
from app.services.detection_service import DetectionService


@pytest.mark.asyncio
async def test_entry_lifecycle(db_session: AsyncSession):
    """
    Given customer enters:
    - people_in_store increases
    - total_entries_today increases
    """
    svc = DetectionService(db_session)
    analytics = AnalyticsService(db_session)

    # 1. Initially store is empty
    metrics_before = await analytics.get_dashboard_metrics()
    assert metrics_before.people_in_store == 0
    assert metrics_before.total_entries_today == 0

    # 2. Customer 1 enters
    entry_msg = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            entry_time=datetime.now(timezone.utc).isoformat(),
            entry_count=201,
            age_class="25-35",
            gender="female",
            gender_conf=0.95,
            enter_emotion="natural",
            enter_emotion_conf=0.90,
        )
    )
    await svc.process(entry_msg)
    await db_session.commit()

    # 3. Check metrics
    metrics_after_entry = await analytics.get_dashboard_metrics()
    assert metrics_after_entry.people_in_store == 1
    assert metrics_after_entry.total_entries_today == 1
    assert metrics_after_entry.total_exits_today == 0


@pytest.mark.asyncio
async def test_exit_lifecycle_and_longest_stay(db_session: AsyncSession):
    """
    Given customer exits:
    - people_in_store decreases
    - total_exits_today increases
    - duration is calculated
    - longest stay is updated
    """
    svc = DetectionService(db_session)
    analytics = AnalyticsService(db_session)

    now = datetime.now(timezone.utc)
    entry_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    exit_time = now.replace(hour=11, minute=30, second=0, microsecond=0)

    # 1. Entry
    await svc.process(
        detection_pb2.DetectionMessage(
            customer_flow=detection_pb2.CustomerFlow(
                entry_time=entry_time.isoformat(),
                entry_count=301,
                enter_emotion="natural",
            )
        )
    )
    await db_session.commit()

    # While inside, longest stay should be None (not completed)
    metrics_inside = await analytics.get_dashboard_metrics()
    assert metrics_inside.people_in_store == 1
    assert metrics_inside.longest_stay is None

    # 2. Exit
    await svc.process(
        detection_pb2.DetectionMessage(
            customer_flow=detection_pb2.CustomerFlow(
                exit_time=exit_time.isoformat(),
                exit_count=301,
                exit_emotion="happy",
            )
        )
    )
    await db_session.commit()

    # 3. Check metrics after exit
    metrics_after_exit = await analytics.get_dashboard_metrics()
    assert metrics_after_exit.people_in_store == 0
    assert metrics_after_exit.total_entries_today == 1
    assert metrics_after_exit.total_exits_today == 1
    assert metrics_after_exit.longest_stay is not None
    assert metrics_after_exit.longest_stay.entry_count == 301
    assert metrics_after_exit.longest_stay.duration_seconds == 5400.0  # 1.5 hours = 5400s


@pytest.mark.asyncio
async def test_all_four_emotion_transitions(db_session: AsyncSession):
    """
    Verify all 4 required emotion transitions:
      1. natural -> angry
      2. angry -> natural
      3. natural -> natural
      4. angry -> angry
    """
    svc = DetectionService(db_session)
    analytics = AnalyticsService(db_session)

    now = datetime.now(timezone.utc)

    # 1. natural -> angry
    await svc.process(
        detection_pb2.DetectionMessage(
            customer_flow=detection_pb2.CustomerFlow(
                entry_time=now.isoformat(),
                entry_count=401,
                enter_emotion="natural",
                exit_time=now.isoformat(),
                exit_count=401,
                exit_emotion="angry",
            )
        )
    )

    # 2. angry -> natural (using 'neutral' synonym)
    await svc.process(
        detection_pb2.DetectionMessage(
            customer_flow=detection_pb2.CustomerFlow(
                entry_time=now.isoformat(),
                entry_count=402,
                enter_emotion="angry",
                exit_time=now.isoformat(),
                exit_count=402,
                exit_emotion="neutral",
            )
        )
    )

    # 3. natural -> natural
    await svc.process(
        detection_pb2.DetectionMessage(
            customer_flow=detection_pb2.CustomerFlow(
                entry_time=now.isoformat(),
                entry_count=403,
                enter_emotion="natural",
                exit_time=now.isoformat(),
                exit_count=403,
                exit_emotion="natural",
            )
        )
    )

    # 4. angry -> angry
    await svc.process(
        detection_pb2.DetectionMessage(
            customer_flow=detection_pb2.CustomerFlow(
                entry_time=now.isoformat(),
                entry_count=404,
                enter_emotion="angry",
                exit_time=now.isoformat(),
                exit_count=404,
                exit_emotion="angry",
            )
        )
    )

    await db_session.commit()

    emotions = await analytics.get_emotion_transitions()
    assert emotions.natural_to_angry == 1
    assert emotions.angry_to_natural == 1
    assert emotions.natural_to_natural == 1
    assert emotions.angry_to_angry == 1


@pytest.mark.asyncio
async def test_occupancy_timeline_and_peak(db_session: AsyncSession):
    """
    Test occupancy aggregation across time buckets (1h, 30m, 15m).
    """
    svc = DetectionService(db_session)
    analytics = AnalyticsService(db_session)

    today = datetime.now(timezone.utc).date()
    t1 = datetime(today.year, today.month, today.day, 14, 0, 0)
    t2 = datetime(today.year, today.month, today.day, 15, 0, 0)

    # 3 customers inside between 14:00 and 15:00
    for i in range(3):
        await svc.process(
            detection_pb2.DetectionMessage(
                customer_flow=detection_pb2.CustomerFlow(
                    entry_time=t1.isoformat(),
                    entry_count=500 + i,
                    exit_time=t2.isoformat(),
                    exit_count=500 + i,
                    enter_emotion="natural",
                    exit_emotion="natural",
                )
            )
        )
    await db_session.commit()

    occ_1h = await analytics.get_occupancy_timeline(bucket="1h")
    assert len(occ_1h.timeline) > 0
    assert occ_1h.peak_period is not None
    assert occ_1h.peak_period.occupancy == 3


@pytest.mark.asyncio
async def test_multiple_sequential_detections(db_session: AsyncSession):
    """
    Verify database consistency across rapid sequential detections.
    """
    svc = DetectionService(db_session)
    analytics = AnalyticsService(db_session)

    now = datetime.now(timezone.utc)

    # 10 customers enter
    for i in range(1, 11):
        await svc.process(
            detection_pb2.DetectionMessage(
                customer_flow=detection_pb2.CustomerFlow(
                    entry_time=now.isoformat(),
                    entry_count=600 + i,
                    enter_emotion="natural",
                )
            )
        )
    await db_session.commit()

    m1 = await analytics.get_dashboard_metrics()
    assert m1.people_in_store == 10
    assert m1.total_entries_today == 10
    assert m1.total_exits_today == 0

    # 4 customers exit
    for i in range(1, 5):
        await svc.process(
            detection_pb2.DetectionMessage(
                customer_flow=detection_pb2.CustomerFlow(
                    exit_time=now.isoformat(),
                    exit_count=600 + i,
                    exit_emotion="angry",
                )
            )
        )
    await db_session.commit()

    m2 = await analytics.get_dashboard_metrics()
    assert m2.people_in_store == 6
    assert m2.total_entries_today == 10
    assert m2.total_exits_today == 4
    assert m2.emotion_transitions.natural_to_angry == 4
