"""주간 목표 정책.

목표 조회, 초기 시드, /목표 안내, 목표 변경을 제공한다.
정책은 INSERT만 한다. 지난 주의 목표는 바뀌지 않는다 (REQUIREMENTS 6.3).
"""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.database import Database
from app.core.week import Week, to_kst_date
from app.repositories.audit import AuditRepository
from app.repositories.policy import PolicyRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetOverview:
    this_week: Week
    this_target: int
    next_week: Week
    next_target: int

    @property
    def changes_next_week(self) -> bool:
        return self.next_target != self.this_target


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

    async def overview(self) -> TargetOverview:
        """이번 주 목표와 (예약된 변경이 있다면 반영한) 다음 주 목표."""
        this_week = Week.of(self.today())
        next_week = this_week.next()
        async with self._db.transaction() as s:
            this_target = await target_for_week(s, this_week, self._initial)
            next_target = await target_for_week(s, next_week, self._initial)
        return TargetOverview(this_week, this_target, next_week, next_target)

    async def set_target(
        self, weekly_target: int, *, this_week: bool, actor_discord_id: int
    ) -> Week:
        """목표를 바꾸고 적용 주를 반환한다.

        기본은 다음 주 월요일부터, this_week면 이번 주 월요일부터 적용한다.
        """
        if not 1 <= weekly_target <= 7:
            raise ValueError("주간 목표는 1~7회입니다")
        week = Week.of(self.today())
        if not this_week:
            week = week.next()
        async with self._db.transaction() as s:
            previous = await target_for_week(s, week, self._initial)
            policy = await PolicyRepository(s).add(
                weekly_target=weekly_target,
                effective_from=week.start,
                created_by_discord_id=actor_discord_id,
            )
            await AuditRepository(s).record(
                actor_discord_id=actor_discord_id,
                action="POLICY_CHANGED",
                target_type="club_policy",
                target_id=policy.id,
                details={
                    "weekly_target": weekly_target,
                    "previous_target": previous,
                    "effective_from": week.start.isoformat(),
                },
            )
        log.info(
            "주간 목표 %d회로 변경 (%s부터, by %s)", weekly_target, week.start, actor_discord_id
        )
        return week

    def today(self) -> date:
        return to_kst_date(self._clock.now())
