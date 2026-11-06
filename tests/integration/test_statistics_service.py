"""주간 집계, 개인 기록, 목표 정책 (REQUIREMENTS 5, 6.3, 7).

고정 시각: 2026-11-04(수) 12:00 KST. 이번 주는 11/2(월) ~ 11/8(일).
"""

from datetime import date, datetime

import pytest
from sqlalchemy import update

from app.container import Services
from app.core.exceptions import NotAMember
from app.core.week import KST, Week
from app.models import (
    Attendance,
    AttendanceStatus,
    ReviewDecision,
    ReviewerType,
    Verification,
    VerificationReview,
    VerificationStatus,
)
from app.repositories.policy import PolicyRepository
from tests.fakes import FakeFetcher, make_settings
from tests.images import photo

THIS_WEEK = Week(date(2026, 11, 2))
LAST_WEEK = THIS_WEEK.previous()
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=KST)
LONG_AGO = datetime(2026, 10, 1, 12, 0, tzinfo=KST)


def kst(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=KST)


@pytest.fixture
def services(db, clock, tmp_path) -> Services:
    settings = make_settings().model_copy(
        update={"image_dir": str(tmp_path), "initial_weekly_target": 3}
    )
    return Services.build(settings, db, clock, fetcher=FakeFetcher())


class Club:
    """테스트 시나리오를 만드는 도우미. 시각을 옮겨 가입·탈퇴·인증 시점을 정한다."""

    def __init__(self, services: Services, clock) -> None:
        self.s = services
        self.clock = clock
        self._ids = iter(range(70_000, 80_000))

    async def join(self, user_id: int, name: str, at: datetime = LONG_AGO) -> None:
        self.clock.set(at)
        await self.s.members.sync(user_id, name, has_member_role=True)
        self.clock.set(NOW)

    async def leave(self, user_id: int, at: datetime) -> None:
        self.clock.set(at)
        await self.s.members.deactivate(user_id, reason="서버 탈퇴")
        self.clock.set(NOW)

    async def verify(self, user_id: int, at: datetime, *, image: bytes | None = None, **kw):
        from app.services.verification_service import Submission

        attachment = self.s.fetcher.add(image) if image is not None else None
        return await self.s.verifications.submit(
            Submission(
                discord_user_id=user_id,
                interaction_id=next(self._ids),
                channel_id=1,
                submitted_at=at,
                content=kw.get("content", "스쿼트 5세트, 런지 3세트"),
                attachment=attachment,
            )
        )

    async def revoke(self, verification_id: int, reason: str) -> None:
        """관리자 출석 취소(Phase 4)를 흉내 낸다."""
        async with self.s.db.transaction() as s:
            await s.execute(
                update(Attendance)
                .where(Attendance.verification_id == verification_id)
                .values(status=AttendanceStatus.REVOKED, revoked_at=NOW)
            )
            await s.execute(
                update(Verification)
                .where(Verification.id == verification_id)
                .values(status=VerificationStatus.REJECTED)
            )
            s.add(
                VerificationReview(
                    verification_id=verification_id,
                    reviewer_type=ReviewerType.ADMIN,
                    actor_discord_id=1,
                    decision=ReviewDecision.REVOKE,
                    reason=reason,
                )
            )

    async def reject(self, verification_id: int, reason: str) -> None:
        async with self.s.db.transaction() as s:
            await s.execute(
                update(Verification)
                .where(Verification.id == verification_id)
                .values(status=VerificationStatus.REJECTED, decided_at=NOW)
            )
            s.add(
                VerificationReview(
                    verification_id=verification_id,
                    reviewer_type=ReviewerType.ADMIN,
                    actor_discord_id=1,
                    decision=ReviewDecision.REJECT,
                    reason=reason,
                )
            )

    async def add_policy(self, target: int, effective_from: date) -> None:
        async with self.s.db.transaction() as s:
            await PolicyRepository(s).add(
                weekly_target=target, effective_from=effective_from, created_by_discord_id=1
            )


@pytest.fixture
async def club(services, clock) -> Club:
    clock.set(NOW)
    return Club(services, clock)


def counts(stats) -> dict[str, int]:
    return {row.display_name: row.count for row in stats.rows}


class TestWeekMembership:
    async def test_who_is_counted(self, club, services):
        await club.join(1, "철수")
        await club.join(2, "민수", at=kst(11, 3))  # 주중 가입 → 포함
        await club.join(3, "지수")
        await club.verify(3, kst(11, 2))
        await club.leave(3, kst(11, 3))  # 주중 탈퇴 → 제외 (출석이 있어도)
        await club.join(4, "하나")
        await club.leave(4, kst(10, 28))  # 지난주 탈퇴 → 제외

        stats = await services.statistics.week_stats(THIS_WEEK)
        assert set(counts(stats)) == {"철수", "민수"}

    async def test_member_who_left_later_is_counted_in_past_week(self, club, services):
        await club.join(3, "지수")
        await club.verify(3, kst(10, 27))
        await club.leave(3, kst(11, 3))  # 지난주가 끝난 뒤에 탈퇴

        stats = await services.statistics.week_stats(LAST_WEEK)
        assert counts(stats) == {"지수": 1}

    async def test_member_who_joined_after_week_is_not_counted(self, club, services):
        await club.join(2, "민수", at=kst(11, 3))
        stats = await services.statistics.week_stats(LAST_WEEK)
        assert counts(stats) == {}


class TestWeekCounts:
    async def test_week_boundary(self, club, services):
        await club.join(1, "철수")
        await club.verify(1, datetime(2026, 11, 1, 23, 59, 59, tzinfo=KST))  # 지난주 일요일
        await club.verify(1, datetime(2026, 11, 2, 0, 0, 0, tzinfo=KST))  # 이번 주 월요일
        await club.verify(1, datetime(2026, 11, 8, 23, 59, 59, tzinfo=KST))  # 이번 주 일요일
        await club.verify(1, datetime(2026, 11, 9, 0, 0, 0, tzinfo=KST))  # 다음 주

        assert counts(await services.statistics.week_stats(LAST_WEEK)) == {"철수": 1}
        assert counts(await services.statistics.week_stats(THIS_WEEK)) == {"철수": 2}
        assert counts(await services.statistics.week_stats(THIS_WEEK.next())) == {"철수": 1}

    async def test_revoked_and_pending_are_not_counted(self, club, services):
        await club.join(1, "철수")
        await club.join(2, "영희")
        first = await club.verify(1, kst(11, 2), image=photo(1))
        await club.verify(1, kst(11, 3))
        await club.revoke(first.verification_id, "다른 날 사진")
        await club.verify(2, kst(11, 3), image=photo(1))  # 같은 사진 → 검토 대기

        stats = await services.statistics.week_stats(THIS_WEEK)
        assert counts(stats) == {"철수": 1, "영희": 0}
        assert stats.pending_reviews == 1

    async def test_summary_and_ranking_order(self, club, services):
        for uid, name, days in [
            (1, "가람", [2, 3]),
            (2, "나래", [2, 3, 4, 5]),
            (3, "다온", [2, 3, 4]),
            (4, "라온", []),
            (5, "마루", [2, 3, 4]),
        ]:
            await club.join(uid, name)
            for d in days:
                await club.verify(uid, kst(11, d))

        stats = await services.statistics.week_stats(THIS_WEEK)
        # 목표 3회: 달성자 먼저(횟수 많은 순, 같으면 이름순), 그다음 미달성자
        assert [(r.display_name, r.count, r.achieved) for r in stats.rows] == [
            ("나래", 4, True),
            ("다온", 3, True),
            ("마루", 3, True),
            ("가람", 2, False),
            ("라온", 0, False),
        ]
        assert (stats.member_count, stats.total_attendance, stats.achieved_count) == (5, 12, 3)
        assert stats.achievement_rate == pytest.approx(0.6)

    async def test_default_is_current_week(self, club, services):
        await club.join(1, "철수")
        await club.verify(1, kst(11, 4))
        stats = await services.statistics.week_stats()
        assert stats.week == THIS_WEEK and counts(stats) == {"철수": 1}


class TestMyWeek:
    async def test_progress(self, club, services):
        await club.join(1, "철수")
        await club.join(2, "영희")
        await club.verify(2, kst(11, 1), image=photo(1))
        await club.verify(1, kst(11, 2))
        await club.verify(1, kst(11, 4), image=photo(1))  # 검토 대기

        me = await services.statistics.my_week(1)
        assert me.days == (date(2026, 11, 2),)
        assert (me.count, me.target, me.remaining, me.achieved) == (1, 3, 2, False)
        assert me.pending_reviews == 1
        assert me.days_left == 5  # 수~일

    async def test_unknown_user(self, services):
        with pytest.raises(NotAMember):
            await services.statistics.my_week(999)


class TestHistory:
    async def test_pages_newest_first(self, club, services):
        await club.join(1, "철수")
        for d in range(1, 8):  # 11/1 ~ 11/7, 7건
            await club.verify(1, kst(11, d))

        first = await services.statistics.history(1, page=1)
        assert (first.total, first.total_pages) == (7, 2)
        assert [i.workout_date.day for i in first.items] == [7, 6, 5, 4, 3]
        second = await services.statistics.history(1, page=2)
        assert [i.workout_date.day for i in second.items] == [2, 1]

    async def test_page_is_clamped(self, club, services):
        await club.join(1, "철수")
        await club.verify(1, kst(11, 2))
        assert (await services.statistics.history(1, page=99)).page == 1

    async def test_empty(self, club, services):
        await club.join(1, "철수")
        page = await services.statistics.history(1)
        assert (page.items, page.total, page.total_pages) == ((), 0, 1)

    async def test_shows_status_and_reasons(self, club, services):
        await club.join(1, "철수")
        await club.join(2, "영희")
        await club.verify(2, kst(10, 30), image=photo(1))
        revoked = await club.verify(1, kst(11, 2))
        rejected = await club.verify(1, kst(11, 3), image=photo(1))
        pending = await club.verify(1, kst(11, 4), image=photo(1), content=None)
        await club.revoke(revoked.verification_id, "운동 내용과 무관")
        await club.reject(rejected.verification_id, "다른 회원의 사진")

        items = {i.verification_id: i for i in (await services.statistics.history(1)).items}
        r = items[revoked.verification_id]
        assert (r.status, r.revoked, r.reason) == (
            VerificationStatus.REJECTED,
            True,
            "운동 내용과 무관",
        )
        r = items[rejected.verification_id]
        assert (r.revoked, r.reason, r.has_image) == (False, "다른 회원의 사진", True)
        r = items[pending.verification_id]
        assert (r.status, r.reason, r.content) == (VerificationStatus.REVIEW_REQUIRED, None, None)


class TestTargetPolicy:
    async def test_target_by_effective_date(self, club, services):
        await services.policies.ensure_initial()  # 3회, 이번 주부터
        await club.add_policy(5, THIS_WEEK.next().start)  # 다음 주부터 5회 예약

        assert await services.policies.target_for(LAST_WEEK) == 3  # 첫 정책 이전 주
        assert await services.policies.target_for(THIS_WEEK) == 3
        assert await services.policies.target_for(THIS_WEEK.next()) == 5

        overview = await services.policies.overview()
        assert (overview.this_target, overview.next_target, overview.changes_next_week) == (
            3,
            5,
            True,
        )

    async def test_changing_this_week_keeps_last_week(self, club, services):
        await club.add_policy(3, LAST_WEEK.start)
        await club.join(1, "철수")
        for d in (26, 27):
            await club.verify(1, kst(10, d))
        before = await services.statistics.week_stats(LAST_WEEK)

        await club.add_policy(2, THIS_WEEK.start)  # 이번 주부터 2회

        after = await services.statistics.week_stats(LAST_WEEK)
        assert before.target == after.target == 3
        assert after.rows[0].achieved is False
        assert (await services.statistics.week_stats(THIS_WEEK)).target == 2

    async def test_latest_created_policy_wins(self, club, services):
        # 다음 주 5회를 예약한 뒤 '이번 주부터 4회'로 다시 정하면, 나중 결정이 다음 주에도 적용된다
        await club.add_policy(3, LAST_WEEK.start)
        await club.add_policy(5, THIS_WEEK.next().start)
        await club.add_policy(4, THIS_WEEK.start)
        assert await services.policies.target_for(THIS_WEEK) == 4
        assert await services.policies.target_for(THIS_WEEK.next()) == 4
