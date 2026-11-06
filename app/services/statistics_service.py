"""주간 집계와 개인 기록 조회 (REQUIREMENTS 5, 7).

주간 횟수는 저장하지 않고 매번 attendance에서 계산한다. 출석 취소나 목표 변경이 바로 반영된다.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.database import Database
from app.core.exceptions import NotAMember
from app.core.week import Week, to_kst_date
from app.models import Member, ReviewDecision, VerificationStatus
from app.repositories.attendance import AttendanceRepository
from app.repositories.member import MemberRepository
from app.repositories.verification import VerificationRepository
from app.services.policy_service import target_for_week

HISTORY_PAGE_SIZE = 5


@dataclass(frozen=True)
class MemberWeek:
    member_id: int
    discord_user_id: int
    display_name: str
    count: int
    target: int

    @property
    def achieved(self) -> bool:
        return self.count >= self.target


@dataclass(frozen=True)
class WeekStats:
    week: Week
    target: int
    rows: tuple[MemberWeek, ...]  # 달성 여부 → 횟수 → 이름 순
    pending_reviews: int

    @property
    def member_count(self) -> int:
        return len(self.rows)

    @property
    def total_attendance(self) -> int:
        return sum(r.count for r in self.rows)

    @property
    def achieved_count(self) -> int:
        return sum(1 for r in self.rows if r.achieved)

    @property
    def achievement_rate(self) -> float:
        return self.achieved_count / self.member_count if self.rows else 0.0


@dataclass(frozen=True)
class MyWeek:
    week: Week
    today: date
    target: int
    days: tuple[date, ...]  # 출석한 날
    pending_reviews: int

    @property
    def count(self) -> int:
        return len(self.days)

    @property
    def remaining(self) -> int:
        return max(self.target - self.count, 0)

    @property
    def achieved(self) -> bool:
        return self.count >= self.target

    @property
    def days_left(self) -> int:
        """오늘을 포함해 이번 주에 남은 날."""
        return (self.week.end - self.today).days + 1 if self.today in self.week else 0


@dataclass(frozen=True)
class HistoryItem:
    verification_id: int
    workout_date: date
    submitted_at: datetime
    content: str | None
    has_image: bool
    status: VerificationStatus
    revoked: bool  # 승인 후 관리자가 출석을 취소함
    reason: str | None  # 거절·취소 사유


@dataclass(frozen=True)
class HistoryPage:
    items: tuple[HistoryItem, ...]
    page: int  # 1부터
    total: int
    per_page: int = HISTORY_PAGE_SIZE

    @property
    def total_pages(self) -> int:
        return max(math.ceil(self.total / self.per_page), 1)


def rank_rows(rows: list[MemberWeek]) -> tuple[MemberWeek, ...]:
    return tuple(sorted(rows, key=lambda r: (not r.achieved, -r.count, r.display_name)))


class StatisticsService:
    def __init__(self, db: Database, clock: Clock, *, default_weekly_target: int) -> None:
        self._db = db
        self._clock = clock
        self._default_target = default_weekly_target

    def current_week(self) -> Week:
        return Week.containing(self._clock.now())

    async def week_stats(self, week: Week | None = None) -> WeekStats:
        week = week or self.current_week()
        async with self._db.transaction() as s:
            target = await target_for_week(s, week, self._default_target)
            members = await MemberRepository(s).counted_in_week(week)
            counts = await AttendanceRepository(s).counts_by_member_in_week(week)
            pending = await VerificationRepository(s).count_pending(week=week)
        rows = [
            MemberWeek(m.id, m.discord_user_id, m.display_name, counts.get(m.id, 0), target)
            for m in members
        ]
        return WeekStats(week, target, rank_rows(rows), pending)

    async def my_week(self, discord_user_id: int, week: Week | None = None) -> MyWeek:
        week = week or self.current_week()
        async with self._db.transaction() as s:
            member = await self._member(s, discord_user_id)
            days = await AttendanceRepository(s).active_days_in_week(member.id, week)
            pending = await VerificationRepository(s).count_pending(week=week, member_id=member.id)
            target = await target_for_week(s, week, self._default_target)
        return MyWeek(week, to_kst_date(self._clock.now()), target, tuple(days), pending)

    async def history(self, discord_user_id: int, page: int = 1) -> HistoryPage:
        async with self._db.transaction() as s:
            member = await self._member(s, discord_user_id)
            repo = VerificationRepository(s)
            total = await repo.count_for_member(member.id)
            pages = max(math.ceil(total / HISTORY_PAGE_SIZE), 1)
            page = min(max(page, 1), pages)
            rows = await repo.page_for_member(
                member.id, offset=(page - 1) * HISTORY_PAGE_SIZE, limit=HISTORY_PAGE_SIZE
            )
            reviews = await repo.latest_reviews(v.id for v in rows)

        items = []
        for v in rows:
            review = reviews.get(v.id)
            status = VerificationStatus(v.status)
            revoked = review is not None and review.decision == ReviewDecision.REVOKE
            reason = review.reason if status is VerificationStatus.REJECTED and review else None
            items.append(
                HistoryItem(
                    verification_id=v.id,
                    workout_date=v.workout_date,
                    submitted_at=v.submitted_at,
                    content=v.content,
                    has_image=v.has_image,
                    status=status,
                    revoked=revoked,
                    reason=reason,
                )
            )
        return HistoryPage(tuple(items), page, total)

    @staticmethod
    async def _member(s: AsyncSession, discord_user_id: int) -> Member:
        member = await MemberRepository(s).get_by_discord_id(discord_user_id)
        if member is None:
            raise NotAMember()
        return member
