from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, pk


class AttendanceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class Attendance(Base):
    """승인된 인증 1건에 출석 1건. 주간 횟수는 저장하지 않고 이 테이블에서 계산한다."""

    __tablename__ = "attendance"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="status"),
        CheckConstraint(
            "(status = 'ACTIVE') = (revoked_at IS NULL)", name="revoked_at_matches_status"
        ),
        # 하루 1회 출석. 취소된 날은 다시 출석할 수 있다.
        Index(
            "uq_attendance_member_day_active",
            "member_id",
            "workout_date",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_attendance_workout_date", "workout_date"),
    )

    id: Mapped[int] = pk()
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), nullable=False)
    verification_id: Mapped[int] = mapped_column(
        ForeignKey("verification.id"), unique=True, nullable=False
    )
    workout_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
