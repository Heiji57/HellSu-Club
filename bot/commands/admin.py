"""관리자 명령어 `/관리 …`. 실행할 때마다 관리자 역할을 확인한다 (ADR-0004)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.services.review_service import MAX_REASON_LENGTH
from bot import checks
from bot.checks import AdminOnly
from bot.embeds.review import members_embed, pending_embed
from bot.embeds.statistics import week_range
from bot.responses import send_private
from bot.views.confirm_view import ConfirmView
from bot.views.review_view import ReviewButton, apply_action, refresh_review_message, send_detail

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)

PENDING_LIST_LIMIT = 10


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
        if not checks.is_admin(interaction):
            raise AdminOnly()
        return True

    @admin.command(name="회원목록", description="등록된 회원과 상태를 봅니다.")
    async def member_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        members = await self.bot.services.members.list_all()
        await send_private(interaction, embed=members_embed(members))

    @admin.command(name="검토목록", description="관리자 확인이 필요한 인증을 봅니다.")
    async def pending_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = self.bot.services.settings
        items, total = await self.bot.services.reviews.list_pending(limit=PENDING_LIST_LIMIT)
        embed = pending_embed(
            items, total, guild_id=settings.guild_id, admin_channel_id=settings.admin_channel_id
        )
        view = None
        if items:
            view = discord.ui.View(timeout=None)
            for item in items:
                button = ReviewButton("detail", item.verification_id)
                button.item.label = f"#{item.verification_id} 상세"
                view.add_item(button)
        await send_private(interaction, embed=embed, view=view)

    @admin.command(name="인증상세", description="인증 원문, 사진, 판정 이력을 봅니다.")
    @app_commands.rename(verification_id="번호")
    @app_commands.describe(verification_id="인증 번호 (#뒤의 숫자)")
    async def detail(
        self, interaction: discord.Interaction, verification_id: app_commands.Range[int, 1]
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await send_detail(interaction, verification_id)

    @admin.command(name="출석취소", description="승인된 인증의 출석을 취소합니다.")
    @app_commands.rename(verification_id="번호", reason="사유")
    @app_commands.describe(verification_id="인증 번호", reason="회원에게 전달되는 취소 사유")
    async def revoke(
        self,
        interaction: discord.Interaction,
        verification_id: app_commands.Range[int, 1],
        reason: app_commands.Range[str, 1, MAX_REASON_LENGTH],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        outcome = await apply_action(
            self.bot,
            "revoke",
            verification_id,
            actor_discord_id=interaction.user.id,
            reason=reason,
        )
        await send_private(
            interaction,
            f"인증 #{verification_id}({outcome.member_display_name})의 출석을 취소했습니다.",
        )
        await refresh_review_message(self.bot, outcome)

    @admin.command(
        name="목표설정", description="주간 출석 목표를 바꿉니다. 기본은 다음 주부터 적용합니다."
    )
    @app_commands.rename(target="횟수", apply="적용")
    @app_commands.describe(target="주간 목표 횟수 (1~7)", apply="적용 시점")
    async def set_target(
        self,
        interaction: discord.Interaction,
        target: app_commands.Range[int, 1, 7],
        apply: Literal["다음주", "이번주"] = "다음주",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        services = self.bot.services
        actor = interaction.user.id
        if apply == "다음주":
            week = await services.policies.set_target(
                target, this_week=False, actor_discord_id=actor
            )
            await send_private(
                interaction, f"다음 주({week_range(week)})부터 주간 목표를 {target}회로 바꿨습니다."
            )
            return

        # 이번 주 적용: 달성 여부가 바로 바뀌므로 영향을 먼저 보여주고 확인을 받는다.
        stats = await services.statistics.week_stats()
        after = sum(1 for row in stats.rows if row.count >= target)
        embed = discord.Embed(
            title="이번 주 목표 변경 확인",
            description=(
                f"이번 주({week_range(stats.week)}) 목표: **{stats.target}회 → {target}회**\n"
                f"달성 회원: **{stats.achieved_count}명 → {after}명**"
                f" (집계 대상 {stats.member_count}명)"
            ),
            color=discord.Color.orange(),
        )

        async def confirm() -> str:
            await services.policies.set_target(target, this_week=True, actor_discord_id=actor)
            return f"이번 주부터 주간 목표를 {target}회로 바꿨습니다."

        view = ConfirmView(owner_id=actor, on_confirm=confirm)
        view.message = await send_private(interaction, embed=embed, view=view)

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
