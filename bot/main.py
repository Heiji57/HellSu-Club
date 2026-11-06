"""봇 진입점: `python -m bot.main`"""

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord.ext import commands

from app.container import Services
from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.database import Database
from bot.commands.admin import AdminCommands
from bot.commands.me import MeCommands
from bot.commands.verification import VerificationCommands
from bot.events.members import MemberEvents
from bot.responses import GymCommandTree
from bot.views.review_view import ReviewButton

log = logging.getLogger("bot")


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True  # 역할 변경·탈퇴 감지 (Server Members Intent, ADR-0004)
    return intents


class GymBot(commands.Bot):
    def __init__(self, services: Services) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(),
            tree_cls=GymCommandTree,
            help_command=None,
        )
        self.services = services
        self.started_at: datetime | None = None

    async def add_all_cogs(self) -> None:
        await self.add_cog(MemberEvents(self))
        await self.add_cog(VerificationCommands(self))
        await self.add_cog(MeCommands(self))
        await self.add_cog(AdminCommands(self))

    async def setup_hook(self) -> None:
        # DB에 연결할 수 없으면 바로 종료한다(컨테이너가 재시작).
        await self.services.db.ping()
        await self.services.policies.ensure_initial()
        await self.add_all_cogs()
        # 재시작 전에 게시한 검토 메시지의 버튼도 동작하게 한다 (Persistent View)
        self.add_dynamic_items(ReviewButton)

        guild = discord.Object(id=self.services.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("길드 명령어 %d개 동기화: %s", len(synced), ", ".join(c.name for c in synced))
        self.started_at = datetime.now(UTC)

    async def close(self) -> None:
        await self.services.aclose()
        await super().close()

    async def on_ready(self) -> None:
        log.info("로그인: %s (id=%s)", self.user, self.user.id if self.user else "?")


async def run() -> None:
    settings = get_settings()
    discord.utils.setup_logging(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    db = Database(settings.database_url)
    services = Services.build(settings, db, SystemClock())
    bot = GymBot(services)
    try:
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(run())
