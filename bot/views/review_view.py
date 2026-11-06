"""검토 버튼과 사유 모달 (REQUIREMENTS 6.1).

버튼은 DynamicItem이다. custom_id에 동작과 인증 번호를 담아 두므로, 봇이 재시작되어도
`bot.add_dynamic_items(ReviewButton)`만 해 두면 예전 메시지의 버튼이 그대로 동작한다.
권한은 누를 때마다 관리자 역할로 다시 확인한다.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import discord

from app.models import VerificationStatus
from app.services.review_service import MAX_REASON_LENGTH, ReviewOutcome
from bot import checks
from bot.embeds.review import detail_embed, mark_processed
from bot.responses import send_private, user_message_for

if TYPE_CHECKING:
    from bot.main import GymBot

log = logging.getLogger(__name__)

ACTIONS: dict[str, tuple[str, discord.ButtonStyle]] = {
    "approve": ("승인", discord.ButtonStyle.success),
    "reject": ("거절", discord.ButtonStyle.danger),
    "revoke": ("출석 취소", discord.ButtonStyle.danger),
    "detail": ("상세", discord.ButtonStyle.secondary),
}
DONE_TEXT = {"approve": "승인했습니다", "reject": "거절했습니다", "revoke": "출석을 취소했습니다"}


def actions_for(status: VerificationStatus) -> list[str]:
    if status is VerificationStatus.REVIEW_REQUIRED:
        return ["approve", "reject", "detail"]
    if status is VerificationStatus.APPROVED:
        return ["revoke", "detail"]
    return ["detail"]


def review_view(verification_id: int, status: VerificationStatus, *, detail: bool = True):
    """상태에 맞는 버튼 묶음. timeout=None이어야 영구 버튼으로 쓸 수 있다."""
    view = discord.ui.View(timeout=None)
    for action in actions_for(status):
        if action == "detail" and not detail:
            continue
        view.add_item(ReviewButton(action, verification_id))
    return view


class ReviewButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"review:(?P<action>approve|reject|revoke|detail):(?P<vid>[0-9]+)",
):
    def __init__(self, action: str, verification_id: int) -> None:
        label, style = ACTIONS[action]
        super().__init__(
            discord.ui.Button(
                label=label, style=style, custom_id=f"review:{action}:{verification_id}"
            )
        )
        self.action = action
        self.verification_id = verification_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ) -> ReviewButton:
        return cls(match["action"], int(match["vid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not checks.is_admin(interaction):
            await send_private(interaction, checks.AdminOnly.user_message)
            return
        if self.action == "detail":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await send_detail(interaction, self.verification_id)
            return
        await interaction.response.send_modal(ReasonModal(self.action, self.verification_id))


class ReasonModal(discord.ui.Modal):
    def __init__(self, action: str, verification_id: int) -> None:
        super().__init__(title=f"인증 #{verification_id} {ACTIONS[action][0]}", timeout=600)
        self.action = action
        self.verification_id = verification_id
        required = action != "approve"
        self.reason: discord.ui.TextInput = discord.ui.TextInput(
            label="사유" if required else "사유 (선택)",
            style=discord.TextStyle.paragraph,
            required=required,
            max_length=MAX_REASON_LENGTH,
            placeholder="회원에게 그대로 전달됩니다." if required else None,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not checks.is_admin(interaction):
            await send_private(interaction, checks.AdminOnly.user_message)
            return
        outcome = await apply_action(
            checks.bot_of(interaction),
            self.action,
            self.verification_id,
            actor_discord_id=interaction.user.id,
            reason=self.reason.value,
        )
        await send_private(
            interaction, f"인증 #{self.verification_id}을(를) {DONE_TEXT[self.action]}."
        )
        await refresh_review_message(checks.bot_of(interaction), outcome)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        message = user_message_for(error)
        if message == user_message_for(RuntimeError()):
            log.error("검토 처리 실패: #%d %s", self.verification_id, self.action, exc_info=error)
        await send_private(interaction, message)


async def apply_action(
    bot: GymBot, action: str, verification_id: int, *, actor_discord_id: int, reason: str | None
) -> ReviewOutcome:
    reviews = bot.services.reviews
    if action == "approve":
        return await reviews.approve(
            verification_id, actor_discord_id=actor_discord_id, reason=reason
        )
    if action == "reject":
        return await reviews.reject(
            verification_id, actor_discord_id=actor_discord_id, reason=reason
        )
    if action == "revoke":
        return await reviews.revoke(
            verification_id, actor_discord_id=actor_discord_id, reason=reason
        )
    raise ValueError(action)


async def admin_channel(bot: GymBot) -> discord.abc.Messageable | None:
    channel_id = bot.services.settings.admin_channel_id
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            log.warning("관리자 채널을 찾을 수 없습니다: %s", channel_id, exc_info=True)
            return None
    return channel if isinstance(channel, discord.abc.Messageable) else None


async def refresh_review_message(bot: GymBot, outcome: ReviewOutcome) -> None:
    """관리자 채널의 검토 요청 메시지에 결과를 표시하고, 지금 상태에 맞는 버튼만 남긴다."""
    if outcome.review_message_id is None:
        return
    channel = await admin_channel(bot)
    if channel is None:
        return
    try:
        message = await channel.fetch_message(outcome.review_message_id)
        if not message.embeds:
            return
        await message.edit(
            embed=mark_processed(message.embeds[0], outcome),
            view=review_view(outcome.verification_id, outcome.status),
        )
    except discord.HTTPException:
        log.warning("검토 메시지 갱신 실패: #%d", outcome.verification_id, exc_info=True)


async def send_detail(interaction: discord.Interaction, verification_id: int) -> None:
    """인증 상세를 본인에게만 보여준다. 사진이 남아 있으면 첨부한다."""
    bot = checks.bot_of(interaction)
    settings = bot.services.settings
    detail = await bot.services.reviews.detail(verification_id)

    kwargs: dict[str, Any] = {}
    image_url = None
    if detail.image_file is not None:
        filename = f"verification-{verification_id}{detail.image_file.suffix}"
        kwargs["file"] = discord.File(detail.image_file, filename=filename)
        image_url = f"attachment://{filename}"
    embed = detail_embed(
        detail,
        guild_id=settings.guild_id,
        admin_channel_id=settings.admin_channel_id,
        image_url=image_url,
    )
    view = review_view(verification_id, detail.status, detail=False)
    if not view.children:
        view = None
    await interaction.followup.send(embed=embed, view=view, ephemeral=True, **kwargs)
