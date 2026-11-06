"""회원용 조회 명령어: /내출석, /내기록, /목표, /랭킹, /도움말. 모든 응답은 본인에게만 보인다."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import ensure_member, is_admin
from bot.embeds.help import help_embed
from bot.embeds.statistics import history_embed, my_week_embed, ranking_embed, target_embed
from bot.notify import deliver_notices
from bot.responses import send_private
from bot.views.pagination_view import PaginationView

if TYPE_CHECKING:
    from bot.main import GymBot


class MeCommands(commands.Cog):
    def __init__(self, bot: GymBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="내출석", description="이번 주 내 출석 횟수와 목표 달성 현황을 봅니다."
    )
    @app_commands.guild_only()
    async def my_attendance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ensure_member(interaction)
        data = await self.bot.services.statistics.my_week(interaction.user.id)
        await send_private(interaction, embed=my_week_embed(data))
        await deliver_notices(interaction)

    @app_commands.command(name="내기록", description="내 인증 기록을 봅니다.")
    @app_commands.guild_only()
    @app_commands.rename(page="페이지")
    @app_commands.describe(page="볼 페이지 (한 페이지에 5건)")
    async def my_history(
        self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ensure_member(interaction)
        statistics = self.bot.services.statistics
        user_id = interaction.user.id

        async def render(p: int) -> tuple[discord.Embed, int, int]:
            result = await statistics.history(user_id, p)
            return history_embed(result), result.page, result.total_pages

        embed, current, total_pages = await render(page)
        if total_pages <= 1:
            await send_private(interaction, embed=embed)
        else:
            view = PaginationView(
                owner_id=user_id, page=current, total_pages=total_pages, render=render
            )
            view.message = await send_private(interaction, embed=embed, view=view)
        await deliver_notices(interaction)

    @app_commands.command(name="랭킹", description="이번 주 전체 회원의 출석 현황을 봅니다.")
    @app_commands.guild_only()
    async def ranking(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ensure_member(interaction, allow_admin=True)
        stats = await self.bot.services.statistics.week_stats()
        await send_private(interaction, embed=ranking_embed(stats, viewer_id=interaction.user.id))
        await deliver_notices(interaction)

    @app_commands.command(name="목표", description="이번 주와 다음 주 출석 목표를 봅니다.")
    @app_commands.guild_only()
    async def target(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ensure_member(interaction, allow_admin=True)
        overview = await self.bot.services.policies.overview()
        await send_private(interaction, embed=target_embed(overview))
        await deliver_notices(interaction)

    @app_commands.command(name="도움말", description="헬스클럽 봇 사용법을 안내합니다.")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction) -> None:
        await send_private(interaction, embed=help_embed(include_admin=is_admin(interaction)))
        await deliver_notices(interaction)
