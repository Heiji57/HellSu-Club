"""출석 생성과 조회.

자동 승인과 관리자 승인이 같은 `create`를 쓴다(ARCHITECTURE 4.2). 호출하는 쪽의
트랜잭션 안에서 실행되어 인증 상태 변경과 함께 커밋되거나 함께 롤백된다.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Database
from app.core.week import Week
from app.models import Attendance, Verification
from app.repositories.attendance import AttendanceRepository


class AttendanceService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, s: AsyncSession, verification: Verification) -> Attendance:
        """출석일은 인증의 workout_date다(승인 시점이 아니다)."""
        return await AttendanceRepository(s).add(
            member_id=verification.member_id,
            verification_id=verification.id,
            workout_date=verification.workout_date,
        )

    async def count_in_week(self, member_id: int, week: Week) -> int:
        async with self._db.transaction() as s:
            return await AttendanceRepository(s).count_active_in_week(member_id, week)
