"""주간 리포트 (REQUIREMENTS 8).

게시 순서: 선점(행 INSERT + 스냅샷 저장) → Discord 게시 → message_id·posted_at 기록.
- 게시에 실패하면 행은 미게시로 남고, 다음 주기에 **같은 스냅샷**으로 다시 게시한다.
- 같은 주는 week_start UNIQUE로 한 번만 선점된다. 봇은 단일 프로세스이므로(ADR-0001)
  자동 게시와 관리자 수동 게시가 겹치지 않도록 프로세스 안의 Lock으로 직렬화한다.
- 게시 시점이 지난 가장 최근 주를 대상으로 하므로, 봇이 월요일에 꺼져 있었어도 켜진 뒤 보충한다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import Clock
from app.core.database import Database
from app.core.week import KST, Week
from app.models import WeeklyReport
from app.repositories.audit import AuditRepository
from app.services.statistics_service import StatisticsService, WeekStats

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportRow:
    discord_user_id: int
    display_name: str
    count: int
    achieved: bool


@dataclass(frozen=True)
class ReportSnapshot:
    week: Week
    target: int
    rows: tuple[ReportRow, ...]
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

    @classmethod
    def from_stats(cls, stats: WeekStats) -> "ReportSnapshot":
        rows = tuple(
            ReportRow(r.discord_user_id, r.display_name, r.count, r.achieved) for r in stats.rows
        )
        return cls(stats.week, stats.target, rows, stats.pending_reviews)

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_start": self.week.start.isoformat(),
            "target": self.target,
            "pending_reviews": self.pending_reviews,
            "member_count": self.member_count,
            "total_attendance": self.total_attendance,
            "achieved_count": self.achieved_count,
            "rows": [
                {
                    "discord_user_id": r.discord_user_id,
                    "display_name": r.display_name,
                    "count": r.count,
                    "achieved": r.achieved,
                }
                for r in self.rows
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportSnapshot":
        return cls(
            week=Week(date.fromisoformat(data["week_start"])),
            target=data["target"],
            rows=tuple(ReportRow(**r) for r in data["rows"]),
            pending_reviews=data["pending_reviews"],
        )


class PublishStatus(StrEnum):
    POSTED = "POSTED"
    ALREADY_POSTED = "ALREADY_POSTED"
    NO_MEMBERS = "NO_MEMBERS"
    NOT_DUE = "NOT_DUE"


@dataclass(frozen=True)
class PublishResult:
    status: PublishStatus
    week: Week | None
    snapshot: ReportSnapshot | None = None
    message_id: int | None = None


@dataclass(frozen=True)
class ReportPreview:
    snapshot: ReportSnapshot
    posted_at: datetime | None
    message_id: int | None
    channel_id: int | None


Poster = Callable[[ReportSnapshot], Awaitable[int]]
"""스냅샷을 게시하고 메시지 ID를 돌려준다. 실패하면 예외를 던진다."""


class ReportService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        statistics: StatisticsService,
        *,
        channel_id: int,
        report_hour: int,
    ) -> None:
        self._db = db
        self._clock = clock
        self._statistics = statistics
        self._channel_id = channel_id
        self._report_hour = report_hour
        self._lock = asyncio.Lock()

    def due_week(self) -> Week:
        """게시 시각(월요일 REPORT_HOUR KST)이 지난 가장 최근 주."""
        now = self._clock.now()
        this_week = Week.containing(now)
        due_at = datetime.combine(this_week.start, time(self._report_hour), tzinfo=KST)
        last = this_week.previous()
        return last if now >= due_at else last.previous()

    async def publish_due(self, poster: Poster) -> PublishResult:
        return await self.publish(self.due_week(), poster)

    async def publish(
        self, week: Week, poster: Poster, *, actor_discord_id: int | None = None
    ) -> PublishResult:
        if week.start > self.due_week().start:
            return PublishResult(PublishStatus.NOT_DUE, week)  # 아직 끝나지 않은 주
        async with self._lock:
            report = await self._get(week)
            if report is not None and report.posted_at is not None:
                return PublishResult(
                    PublishStatus.ALREADY_POSTED,
                    week,
                    ReportSnapshot.from_dict(report.snapshot),
                    report.message_id,
                )
            if report is None:
                stats = await self._statistics.week_stats(week)
                if not stats.rows:
                    return PublishResult(PublishStatus.NO_MEMBERS, week)
                report = await self._claim(week, ReportSnapshot.from_stats(stats))

            snapshot = ReportSnapshot.from_dict(report.snapshot)
            message_id = await poster(snapshot)  # 실패하면 미게시로 남아 다음에 재시도
            await self._mark_posted(report.id, message_id, actor_discord_id)
        log.info("주간 리포트 게시: %s (message %s)", week.start, message_id)
        return PublishResult(PublishStatus.POSTED, week, snapshot, message_id)

    async def preview(self, week: Week) -> ReportPreview:
        """게시된(또는 선점된) 리포트면 저장된 스냅샷, 아니면 지금 집계한 결과."""
        report = await self._get(week)
        if report is not None:
            return ReportPreview(
                ReportSnapshot.from_dict(report.snapshot),
                report.posted_at,
                report.message_id,
                report.channel_id,
            )
        stats = await self._statistics.week_stats(week)
        return ReportPreview(ReportSnapshot.from_stats(stats), None, None, None)

    async def is_posted(self, week: Week) -> bool:
        report = await self._get(week)
        return report is not None and report.posted_at is not None

    # --- 내부 ---

    async def _get(self, week: Week) -> WeeklyReport | None:
        async with self._db.transaction() as s:
            return await s.scalar(select(WeeklyReport).where(WeeklyReport.week_start == week.start))

    async def _claim(self, week: Week, snapshot: ReportSnapshot) -> WeeklyReport:
        async with self._db.transaction() as s:
            await s.execute(
                insert(WeeklyReport)
                .values(
                    week_start=week.start, channel_id=self._channel_id, snapshot=snapshot.to_dict()
                )
                .on_conflict_do_nothing(index_elements=[WeeklyReport.week_start])
            )
            report = await s.scalar(
                select(WeeklyReport).where(WeeklyReport.week_start == week.start)
            )
        assert report is not None
        return report

    async def _mark_posted(
        self, report_id: int, message_id: int, actor_discord_id: int | None
    ) -> None:
        async with self._db.transaction() as s:
            await s.execute(
                update(WeeklyReport)
                .where(WeeklyReport.id == report_id, WeeklyReport.posted_at.is_(None))
                .values(message_id=message_id, posted_at=self._clock.now())
            )
            await AuditRepository(s).record(
                actor_discord_id=actor_discord_id,
                action="REPORT_POSTED",
                target_type="weekly_report",
                target_id=report_id,
                details={"message_id": message_id},
            )
