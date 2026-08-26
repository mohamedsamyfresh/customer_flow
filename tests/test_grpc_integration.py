from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.grpc import detection_pb2
from app.models.customer_flow import Entry
from app.models.waiting_time import WaitingTime
from app.services.detection_service import DetectionService


@pytest.mark.asyncio
async def test_grpc_detection_service_customer_flow_and_waiting(db_session: AsyncSession):
    service = DetectionService(db_session)
    now = datetime.now(timezone.utc)

    # 1. Entry only message
    msg1 = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            entry_time=now.isoformat(),
            entry_count=1001,
            age_class="18-25",
            gender="male",
            gender_conf=0.91,
            enter_emotion="natural",
            enter_emotion_conf=0.88,
            entry_face_box=[50.0, 60.0, 150.0, 160.0],
            entry_face_vector=[0.1, 0.2, 0.3, 0.4],
        )
    )
    record1 = await service.process(msg1)
    await db_session.commit()
    assert isinstance(record1, Entry)
    assert record1.entry_count == 1001
    assert record1.exit_time is None

    # 2. Exit update correlated by exit_count == 1001
    msg2 = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            exit_time=now.isoformat(),
            exit_count=1001,
            exit_emotion="angry",
            exit_emotion_conf=0.85,
            exit_face_box=[52.0, 62.0, 152.0, 162.0],
            exit_face_vector=[0.11, 0.21, 0.31, 0.41],
            face_match_score=0.95,
        )
    )
    record2 = await service.process(msg2)
    await db_session.commit()
    assert isinstance(record2, Entry)
    assert record2.uuid == record1.uuid
    assert record2.exit_count == 1001
    assert record2.exit_emotion == "angry"

    # 3. Complete entry + exit in single message
    msg3 = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            entry_time=now.isoformat(),
            entry_count=1002,
            enter_emotion="angry",
            exit_time=now.isoformat(),
            exit_count=1002,
            exit_emotion="natural",
        )
    )
    record3 = await service.process(msg3)
    await db_session.commit()
    assert isinstance(record3, Entry)
    assert record3.entry_count == 1002
    assert record3.exit_count == 1002

    # 4. Waiting session message
    msg4 = detection_pb2.DetectionMessage(
        waiting_session=detection_pb2.WaitingSession(
            id=99,
            entry_frame=100,
            exit_frame=300,
            entry_time="10:00:00",
            exit_time="10:05:00",
            duration="00:05:00",
            duration_s=300.0,
        )
    )
    record4 = await service.process(msg4)
    await db_session.commit()
    assert isinstance(record4, WaitingTime)
    assert record4.id == 99
    assert float(record4.duration_s) == 300.0
