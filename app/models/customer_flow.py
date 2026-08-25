import uuid as uuid_module
from datetime import datetime

from sqlalchemy import Integer, Numeric, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class Entry(Base):
    __tablename__ = "entries"

    uuid: Mapped[uuid_module.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid_module.uuid4,
        nullable=False,
    )

    entry_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP
    )

    entry_count: Mapped[int | None] = mapped_column(
        Integer
    )

    age_class: Mapped[str | None] = mapped_column(
        String(20)
    )

    gender: Mapped[str | None] = mapped_column(
        String(10)
    )

    gender_conf: Mapped[float | None] = mapped_column(
        Numeric
    )

    enter_emotion: Mapped[str | None] = mapped_column(
        String(20)
    )

    enter_emotion_conf: Mapped[float | None] = mapped_column(
        Numeric
    )

    entry_face_box: Mapped[str | None] = mapped_column(
        Text
    )

    entry_face_vector: Mapped[str | None] = mapped_column(
        Text
    )

    exit_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP
    )

    exit_count: Mapped[int | None] = mapped_column(
        Integer
    )

    exit_emotion: Mapped[str | None] = mapped_column(
        String(20)
    )

    exit_emotion_conf: Mapped[float | None] = mapped_column(
        Numeric
    )

    exit_face_box: Mapped[str | None] = mapped_column(
        Text
    )

    exit_face_vector: Mapped[str | None] = mapped_column(
        Text
    )

    face_match_score: Mapped[float | None] = mapped_column(
        Numeric
    )