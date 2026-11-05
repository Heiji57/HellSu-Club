"""명령 응답 공통 처리: 본인에게만 보이는 응답, 예외 → 사용자 메시지 변환."""

import logging

import discord
from discord import app_commands
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.exceptions import AppError

log = logging.getLogger(__name__)

GENERIC_ERROR = "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
DB_ERROR = "데이터베이스에 연결할 수 없어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."


def user_message_for(error: BaseException) -> str:
    """예외를 사용자에게 보여줄 메시지로 바꾼다. 예상하지 못한 예외는 내부 내용을 숨긴다."""
    original = getattr(error, "original", error)
    if isinstance(original, AppError) or isinstance(getattr(original, "user_message", None), str):
        return original.user_message  # type: ignore[union-attr]
    if isinstance(original, OperationalError | DBAPIError | ConnectionError):
        return DB_ERROR
    if isinstance(error, app_commands.NoPrivateMessage):
        return "서버 채널에서만 사용할 수 있는 명령어입니다."
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"잠시 후 다시 시도해 주세요. ({error.retry_after:.0f}초)"
    if isinstance(error, app_commands.CheckFailure):
        return "이 명령어를 사용할 권한이 없습니다."
    return GENERIC_ERROR


async def send_private(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
) -> None:
    """본인에게만 보이는 메시지. 이미 응답(defer 포함)했다면 followup으로 보낸다."""
    kwargs: dict = {"content": content, "ephemeral": True}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


class GymCommandTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        message = user_message_for(error)
        original = getattr(error, "original", error)
        if message in (GENERIC_ERROR, DB_ERROR):
            command = interaction.command.qualified_name if interaction.command else "?"
            log.error("명령 처리 실패: /%s", command, exc_info=original)
        try:
            await send_private(interaction, message)
        except discord.HTTPException:
            log.warning("오류 메시지 전송 실패", exc_info=True)
