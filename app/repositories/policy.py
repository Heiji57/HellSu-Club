from datetime import date

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubPolicy


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def effective_for(self, week_start: date) -> ClubPolicy | None:
        """effective_from ≤ 주 시작일인 정책 중 가장 최근에 만든 것 (REQUIREMENTS 6.3)."""
        stmt = (
            select(ClubPolicy)
            .where(ClubPolicy.effective_from <= week_start)
            .order_by(ClubPolicy.created_at.desc(), ClubPolicy.id.desc())
            .limit(1)
        )
        return await self._s.scalar(stmt)

    async def earliest(self) -> ClubPolicy | None:
        stmt = select(ClubPolicy).order_by(ClubPolicy.effective_from, ClubPolicy.id).limit(1)
        return await self._s.scalar(stmt)

    async def any_exists(self) -> bool:
        return bool(await self._s.scalar(select(exists().select_from(ClubPolicy))))

    async def lock_for_seed(self) -> None:
        """초기 정책을 두 번 넣지 않도록 트랜잭션 범위 advisory lock을 잡는다."""
        await self._s.execute(text("SELECT pg_advisory_xact_lock(hashtext('club_policy_seed'))"))

    async def add(
        self, *, weekly_target: int, effective_from: date, created_by_discord_id: int | None
    ) -> ClubPolicy:
        policy = ClubPolicy(
            weekly_target=weekly_target,
            effective_from=effective_from,
            created_by_discord_id=created_by_discord_id,
        )
        self._s.add(policy)
        await self._s.flush()
        return policy
