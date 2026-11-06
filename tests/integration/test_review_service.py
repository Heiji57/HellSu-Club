"""관리자 검토(승인·거절·출석 취소), 지난 결과 알림, 목표 변경."""

import asyncio
from datetime import date

import pytest
from sqlalchemy import select

from app.core.exceptions import (
    AlreadyProcessed,
    AlreadyVerifiedToday,
    ReasonRequired,
    VerificationNotFound,
)
from app.core.week import Week
from app.models import (
    Attendance,
    AttendanceStatus,
    AuditLog,
    ClubPolicy,
    Verification,
    VerificationStatus,
)
from tests.images import photo
from tests.integration.conftest import kst

ADMIN, ADMIN2 = 9001, 9002
THIS_WEEK = Week(date(2026, 11, 2))


@pytest.fixture
async def pending(club):
    """철수의 11/4 인증이 영희의 11/3 사진과 같아 검토 대기 중인 상태."""
    await club.join(1, "철수")
    await club.join(2, "영희")
    original = await club.verify(2, kst(11, 3), image=photo(1))
    result = await club.verify(1, kst(11, 4), image=photo(1))
    assert result.status is VerificationStatus.REVIEW_REQUIRED
    return result.verification_id, original.verification_id


async def week_count(services, user_id: int = 1) -> int:
    return (await services.statistics.my_week(user_id, THIS_WEEK)).count


async def verification(db, vid: int) -> Verification:
    async with db.transaction() as s:
        return await s.get(Verification, vid)


async def audit_actions(db) -> list[tuple[str, int | None]]:
    async with db.transaction() as s:
        rows = await s.execute(
            select(AuditLog.action, AuditLog.actor_discord_id).order_by(AuditLog.id)
        )
        return [tuple(r) for r in rows if not r.action.startswith("MEMBER_")]


class TestApprove:
    async def test_creates_attendance_on_workout_date(self, db, clock, services, pending):
        vid, _ = pending
        clock.set(kst(11, 6))  # 이틀 뒤에 승인해도 출석일은 인증한 날이다
        outcome = await services.reviews.approve(vid, actor_discord_id=ADMIN, reason="헬스장 확인")

        assert (outcome.status, outcome.member_discord_id) == (VerificationStatus.APPROVED, 1)
        async with db.transaction() as s:
            attendance = await s.scalar(select(Attendance).where(Attendance.verification_id == vid))
        assert attendance.workout_date == date(2026, 11, 4)
        assert attendance.status == AttendanceStatus.ACTIVE
        assert await week_count(services) == 1

        v = await verification(db, vid)
        assert v.result_notified_at is None  # 다음 명령 때 알린다
        detail = await services.reviews.detail(vid)
        assert [(r.reviewer_type, r.decision, r.reason) for r in detail.reviews] == [
            ("RULE", "REVIEW", detail.reviews[0].reason),
            ("ADMIN", "APPROVE", "헬스장 확인"),
        ]
        assert await audit_actions(db) == [("VERIFICATION_APPROVED", ADMIN)]

    async def test_twice_is_refused(self, services, pending):
        vid, _ = pending
        await services.reviews.approve(vid, actor_discord_id=ADMIN)
        with pytest.raises(AlreadyProcessed) as e:
            await services.reviews.approve(vid, actor_discord_id=ADMIN2)
        assert "승인" in e.value.user_message

    async def test_not_found(self, services):
        with pytest.raises(VerificationNotFound):
            await services.reviews.approve(12345, actor_discord_id=ADMIN)

    async def test_approved_by_rule_cannot_be_approved_again(self, club, services):
        await club.join(1, "철수")
        result = await club.verify(1, kst(11, 4))
        with pytest.raises(AlreadyProcessed):
            await services.reviews.approve(result.verification_id, actor_discord_id=ADMIN)


class TestReject:
    async def test_requires_reason(self, services, pending):
        vid, _ = pending
        for reason in (None, "", "   "):
            with pytest.raises(ReasonRequired):
                await services.reviews.reject(vid, actor_discord_id=ADMIN, reason=reason)

    async def test_saves_reason_and_allows_resubmission(self, db, club, services, pending):
        vid, _ = pending
        await services.reviews.reject(vid, actor_discord_id=ADMIN, reason=" 다른 회원의 사진 ")

        history = await services.statistics.history(1)
        assert history.items[0].reason == "다른 회원의 사진"
        assert await week_count(services) == 0
        again = await club.verify(1, kst(11, 4, 20))  # 같은 날 다시 인증
        assert again.status is VerificationStatus.APPROVED
        assert await audit_actions(db) == [("VERIFICATION_REJECTED", ADMIN)]


class TestConcurrency:
    async def test_two_admins_only_one_wins(self, db, services, pending):
        vid, _ = pending
        results = await asyncio.gather(
            services.reviews.approve(vid, actor_discord_id=ADMIN),
            services.reviews.reject(vid, actor_discord_id=ADMIN2, reason="중복 사진"),
            return_exceptions=True,
        )
        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, BaseException)]
        assert len(winners) == 1 and len(losers) == 1
        assert isinstance(losers[0], AlreadyProcessed)

        v = await verification(db, vid)
        assert v.status == winners[0].status
        expected_attendance = 1 if v.status == VerificationStatus.APPROVED else 0
        assert await week_count(services) == expected_attendance
        assert len(await audit_actions(db)) == 1


class TestRevoke:
    async def test_revoke_reduces_count_and_allows_reverification(self, db, club, services):
        await club.join(1, "철수")
        await club.verify(1, kst(11, 2))
        today = await club.verify(1, kst(11, 4))
        assert await week_count(services) == 2

        outcome = await services.reviews.revoke(
            today.verification_id, actor_discord_id=ADMIN, reason="운동 내용과 무관"
        )
        assert outcome.status is VerificationStatus.REJECTED
        assert await week_count(services) == 1
        async with db.transaction() as s:
            att = await s.scalar(
                select(Attendance).where(Attendance.verification_id == today.verification_id)
            )
        assert att.status == AttendanceStatus.REVOKED and att.revoked_at is not None

        history = await services.statistics.history(1)
        assert (history.items[0].revoked, history.items[0].reason) == (True, "운동 내용과 무관")

        again = await club.verify(1, kst(11, 4, 21))  # 취소된 날은 다시 인증할 수 있다
        assert again.status is VerificationStatus.APPROVED
        assert await week_count(services) == 2
        assert await audit_actions(db) == [("ATTENDANCE_REVOKED", ADMIN)]

    async def test_only_approved_can_be_revoked(self, services, pending):
        vid, _ = pending
        with pytest.raises(AlreadyProcessed):
            await services.reviews.revoke(vid, actor_discord_id=ADMIN, reason="취소")

    async def test_revoke_twice(self, club, services):
        await club.join(1, "철수")
        r = await club.verify(1, kst(11, 4))
        await services.reviews.revoke(r.verification_id, actor_discord_id=ADMIN, reason="취소")
        with pytest.raises(AlreadyProcessed):
            await services.reviews.revoke(r.verification_id, actor_discord_id=ADMIN, reason="취소")

    async def test_revoke_after_admin_approval(self, services, pending):
        vid, _ = pending
        await services.reviews.approve(vid, actor_discord_id=ADMIN)
        await services.reviews.revoke(vid, actor_discord_id=ADMIN2, reason="다시 보니 아님")
        assert await week_count(services) == 0

    async def test_another_day_still_blocked(self, club, services):
        # 오늘이 아닌 다른 날의 인증은 이미 있으면 그대로 막힌다
        await club.join(1, "철수")
        await club.verify(1, kst(11, 3))
        with pytest.raises(AlreadyVerifiedToday):
            await club.verify(1, kst(11, 3, 20))


class TestDetailAndPending:
    async def test_detail(self, services, pending):
        vid, original = pending
        detail = await services.reviews.detail(vid)
        assert detail.member.display_name == "철수"
        assert detail.status is VerificationStatus.REVIEW_REQUIRED
        [(ref, distance, exact)] = detail.matches
        assert (ref.id, ref.member_display_name, distance, exact) == (original, "영희", 0, True)
        assert detail.image_file is not None and detail.image_file.exists()

    async def test_detail_not_found(self, services):
        with pytest.raises(VerificationNotFound):
            await services.reviews.detail(999)

    async def test_list_pending_oldest_first(self, club, services, pending):
        vid, _ = pending
        await club.join(3, "민수")
        second = await club.verify(3, kst(11, 5), image=photo(1))
        items, total = await services.reviews.list_pending()
        assert total == 2
        assert [i.verification_id for i in items] == [vid, second.verification_id]
        assert items[0].member_display_name == "철수" and "같은 파일" in items[0].reason

        items, total = await services.reviews.list_pending(limit=1)
        assert (len(items), total) == (1, 2)


class TestNotifications:
    async def test_admin_decision_is_delivered_once(self, services, pending):
        vid, _ = pending
        assert await services.notifications.claim(1) == []  # 아직 검토 중
        await services.reviews.reject(vid, actor_discord_id=ADMIN, reason="중복 사진")

        [notice] = await services.notifications.claim(1)
        assert (notice.verification_id, notice.status, notice.revoked, notice.reason) == (
            vid,
            VerificationStatus.REJECTED,
            False,
            "중복 사진",
        )
        assert await services.notifications.claim(1) == []

    async def test_rule_results_are_not_repeated(self, club, services):
        await club.join(1, "철수")
        await club.verify(1, kst(11, 4))
        assert await services.notifications.claim(1) == []

    async def test_revoke_is_delivered(self, club, services):
        await club.join(1, "철수")
        r = await club.verify(1, kst(11, 3))
        await services.reviews.revoke(r.verification_id, actor_discord_id=ADMIN, reason="사진 없음")
        [notice] = await services.notifications.claim(1)
        assert notice.revoked and notice.reason == "사진 없음"

    async def test_concurrent_claims_deliver_once(self, services, pending):
        vid, _ = pending
        await services.reviews.approve(vid, actor_discord_id=ADMIN)
        results = await asyncio.gather(*(services.notifications.claim(1) for _ in range(5)))
        assert sum(len(r) for r in results) == 1

    async def test_other_members_notices_are_separate(self, services, pending):
        vid, _ = pending
        await services.reviews.approve(vid, actor_discord_id=ADMIN)
        assert await services.notifications.claim(2) == []
        assert len(await services.notifications.claim(1)) == 1

    async def test_unknown_user(self, services):
        assert await services.notifications.claim(424242) == []


class TestSetTarget:
    async def test_next_week_by_default(self, db, services):
        await services.policies.ensure_initial()  # 3회 (services 픽스처)
        week = await services.policies.set_target(5, this_week=False, actor_discord_id=ADMIN)
        assert week == THIS_WEEK.next()
        assert await services.policies.target_for(THIS_WEEK) == 3
        assert await services.policies.target_for(THIS_WEEK.next()) == 5
        async with db.transaction() as s:
            log = await s.scalar(select(AuditLog).where(AuditLog.action == "POLICY_CHANGED"))
        assert log.actor_discord_id == ADMIN
        assert log.details == {
            "weekly_target": 5,
            "previous_target": 3,
            "effective_from": "2026-11-09",
        }

    async def test_this_week_keeps_last_week(self, db, services):
        await services.policies.ensure_initial()
        async with db.transaction() as s:
            s.add(ClubPolicy(weekly_target=3, effective_from=THIS_WEEK.previous().start))
        await services.policies.set_target(2, this_week=True, actor_discord_id=ADMIN)
        assert await services.policies.target_for(THIS_WEEK) == 2
        assert await services.policies.target_for(THIS_WEEK.previous()) == 3

    async def test_out_of_range(self, services):
        with pytest.raises(ValueError):
            await services.policies.set_target(8, this_week=False, actor_discord_id=ADMIN)
