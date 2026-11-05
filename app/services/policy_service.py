"""주간 목표 정책.

Phase 2에서는 인증 결과에 이번 주 진행 현황을 보여주기 위한 조회와 초기 시드만 둔다.
목표 변경(이번 주/다음 주 적용)은 Phase 4에서 추가한다.
"""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.database import Database
from app.core.week import Week, to_kst_date
from app.repositories.policy import PolicyRepository

log = logging.getLogger(__name__)


async def target_for_week(s: AsyncSession, week: Week, default: int) -> int:
    """그 주의 목표. 첫 정책보다 이전 주는 첫 정책의 목표를 쓴다."""
    repo = PolicyRepository(s)
    policy = await repo.effective_for(week.start) or await repo.earliest()
    return policy.weekly_target if policy else default


class PolicyService:
    def __init__(self, db: Database, clock: Clock, *, initial_weekly_target: int) -> None:
        self._db = db
        self._clock = clock
        self._initial = initial_weekly_target

    async def ensure_initial(self) -> bool:
        """정책이 하나도 없으면 INITIAL_WEEKLY_TARGET을 이번 주부터 적용하는 정책을 만든다.

        시드를 DB에 남겨 두면 나중에 환경변수를 바꿔도 지난 주의 목표가 바뀌지 않는다.
        """
        async with self._db.transaction() as s:
            repo = PolicyRepository(s)
            await repo.lock_for_seed()
            if await repo.any_exists():
                return False
            start = Week.of(self.today()).start
            await repo.add(
                weekly_target=self._initial, effective_from=start, created_by_discord_id=None
            )
        log.info("초기 주간 목표 %d회 (%s부터)", self._initial, start)
        return True

    async def target_for(self, week: Week) -> int:
        async with self._db.transaction() as s:
            return await target_for_week(s, week, self._initial)

    def today(self) -> date:
        return to_kst_date(self._clock.now())
