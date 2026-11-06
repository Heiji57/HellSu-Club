"""setup_hook: DB 확인, 초기 목표 시드, 영구 버튼 등록, 주기 작업 시작, 길드 명령 동기화."""

import asyncio

from sqlalchemy import func, select

from app.models import ClubPolicy


async def test_setup_hook(bot_env, monkeypatch):
    bot = bot_env.bot
    synced = {}

    async def fake_sync(*, guild):
        synced["guild"] = guild.id
        return list(bot.tree.get_commands(guild=guild))

    monkeypatch.setattr(bot.tree, "sync", fake_sync)
    # 로그인하지 않은 봇이라 준비 완료를 기다리기만 하게 한다(작업이 실제로 돌지는 않음)
    monkeypatch.setattr(bot, "wait_until_ready", lambda: asyncio.Event().wait())
    await bot.setup_hook()
    try:
        assert synced["guild"] == bot.services.settings.guild_id
        assert bot.started_at is not None
        assert "review:" in next(iter(bot._connection._view_store._dynamic_items)).pattern
        assert bot.get_cog("WeeklyReportTask").loop.is_running()
        assert bot.get_cog("ImageCleanupTask").loop.is_running()
        async with bot_env.db.transaction() as s:
            assert await s.scalar(select(func.count()).select_from(ClubPolicy)) == 1
    finally:
        bot.get_cog("WeeklyReportTask").loop.cancel()
        bot.get_cog("ImageCleanupTask").loop.cancel()
