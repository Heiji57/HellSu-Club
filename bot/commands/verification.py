"""/인증 명령 (REQUIREMENTS 3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from app.core.exceptions import WrongChannel
from app.services.verification_service import (
    MAX_CONTENT_LENGTH,
    AttachmentInfo,
    Submission,
    SubmissionResult,
)
from bot.checks import ensure_member
from bot.embeds.verification import review_request_embed, submission_embed
from bot.responses import send_private

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)


class VerificationCommands(commands.Cog):
    def __init__(self, bot: GymBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="인증", description="오늘의 운동을 인증합니다. 하루에 한 번 할 수 있습니다."
    )
    @app_commands.guild_only()
    @app_commands.rename(content="내용", photo="사진")
    @app_commands.describe(
        content="운동 내용. 사진 없이 올릴 때는 조금 더 자세히 적어 주세요.",
        photo="운동 사진 (JPEG, PNG, WEBP)",
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        content: app_commands.Range[str, 1, MAX_CONTENT_LENGTH] | None = None,
        photo: discord.Attachment | None = None,
    ) -> None:
        services = self.bot.services
        channel_id = services.settings.verification_channel_id
        if channel_id is not None and interaction.channel_id != channel_id:
            raise WrongChannel(f"<#{channel_id}> 채널에서만 인증할 수 있습니다.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ensure_member(interaction)
        user = interaction.user

        result = await services.verifications.submit(
            Submission(
                discord_user_id=user.id,
                interaction_id=interaction.id,
                channel_id=interaction.channel_id or 0,
                submitted_at=interaction.created_at,
                content=content,
                attachment=(
                    AttachmentInfo(url=photo.url, filename=photo.filename, size=photo.size)
                    if photo
                    else None
                ),
            )
        )
        await send_private(interaction, embed=submission_embed(result))
        if not result.approved:
            await self._post_review_request(interaction, result, photo)

    async def _post_review_request(
        self,
        interaction: discord.Interaction,
        result: SubmissionResult,
        photo: discord.Attachment | None,
    ) -> None:
        """관리자 채널에 검토 요청을 게시한다. 실패해도 인증은 검토 대기 상태로 남는다."""
        settings = self.bot.services.settings
        try:
            channel = self.bot.get_channel(settings.admin_channel_id) or (
                await self.bot.fetch_channel(settings.admin_channel_id)
            )
            if not isinstance(channel, discord.abc.Messageable):
                log.error("ADMIN_CHANNEL_ID가 메시지 채널이 아닙니다: %s", channel)
                return

            kwargs: dict[str, Any] = {"allowed_mentions": discord.AllowedMentions.none()}
            image_url = None
            if result.image_file is not None and result.image_file.exists():
                filename = f"verification-{result.verification_id}.{result.image_ext}"
                kwargs["file"] = discord.File(result.image_file, filename=filename)
                image_url = f"attachment://{filename}"
            elif photo is not None:
                image_url = photo.url  # 파일 저장에 실패한 경우. 원본 URL은 나중에 만료된다.

            kwargs["embed"] = review_request_embed(
                result,
                member=interaction.user,
                submitted_at=interaction.created_at,
                guild_id=settings.guild_id,
                admin_channel_id=settings.admin_channel_id,
                image_url=image_url,
            )
            message = await channel.send(**kwargs)
        except discord.HTTPException:
            log.warning("인증 #%d 검토 요청 게시 실패", result.verification_id, exc_info=True)
            return
        await self.bot.services.verifications.set_review_message(result.verification_id, message.id)
