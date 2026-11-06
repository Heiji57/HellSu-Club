from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, pk


class WeeklyReport(Base):
    """주간 리포트 게시 기록 (REQUIREMENTS 8).

    week_start UNIQUE로 같은 주를 두 번 게시하지 않는다. 행을 먼저 만든(선점) 뒤 게시하고,
    게시에 성공하면 message_id와 posted_at을 채운다. snapshot은 선점할 때의 집계 결과다.
    """

    __tablename__ = "weekly_report"
    __table_args__ = (
        CheckConstraint("EXTRACT(ISODOW FROM week_start) = 1", name="week_start_monday"),
        CheckConstraint(
            "(posted_at IS NULL) = (message_id IS NULL)", name="posted_matches_message"
        ),
    )

    id: Mapped[int] = pk()
    week_start: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
