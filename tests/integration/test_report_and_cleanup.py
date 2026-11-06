"""주간 리포트(선점·게시·스냅샷·보충), 이미지 정리, 전체 흐름."""

import asyncio
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.week import KST, Week
from app.models import AuditLog, Verification, VerificationStatus, WeeklyReport
from app.services.report_service import PublishStatus, ReportSnapshot
from tests.images import photo
from tests.integration.conftest import kst

THIS_WEEK = Week(date(2026, 11, 2))
LAST_WEEK = THIS_WEEK.previous()
ADMIN = 9001


class Poster:
    def __init__(self, fail_times: int = 0) -> None:
        self.posted: list[ReportSnapshot] = []
        self.fail_times = fail_times

    async def __call__(self, snapshot: ReportSnapshot) -> int:
        await asyncio.sleep(0)  # 실제 전송처럼 다른 작업에 양보한다
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("Discord 전송 실패")
        self.posted.append(snapshot)
        return 800_000 + len(self.posted)


@pytest.fixture
async def last_week_activity(club):
    """지난주(10/26~11/1): 철수 3회, 영희 1회. 목표 3회."""
    await club.join(1, "철수")
    await club.join(2, "영희")
    for d in (26, 27, 28):
        await club.verify(1, kst(10, d))
    await club.verify(2, kst(10, 29))


async def report_rows(db) -> list[WeeklyReport]:
    async with db.transaction() as s:
        return list(await s.scalars(select(WeeklyReport)))


class TestDueWeek:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (datetime(2026, 11, 4, 12, 0, tzinfo=KST), LAST_WEEK),  # 수요일
            (datetime(2026, 11, 9, 8, 59, tzinfo=KST), LAST_WEEK),  # 다음 월요일 게시 전
            (datetime(2026, 11, 9, 9, 0, tzinfo=KST), THIS_WEEK),  # 다음 월요일 게시 시각
            (datetime(2026, 11, 2, 0, 30, tzinfo=KST), LAST_WEEK.previous()),  # 월요일 새벽
        ],
    )
    def test_most_recent_week_past_report_time(self, clock, services, now, expected):
        clock.set(now)
        assert services.reports.due_week() == expected


class TestPublish:
    async def test_posts_once(self, db, services, last_week_activity):
        poster = Poster()
        result = await services.reports.publish_due(poster)

        assert (result.status, result.week, result.message_id) == (
            PublishStatus.POSTED,
            LAST_WEEK,
            800_001,
        )
        [snap] = poster.posted
        assert [(r.display_name, r.count, r.achieved) for r in snap.rows] == [
            ("철수", 3, True),
            ("영희", 1, False),
        ]
        assert (snap.target, snap.total_attendance, snap.achieved_count) == (3, 4, 1)

        again = await services.reports.publish_due(poster)
        assert again.status is PublishStatus.ALREADY_POSTED
        assert len(poster.posted) == 1
        [row] = await report_rows(db)
        assert (row.week_start, row.message_id) == (LAST_WEEK.start, 800_001)
        assert row.posted_at is not None
        async with db.transaction() as s:
            assert (
                await s.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "REPORT_POSTED")
                )
                == 1
            )

    async def test_concurrent_runs_post_once(self, services, last_week_activity):
        poster = Poster()
        results = await asyncio.gather(*(services.reports.publish_due(poster) for _ in range(3)))
        assert sorted(r.status for r in results) == [
            PublishStatus.ALREADY_POSTED,
            PublishStatus.ALREADY_POSTED,
            PublishStatus.POSTED,
        ]
        assert len(poster.posted) == 1

    async def test_failed_post_is_retried_with_same_snapshot(
        self, db, club, services, last_week_activity
    ):
        poster = Poster(fail_times=1)
        with pytest.raises(ConnectionError):
            await services.reports.publish_due(poster)
        [row] = await report_rows(db)
        assert row.posted_at is None and row.message_id is None

        # 실패와 재시도 사이에 지난주 출석이 취소되어도, 선점한 스냅샷 그대로 게시한다
        async with db.transaction() as s:
            vid = await s.scalar(
                select(Verification.id).where(Verification.workout_date == date(2026, 10, 26))
            )
        await services.reviews.revoke(vid, actor_discord_id=ADMIN, reason="취소")

        result = await services.reports.publish_due(poster)
        assert result.status is PublishStatus.POSTED
        assert poster.posted[0].rows[0].count == 3

    async def test_snapshot_is_kept_after_later_changes(self, db, services, last_week_activity):
        await services.reports.publish_due(Poster())
        async with db.transaction() as s:
            vid = await s.scalar(
                select(Verification.id).where(Verification.workout_date == date(2026, 10, 26))
            )
        await services.reviews.revoke(vid, actor_discord_id=ADMIN, reason="취소")

        preview = await services.reports.preview(LAST_WEEK)
        assert preview.posted_at is not None
        assert preview.snapshot.rows[0].count == 3  # 게시된 리포트는 바뀌지 않는다
        live = await services.statistics.week_stats(LAST_WEEK)
        assert live.rows[0].count == 2  # 실시간 통계에는 반영된다

    async def test_catch_up_after_downtime(self, clock, services, last_week_activity):
        # 월요일 09:00에 꺼져 있다가 화요일에 켜진 경우 → 지난주 리포트를 보충
        clock.set(datetime(2026, 11, 10, 15, 0, tzinfo=KST))
        poster = Poster()
        result = await services.reports.publish_due(poster)
        assert result.week == THIS_WEEK  # 11/10 기준으로 '지난주'는 11/2 주
        assert result.status is PublishStatus.POSTED

    async def test_no_members_is_not_posted(self, db, services):
        result = await services.reports.publish_due(Poster())
        assert result.status is PublishStatus.NO_MEMBERS
        assert await report_rows(db) == []

    async def test_current_week_is_not_due(self, services, last_week_activity):
        result = await services.reports.publish(THIS_WEEK, Poster())
        assert result.status is PublishStatus.NOT_DUE

    async def test_preview_before_posting_is_live(self, db, services, last_week_activity):
        preview = await services.reports.preview(LAST_WEEK)
        assert preview.posted_at is None
        assert preview.snapshot.total_attendance == 4
        assert await report_rows(db) == []  # 미리보기는 선점하지 않는다

    def test_snapshot_round_trip(self):
        from app.services.report_service import ReportRow

        snap = ReportSnapshot(LAST_WEEK, 3, (ReportRow(1, "철수", 3, True),), 2)
        assert ReportSnapshot.from_dict(snap.to_dict()) == snap


class TestImageCleanup:
    async def stored_paths(self, db) -> dict[int, str | None]:
        async with db.transaction() as s:
            rows = await s.execute(select(Verification.id, Verification.image_path))
            return dict(rows.all())

    async def test_deletes_only_expired_decided_images(self, db, clock, club, services):
        await club.join(1, "철수")
        await club.join(2, "영희")
        clock.set(kst(10, 1))  # decided_at은 처리 시각이다
        old = await club.verify(1, kst(10, 1), image=photo(1))
        pending = await club.verify(2, kst(10, 2), image=photo(1))  # 검토 대기
        recent_at = kst(10, 20)
        clock.set(recent_at)
        recent = await club.verify(1, recent_at, image=photo(3))

        clock.set(kst(11, 4))  # old·pending은 34일, recent는 15일 경과
        result = await services.image_cleanup.run()

        assert (result.cleared, result.deleted_files) == (1, 1)
        assert old.image_file is not None and not old.image_file.exists()
        paths = await self.stored_paths(db)
        assert paths[old.verification_id] is None
        assert paths[pending.verification_id] is not None  # 관리자가 아직 봐야 한다
        assert paths[recent.verification_id] is not None
        assert recent.image_file.exists()

    async def test_duplicate_detection_survives_cleanup(self, clock, club, services):
        await club.join(1, "철수")
        await club.join(2, "영희")
        clock.set(kst(9, 1))
        await club.verify(1, kst(9, 1), image=photo(1))
        clock.set(kst(11, 4))
        assert (await services.image_cleanup.run()).cleared == 1

        again = await club.verify(2, kst(11, 4), image=photo(1))
        assert again.status is VerificationStatus.REVIEW_REQUIRED

    async def test_missing_file_still_clears_path(self, db, clock, club, services):
        await club.join(1, "철수")
        clock.set(kst(9, 1))
        r = await club.verify(1, kst(9, 1), image=photo(1))
        r.image_file.unlink()
        clock.set(kst(11, 4))
        result = await services.image_cleanup.run()
        assert (result.cleared, result.deleted_files) == (1, 0)  # 이미 없던 파일
        assert (await self.stored_paths(db))[r.verification_id] is None

    async def test_failed_delete_keeps_path(self, db, clock, club, services, monkeypatch):
        await club.join(1, "철수")
        clock.set(kst(9, 1))
        r = await club.verify(1, kst(9, 1), image=photo(1))

        async def broken(relative):
            raise OSError("권한 없음")

        monkeypatch.setattr(services.storage, "delete", broken)
        clock.set(kst(11, 4))
        assert (await services.image_cleanup.run()).cleared == 0
        assert (await self.stored_paths(db))[r.verification_id] is not None

    async def test_usage(self, club, services):
        await club.join(1, "철수")
        r = await club.verify(1, kst(11, 4), image=photo(1))
        files, size = await services.storage.usage()
        assert (files, size) == (1, r.image_file.stat().st_size)


async def test_full_flow(db, clock, club, services):
    """인증 → 검토 → 승인 → 통계 → 리포트."""
    await club.join(1, "철수")
    await club.join(2, "영희")
    clock.set(kst(10, 26))
    await club.verify(2, kst(10, 26), image=photo(1))
    await club.verify(1, kst(10, 26))
    pending = await club.verify(1, kst(10, 27), image=photo(1))
    assert pending.status is VerificationStatus.REVIEW_REQUIRED
    await club.verify(1, kst(10, 28), image=photo(2))

    assert (await services.statistics.my_week(1, LAST_WEEK)).count == 2
    await services.reviews.approve(pending.verification_id, actor_discord_id=ADMIN)
    assert (await services.statistics.my_week(1, LAST_WEEK)).count == 3

    [notice] = await services.notifications.claim(1)
    assert notice.status is VerificationStatus.APPROVED

    clock.set(datetime(2026, 11, 2, 9, 0, tzinfo=KST))  # 월요일 리포트 시각
    poster = Poster()
    result = await services.reports.publish_due(poster)
    assert result.status is PublishStatus.POSTED and result.week == LAST_WEEK
    snap = poster.posted[0]
    assert [(r.display_name, r.count, r.achieved) for r in snap.rows] == [
        ("철수", 3, True),
        ("영희", 1, False),
    ]
    assert snap.achievement_rate == pytest.approx(0.5)
    clock.advance(timedelta(minutes=5))
    assert (await services.reports.publish_due(poster)).status is PublishStatus.ALREADY_POSTED
