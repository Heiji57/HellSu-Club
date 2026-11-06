"""지난 인증 결과 알림 (ADR-0005).

회원 명령의 응답을 보낸 뒤 호출한다. 관리자가 나중에 결정한 결과가 있으면
본인에게만 보이는 followup으로 함께 보낸다. 알림이 실패해도 명령은 실패하지 않는다.
"""

import logging

import discord

from bot import checks
from bot.embeds.review import notices_embed

log = logging.getLogger(__name__)


async def deliver_notices(interaction: discord.Interaction) -> int:
    if not checks.is_member(interaction):
        return 0
    try:
        notices = await checks.bot_of(interaction).services.notifications.claim(interaction.user.id)
        if notices:
            await interaction.followup.send(embed=notices_embed(notices), ephemeral=True)
        return len(notices)
    except Exception:
        log.warning("지난 결과 알림 실패: user=%s", interaction.user.id, exc_info=True)
        return 0
