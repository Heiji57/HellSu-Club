from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.week import Week
from app.models import Member, MemberStatus


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_discord_id(self, discord_user_id: int, *, lock: bool = False) -> Member | None:
        stmt = select(Member).where(Member.discord_user_id == discord_user_id)
        if lock:
            stmt = stmt.with_for_update()
        return await self._s.scalar(stmt)

    async def insert_if_absent(
        self, discord_user_id: int, display_name: str, joined_at: datetime
    ) -> int | None:
        """없으면 ACTIVE로 추가하고 새 id를 반환. 이미 있으면 None. 동시 요청에도 안전하다."""
        stmt = (
            insert(Member)
            .values(
                discord_user_id=discord_user_id,
                display_name=display_name,
                status=MemberStatus.ACTIVE,
                joined_at=joined_at,
            )
            .on_conflict_do_nothing(index_elements=[Member.discord_user_id])
            .returning(Member.id)
        )
        return await self._s.scalar(stmt)

    async def list_all(self) -> Sequence[Member]:
        stmt = select(Member).order_by(Member.status, Member.display_name)
        return (await self._s.scalars(stmt)).all()

    async def list_active(self, *, lock: bool = False) -> Sequence[Member]:
        stmt = select(Member).where(Member.status == MemberStatus.ACTIVE)
        if lock:
            stmt = stmt.with_for_update()
        return (await self._s.scalars(stmt)).all()

    async def count_active(self) -> int:
        stmt = select(func.count()).select_from(Member).where(Member.status == MemberStatus.ACTIVE)
        return await self._s.scalar(stmt) or 0

    async def counted_in_week(self, week: Week) -> Sequence[Member]:
        """그 주의 집계 대상: 주가 끝나는 시점에 활동 중인 회원 (REQUIREMENTS 7).

        joined_at < 주 종료 AND (deactivated_at IS NULL OR deactivated_at >= 주 종료).
        주중에 비활성화된 회원은 빠지고, 주중에 가입한 회원은 들어간다.
        """
        _, end = week.bounds_utc()
        stmt = (
            select(Member)
            .where(
                Member.joined_at < end,
                or_(Member.deactivated_at.is_(None), Member.deactivated_at >= end),
            )
            .order_by(Member.display_name, Member.id)
        )
        return (await self._s.scalars(stmt)).all()
