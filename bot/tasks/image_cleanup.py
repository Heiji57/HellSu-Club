"""보관 기간이 지난 인증 이미지 정리. 시작 직후와 이후 하루마다 실행한다."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)


class ImageCleanupTask(commands.Cog):
    def __init__(self, bot: GymBot) -> None:
        self.bot = bot

    def start(self) -> None:
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    @tasks.loop(hours=24)
    async def loop(self) -> None:
        try:
            await self.bot.services.image_cleanup.run()
        except Exception:
            log.exception("이미지 정리 실패. 내일 다시 시도합니다.")
