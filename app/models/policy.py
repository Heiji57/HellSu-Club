from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, pk


class ClubPolicy(Base):
    """주간 목표 정책. INSERT만 한다.

    어떤 주의 목표는 effective_from ≤ 주 시작일인 정책 중 가장 최근에 만든 것이다.
    """

    __tablename__ = "club_policy"
    __table_args__ = (
        CheckConstraint("weekly_target BETWEEN 1 AND 7", name="weekly_target"),
        CheckConstraint("EXTRACT(ISODOW FROM effective_from) = 1", name="effective_from_monday"),
    )

    id: Mapped[int] = pk()
    weekly_target: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_discord_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at()
