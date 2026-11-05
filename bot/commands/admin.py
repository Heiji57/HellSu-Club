"""관리자 명령어 `/관리 …`. 실행할 때마다 관리자 역할을 확인한다 (ADR-0004)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import AdminOnly, is_admin
from bot.responses import send_private

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}일")
    if hours or days:
        parts.append(f"{hours}시간")
    parts.append(f"{minutes}분")
    return " ".join(parts)


class AdminCommands(commands.Cog):
    # 일반 회원 화면에서는 숨긴다(서버 설정 → 연동에서 관리자 역할에 허용).
    # 실제 권한은 interaction_check에서 역할로 확인한다.
    admin = app_commands.Group(
        name="관리",
        description="헬스클럽 관리자 명령어",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: GymBot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_admin(interaction):
            raise AdminOnly()
        return True

    @admin.command(name="시스템상태", description="봇과 데이터베이스 상태를 확인합니다.")
    async def system_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        services = self.bot.services

        db_ok = True
        try:
            db_ms = await services.db.ping()
            active = await services.members.count_active()
            db_line = f"🟢 정상 ({db_ms:.0f}ms)"
        except Exception:
            log.exception("DB 상태 확인 실패")
            db_ok = False
            active = None
            db_line = "🔴 연결 실패"

        embed = discord.Embed(
            title="시스템 상태",
            color=discord.Color.green() if db_ok else discord.Color.red(),
            timestamp=datetime.now(UTC),
        )
        embed.add_field(name="봇 지연", value=f"{self.bot.latency * 1000:.0f}ms")
        embed.add_field(name="데이터베이스", value=db_line)
        if self.bot.started_at is not None:
            uptime = (datetime.now(UTC) - self.bot.started_at).total_seconds()
            embed.add_field(name="가동 시간", value=format_duration(uptime))
        embed.add_field(name="활성 회원", value="-" if active is None else f"{active}명")
        await send_private(interaction, embed=embed)
