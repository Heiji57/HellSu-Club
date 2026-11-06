"""역할 변경·서버 탈퇴를 회원 상태에 반영한다 (ADR-0004). Server Members Intent 필요."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.services.member_service import GuildMemberInfo
from bot.checks import has_role

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)


class MemberEvents(commands.Cog):
    def __init__(self, bot: GymBot) -> None:
        self.bot = bot
        self.settings = bot.services.settings
        self.members = bot.services.members

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # 재연결 때마다 다시 호출될 수 있다. reconcile은 멱등이므로 문제없다.
        guild = self.bot.get_guild(self.settings.guild_id)
        if guild is None:
            log.error(
                "GUILD_ID=%s 서버를 찾을 수 없습니다. 봇이 초대되어 있는지 확인하세요.",
                self.settings.guild_id,
            )
            return
        if not guild.chunked:
            await guild.chunk()
        if not guild.chunked:
            # 멤버 목록이 불완전하면 멀쩡한 회원을 비활성화할 수 있으므로 건너뛴다.
            log.warning("서버 멤버 목록을 다 받지 못해 시작 시 회원 동기화를 건너뜁니다.")
            return
        infos = [
            GuildMemberInfo(m.id, m.display_name, has_role(m, self.settings.member_role_id))
            for m in guild.members
            if not m.bot
        ]
        await self.members.reconcile(infos)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != self.settings.guild_id or after.bot:
            return
        role_id = self.settings.member_role_id
        role_changed = has_role(before, role_id) != has_role(after, role_id)
        renamed = before.display_name != after.display_name
        if role_changed or renamed:
            await self.members.sync(
                after.id, after.display_name, has_member_role=has_role(after, role_id)
            )

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        if payload.guild_id != self.settings.guild_id or payload.user.bot:
            return
        await self.members.deactivate(payload.user.id, reason="서버 탈퇴")
