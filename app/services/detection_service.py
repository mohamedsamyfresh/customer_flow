from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grpc import detection_pb2
from app.models.customer_flow import Entry
from app.models.waiting_time import WaitingTime


class DetectionService:
    """
    Processes detection messages received from the ML service.

    The database session is injected through the constructor.
    Transaction ownership belongs to the caller.
    This service only performs add/update/flush operations.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def process(
        self,
        message: detection_pb2.DetectionMessage,
    ) -> Entry | WaitingTime:
        if message.HasField("customer_flow"):
            return await self._process_customer_flow(message.customer_flow)

        if message.HasField("waiting_session"):
            return await self._process_waiting_session(message.waiting_session)

        raise ValueError("DetectionMessage does not contain a payload")

    # ==========================================================
    # CUSTOMER FLOW / ENTRIES
    # ==========================================================

    async def _process_customer_flow(
        self,
        data: detection_pb2.CustomerFlow,
    ) -> Entry:
        has_entry = bool(data.entry_time or data.entry_count != 0)
        has_exit = bool(data.exit_time or data.exit_count != 0)

        if not has_entry and not has_exit:
            raise ValueError("CustomerFlow message contains neither entry nor exit data")

        # ------------------------------------------------------
        # Case 1: Exit message arriving later (or exit-only message)
        # ------------------------------------------------------
        if has_exit and not has_entry:
            if not data.exit_count:
                raise ValueError("Exit CustomerFlow message must provide exit_count for correlation")

            entry = await self._find_entry_by_count(data.exit_count)
            if entry is None:
                raise ValueError(f"No matching entry found for exit_count {data.exit_count}")

            self._apply_exit_data(entry, data)
            await self.db.flush()
            return entry

        # ------------------------------------------------------
        # Case 2: Complete message (both entry and exit data)
        # ------------------------------------------------------
        if has_entry and has_exit:
            entry = None
            if data.exit_count:
                entry = await self._find_entry_by_count(data.exit_count)
            elif data.entry_count:
                entry = await self._find_entry_by_count(data.entry_count)

            if entry is not None:
                self._apply_entry_data(entry, data)
                self._apply_exit_data(entry, data)
                await self.db.flush()
                return entry

            entry = self._create_complete_entry(data)
            self.db.add(entry)
            await self.db.flush()
            return entry

        # ------------------------------------------------------
        # Case 3: Entry-only message
        # ------------------------------------------------------
        entry = self._create_entry_only(data)
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def _find_entry_by_count(
        self,
        count: int,
    ) -> Entry | None:
        result = await self.db.execute(
            select(Entry)
            .where(Entry.entry_count == count)
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _create_entry_only(
        self,
        data: detection_pb2.CustomerFlow,
    ) -> Entry:
        return Entry(
            entry_time=self._parse_timestamp(data.entry_time),
            entry_count=data.entry_count if data.entry_count != 0 else None,
            age_class=self._string_or_none(data.age_class),
            gender=self._string_or_none(data.gender),
            gender_conf=self._float_or_none(data.gender_conf),
            enter_emotion=self._string_or_none(data.enter_emotion),
            enter_emotion_conf=self._float_or_none(data.enter_emotion_conf),
            entry_face_box=self._repeated_float_to_json(data.entry_face_box),
            entry_face_vector=self._repeated_float_to_json(data.entry_face_vector),
            exit_time=None,
            exit_count=None,
            exit_emotion=None,
            exit_emotion_conf=None,
            exit_face_box=None,
            exit_face_vector=None,
            face_match_score=None,
        )

    def _create_complete_entry(
        self,
        data: detection_pb2.CustomerFlow,
    ) -> Entry:
        return Entry(
            entry_time=self._parse_timestamp(data.entry_time),
            entry_count=data.entry_count if data.entry_count != 0 else None,
            age_class=self._string_or_none(data.age_class),
            gender=self._string_or_none(data.gender),
            gender_conf=self._float_or_none(data.gender_conf),
            enter_emotion=self._string_or_none(data.enter_emotion),
            enter_emotion_conf=self._float_or_none(data.enter_emotion_conf),
            entry_face_box=self._repeated_float_to_json(data.entry_face_box),
            entry_face_vector=self._repeated_float_to_json(data.entry_face_vector),
            exit_time=self._parse_timestamp(data.exit_time),
            exit_count=data.exit_count if data.exit_count != 0 else None,
            exit_emotion=self._string_or_none(data.exit_emotion),
            exit_emotion_conf=self._float_or_none(data.exit_emotion_conf),
            exit_face_box=self._repeated_float_to_json(data.exit_face_box),
            exit_face_vector=self._repeated_float_to_json(data.exit_face_vector),
            face_match_score=self._float_or_none(data.face_match_score),
        )

    def _apply_entry_data(
        self,
        entry: Entry,
        data: detection_pb2.CustomerFlow,
    ) -> None:
        if data.entry_time:
            entry.entry_time = self._parse_timestamp(data.entry_time)
        if data.entry_count != 0:
            entry.entry_count = data.entry_count
        if data.age_class:
            entry.age_class = data.age_class
        if data.gender:
            entry.gender = data.gender
        if data.gender_conf != 0:
            entry.gender_conf = data.gender_conf
        if data.enter_emotion:
            entry.enter_emotion = data.enter_emotion
        if data.enter_emotion_conf != 0:
            entry.enter_emotion_conf = data.enter_emotion_conf
        if data.entry_face_box:
            entry.entry_face_box = self._repeated_float_to_json(data.entry_face_box)
        if data.entry_face_vector:
            entry.entry_face_vector = self._repeated_float_to_json(data.entry_face_vector)

    def _apply_exit_data(
        self,
        entry: Entry,
        data: detection_pb2.CustomerFlow,
    ) -> None:
        if data.exit_time:
            entry.exit_time = self._parse_timestamp(data.exit_time)
        if data.exit_count != 0:
            entry.exit_count = data.exit_count
        if data.exit_emotion:
            entry.exit_emotion = data.exit_emotion
        if data.exit_emotion_conf != 0:
            entry.exit_emotion_conf = data.exit_emotion_conf
        if data.exit_face_box:
            entry.exit_face_box = self._repeated_float_to_json(data.exit_face_box)
        if data.exit_face_vector:
            entry.exit_face_vector = self._repeated_float_to_json(data.exit_face_vector)
        if data.face_match_score != 0:
            entry.face_match_score = data.face_match_score

    # ==========================================================
    # WAITING SESSION / WAITING TIME
    # ==========================================================

    async def _process_waiting_session(
        self,
        data: detection_pb2.WaitingSession,
    ) -> WaitingTime:
        waiting_time = WaitingTime(
            id=data.id if data.id != 0 else None,
            entry_frame=data.entry_frame if data.entry_frame != 0 else None,
            exit_frame=data.exit_frame if data.exit_frame != 0 else None,
            entry_time=self._string_or_none(data.entry_time),
            exit_time=self._string_or_none(data.exit_time),
            duration=self._string_or_none(data.duration),
            duration_s=self._float_or_none(data.duration_s),
        )
        self.db.add(waiting_time)
        await self.db.flush()
        return waiting_time

    # ==========================================================
    # HELPERS
    # ==========================================================

    @staticmethod
    def _parse_timestamp(
        value: str | None,
    ) -> datetime | None:
        if not value:
            return None
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _string_or_none(
        value: str | None,
    ) -> str | None:
        return value or None

    @staticmethod
    def _float_or_none(
        value: float | None,
    ) -> float | None:
        return value if value and value != 0 else None

    @staticmethod
    def _repeated_float_to_json(
        values: Any,
    ) -> str | None:
        if not values:
            return None
        return json.dumps(list(values))