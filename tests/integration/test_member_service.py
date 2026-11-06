import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, Member, MemberStatus
from app.services.member_service import GuildMemberInfo, MemberService, SyncAction

ALICE = 1001
BOB = 1002


@pytest.fixture
def service(db, clock) -> MemberService:
    return MemberService(db, clock)


async def audit_actions(db) -> list[str]:
    async with db.transaction() as s:
        return list((await s.scalars(select(AuditLog.action).order_by(AuditLog.id))).all())


class TestSync:
    async def test_role_holder_is_registered_as_active(self, service, db, clock):
        assert await service.sync(ALICE, "앨리스", has_member_role=True) is SyncAction.REGISTERED

        m = await service.get(ALICE)
        assert m is not None
        assert m.status == MemberStatus.ACTIVE
        assert m.display_name == "앨리스"
        assert m.joined_at == clock.now()
        assert m.deactivated_at is None
        assert await audit_actions(db) == ["MEMBER_REGISTERED"]

    async def test_repeat_sync_is_unchanged_and_rename_is_tracked(self, service):
        await service.sync(ALICE, "앨리스", has_member_role=True)
        assert await service.sync(ALICE, "앨리스", has_member_role=True) is SyncAction.UNCHANGED
        assert await service.sync(ALICE, "앨리스2", has_member_role=True) is SyncAction.RENAMED
        assert (await service.get(ALICE)).display_name == "앨리스2"

    async def test_role_removed_deactivates_and_keeps_record(self, service, db, clock):
        await service.sync(ALICE, "앨리스", has_member_role=True)
        clock.advance(timedelta(days=3))

        assert await service.sync(ALICE, "앨리스", has_member_role=False) is SyncAction.DEACTIVATED
        m = await service.get(ALICE)
        assert m.status == MemberStatus.INACTIVE
        assert m.deactivated_at == clock.now()
        # 두 번째 회수는 변화 없음
        assert await service.sync(ALICE, "앨리스", has_member_role=False) is SyncAction.UNCHANGED
        assert await audit_actions(db) == ["MEMBER_REGISTERED", "MEMBER_DEACTIVATED"]

    async def test_role_regranted_reactivates_same_record(self, service, clock):
        await service.sync(ALICE, "앨리스", has_member_role=True)
        original = await service.get(ALICE)
        await service.sync(ALICE, "앨리스", has_member_role=False)
        clock.advance(timedelta(days=10))

        assert await service.sync(ALICE, "앨리스", has_member_role=True) is SyncAction.REACTIVATED
        m = await service.get(ALICE)
        assert m.id == original.id
        assert m.joined_at == original.joined_at
        assert m.status == MemberStatus.ACTIVE and m.deactivated_at is None

    async def test_user_without_role_is_not_registered(self, service):
        assert await service.sync(BOB, "밥", has_member_role=False) is SyncAction.UNCHANGED
        assert await service.get(BOB) is None

    async def test_leaving_server_deactivates(self, service):
        await service.sync(ALICE, "앨리스", has_member_role=True)
        assert await service.deactivate(ALICE, reason="서버 탈퇴") is SyncAction.DEACTIVATED
        assert (await service.get(ALICE)).status == MemberStatus.INACTIVE
        assert await service.deactivate(BOB, reason="서버 탈퇴") is SyncAction.UNCHANGED


class TestDuplicatePrevention:
    async def test_concurrent_registration_creates_one_row(self, service, db):
        results = await asyncio.gather(
            *(service.sync(ALICE, "앨리스", has_member_role=True) for _ in range(5))
        )
        assert results.count(SyncAction.REGISTERED) == 1
        async with db.transaction() as s:
            assert await s.scalar(select(func.count()).select_from(Member)) == 1

    async def test_db_rejects_duplicate_discord_id(self, service, db, clock):
        await service.sync(ALICE, "앨리스", has_member_role=True)
        with pytest.raises(IntegrityError):
            async with db.transaction() as s:
                s.add(
                    Member(
                        discord_user_id=ALICE,
                        display_name="복제",
                        status=MemberStatus.ACTIVE,
                        joined_at=clock.now(),
                    )
                )

    async def test_db_rejects_status_deactivated_at_mismatch(self, db, clock):
        with pytest.raises(IntegrityError):
            async with db.transaction() as s:
                s.add(
                    Member(
                        discord_user_id=BOB,
                        display_name="밥",
                        status=MemberStatus.INACTIVE,
                        joined_at=clock.now(),
                        deactivated_at=None,
                    )
                )


class TestReconcile:
    async def test_reconcile_matches_guild_state(self, service):
        # 기존 DB 상태: A 활성, B 활성, C 비활성, E 활성(이제 서버에 없음)
        for uid, name in [(1, "A"), (2, "B"), (3, "C"), (5, "E")]:
            await service.sync(uid, name, has_member_role=True)
        await service.sync(3, "C", has_member_role=False)

        guild = [
            GuildMemberInfo(1, "A", True),
            GuildMemberInfo(2, "B", False),  # 역할 회수됨
            GuildMemberInfo(3, "C", True),  # 역할 다시 받음
            GuildMemberInfo(4, "D", True),  # 새 회원
            GuildMemberInfo(6, "F", False),  # 회원 아님
        ]
        result = await service.reconcile(guild)

        assert result.get(SyncAction.UNCHANGED) == 1
        assert result.get(SyncAction.REACTIVATED) == 1
        assert result.get(SyncAction.REGISTERED) == 1
        assert result.get(SyncAction.DEACTIVATED) == 2  # B, E

        statuses = {m.discord_user_id: m.status for m in await service.list_all()}
        assert statuses == {
            1: MemberStatus.ACTIVE,
            2: MemberStatus.INACTIVE,
            3: MemberStatus.ACTIVE,
            4: MemberStatus.ACTIVE,
            5: MemberStatus.INACTIVE,
        }
        assert await service.count_active() == 3
