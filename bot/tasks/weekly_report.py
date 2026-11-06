"""주간 리포트 자동 게시. 5분마다(시작 직후 포함) 게시 시각이 지난 주를 확인한다."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from app.services.report_service import PublishStatus, ReportSnapshot
from bot.embeds.report import report_embed

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)


async def post_report(bot: GymBot, snapshot: ReportSnapshot) -> int:
    """리포트 채널에 게시하고 메시지 ID를 돌려준다. 실패하면 예외를 던져 재시도하게 한다."""
    channel_id = bot.services.settings.report_channel_id
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise RuntimeError(f"REPORT_CHANNEL_ID가 메시지 채널이 아닙니다: {channel_id}")
    message = await channel.send(
        embed=report_embed(snapshot), allowed_mentions=discord.AllowedMentions.none()
    )
    return message.id


class WeeklyReportTask(commands.Cog):
    def __init__(self, bot: GymBot) -> None:
        self.bot = bot

    def start(self) -> None:
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    @tasks.loop(minutes=5)
    async def loop(self) -> None:
        try:
            result = await self.bot.services.reports.publish_due(
                lambda snapshot: post_report(self.bot, snapshot)
            )
        except Exception:
            log.exception("주간 리포트 게시 실패. 다음 주기에 다시 시도합니다.")
            return
        if result.status is PublishStatus.POSTED:
            log.info("주간 리포트 게시 완료: %s", result.week)

    @loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()
