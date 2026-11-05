"""역할 기반 회원 관리 (ADR-0004).

Discord 역할 보유 여부가 기준이고, member 테이블은 기록·집계용이다.
- 역할 보유 → 없으면 등록, 비활성이면 재활성화
- 역할 없음 또는 서버 탈퇴 → 비활성화 (기록은 보존)
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.database import Database
from app.models import Member, MemberStatus
from app.repositories.audit import AuditRepository
from app.repositories.member import MemberRepository

log = logging.getLogger(__name__)


class SyncAction(StrEnum):
    REGISTERED = "REGISTERED"
    REACTIVATED = "REACTIVATED"
    DEACTIVATED = "DEACTIVATED"
    RENAMED = "RENAMED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class GuildMemberInfo:
    discord_user_id: int
    display_name: str
    has_member_role: bool


@dataclass
class ReconcileResult:
    counts: dict[SyncAction, int] = field(default_factory=dict)

    def add(self, action: SyncAction) -> None:
        self.counts[action] = self.counts.get(action, 0) + 1

    def get(self, action: SyncAction) -> int:
        return self.counts.get(action, 0)


class MemberService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def sync(
        self, discord_user_id: int, display_name: str, *, has_member_role: bool
    ) -> SyncAction:
        """한 사람의 역할 상태를 DB에 반영한다. 역할 변경 이벤트와 명령 실행 시 호출."""
        async with self._db.transaction() as s:
            action = await self._sync(s, discord_user_id, display_name, has_member_role)
        if action not in (SyncAction.UNCHANGED, SyncAction.RENAMED):
            log.info("member %s: %s", discord_user_id, action)
        return action

    async def deactivate(self, discord_user_id: int, *, reason: str) -> SyncAction:
        """서버 탈퇴 등으로 비활성화. 등록되지 않은 사용자면 아무것도 하지 않는다."""
        async with self._db.transaction() as s:
            member = await MemberRepository(s).get_by_discord_id(discord_user_id, lock=True)
            if member is None or not member.is_active:
                return SyncAction.UNCHANGED
            await self._deactivate(s, member, reason)
        log.info("member %s: DEACTIVATED (%s)", discord_user_id, reason)
        return SyncAction.DEACTIVATED

    async def reconcile(self, guild_members: Iterable[GuildMemberInfo]) -> ReconcileResult:
        """봇 시작 시 길드 전체 상태와 DB를 맞춘다.

        역할 보유자는 등록·재활성화하고, DB에서 ACTIVE인데 역할이 없거나
        길드에 없는 회원은 비활성화한다(봇이 꺼져 있는 동안의 변경 반영).
        """
        result = ReconcileResult()
        holders = {m.discord_user_id: m for m in guild_members if m.has_member_role}
        async with self._db.transaction() as s:
            for info in holders.values():
                result.add(await self._sync(s, info.discord_user_id, info.display_name, True))
            for member in await MemberRepository(s).list_active(lock=True):
                if member.discord_user_id not in holders:
                    await self._deactivate(s, member, "역할 없음 또는 서버에 없음 (시작 시 동기화)")
                    result.add(SyncAction.DEACTIVATED)
        log.info("member reconcile: %s", {k.value: v for k, v in result.counts.items()})
        return result

    async def get(self, discord_user_id: int) -> Member | None:
        async with self._db.transaction() as s:
            return await MemberRepository(s).get_by_discord_id(discord_user_id)

    async def list_all(self) -> Sequence[Member]:
        async with self._db.transaction() as s:
            return await MemberRepository(s).list_all()

    async def count_active(self) -> int:
        async with self._db.transaction() as s:
            return await MemberRepository(s).count_active()

    # --- 내부 ---

    async def _sync(
        self, s: AsyncSession, discord_user_id: int, display_name: str, has_member_role: bool
    ) -> SyncAction:
        repo = MemberRepository(s)
        if not has_member_role:
            member = await repo.get_by_discord_id(discord_user_id, lock=True)
            if member is None or not member.is_active:
                return SyncAction.UNCHANGED
            await self._deactivate(s, member, "회원 역할 회수")
            return SyncAction.DEACTIVATED

        new_id = await repo.insert_if_absent(discord_user_id, display_name, self._clock.now())
        if new_id is not None:
            await AuditRepository(s).record(
                actor_discord_id=None,
                action="MEMBER_REGISTERED",
                target_type="member",
                target_id=new_id,
                details={"discord_user_id": discord_user_id},
            )
            return SyncAction.REGISTERED

        member = await repo.get_by_discord_id(discord_user_id, lock=True)
        assert member is not None  # insert_if_absent가 충돌했으므로 존재한다
        action = SyncAction.UNCHANGED
        if not member.is_active:
            member.status = MemberStatus.ACTIVE
            member.deactivated_at = None
            await AuditRepository(s).record(
                actor_discord_id=None,
                action="MEMBER_REACTIVATED",
                target_type="member",
                target_id=member.id,
                details={"discord_user_id": discord_user_id},
            )
            action = SyncAction.REACTIVATED
        if member.display_name != display_name:
            member.display_name = display_name
            if action is SyncAction.UNCHANGED:
                action = SyncAction.RENAMED
        return action

    async def _deactivate(self, s: AsyncSession, member: Member, reason: str) -> None:
        member.status = MemberStatus.INACTIVE
        member.deactivated_at = self._clock.now()
        await AuditRepository(s).record(
            actor_discord_id=None,
            action="MEMBER_DEACTIVATED",
            target_type="member",
            target_id=member.id,
            details={"discord_user_id": member.discord_user_id, "reason": reason},
        )
