"""회원용 조회 명령어. Phase 1에서는 /도움말만 있다 (/내출석, /내기록, /목표는 Phase 3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import is_admin
from bot.embeds.help import help_embed
from bot.responses import send_private

if TYPE_CHECKING:
    from bot.main import GymBot


class MeCommands(commands.Cog):
    def __init__(self, bot: GymBot) -> None:
        self.bot = bot

    @app_commands.command(name="도움말", description="헬스클럽 봇 사용법을 안내합니다.")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction) -> None:
        await send_private(interaction, embed=help_embed(include_admin=is_admin(interaction)))
