import uuid as uuid_module

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class WaitingTime(Base):
    __tablename__ = "waiting_times"

    uuid: Mapped[uuid_module.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid_module.uuid4,
        nullable=False,
    )

    id: Mapped[int | None] = mapped_column(Integer)

    entry_frame: Mapped[int | None] = mapped_column(Integer)

    exit_frame: Mapped[int | None] = mapped_column(Integer)

    entry_time: Mapped[str | None] = mapped_column(
        String(20)
    )

    exit_time: Mapped[str | None] = mapped_column(
        String(20)
    )

    duration: Mapped[str | None] = mapped_column(
        String(20)
    )

    duration_s: Mapped[float | None] = mapped_column(
        Numeric
    )