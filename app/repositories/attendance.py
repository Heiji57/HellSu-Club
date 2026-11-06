from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.week import Week
from app.models import Attendance, AttendanceStatus


class AttendanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, *, member_id: int, verification_id: int, workout_date: date) -> Attendance:
        attendance = Attendance(
            member_id=member_id,
            verification_id=verification_id,
            workout_date=workout_date,
            status=AttendanceStatus.ACTIVE,
        )
        self._s.add(attendance)
        await self._s.flush()
        return attendance

    async def count_active_in_week(self, member_id: int, week: Week) -> int:
        stmt = (
            select(func.count())
            .select_from(Attendance)
            .where(
                Attendance.member_id == member_id,
                Attendance.status == AttendanceStatus.ACTIVE,
                Attendance.workout_date.between(week.start, week.end),
            )
        )
        return await self._s.scalar(stmt) or 0

    async def active_days_in_week(self, member_id: int, week: Week) -> list[date]:
        stmt = (
            select(Attendance.workout_date)
            .where(
                Attendance.member_id == member_id,
                Attendance.status == AttendanceStatus.ACTIVE,
                Attendance.workout_date.between(week.start, week.end),
            )
            .order_by(Attendance.workout_date)
        )
        return list((await self._s.scalars(stmt)).all())

    async def counts_by_member_in_week(self, week: Week) -> dict[int, int]:
        stmt = (
            select(Attendance.member_id, func.count())
            .where(
                Attendance.status == AttendanceStatus.ACTIVE,
                Attendance.workout_date.between(week.start, week.end),
            )
            .group_by(Attendance.member_id)
        )
        return {member_id: n for member_id, n in await self._s.execute(stmt)}
