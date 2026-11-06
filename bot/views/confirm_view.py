import logging
from collections.abc import Awaitable, Callable

import discord

from bot import checks
from bot.responses import GENERIC_ERROR, user_message_for

log = logging.getLogger(__name__)


class ConfirmView(discord.ui.View):
    """확인/취소. 명령을 실행한 관리자만 누를 수 있다.

    확인을 누르면 on_confirm을 실행하고, 반환한 문구로 메시지를 바꾼다.
    """

    def __init__(
        self, *, owner_id: int, on_confirm: Callable[[], Awaitable[str]], timeout: float = 120
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self._on_confirm = on_confirm
        self.message: discord.Message | None = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not checks.is_admin(interaction):
            await interaction.response.send_message(
                "명령어를 실행한 관리자만 누를 수 있습니다.", ephemeral=True
            )
            return False
        return True

    def _close(self) -> None:
        self.done = True
        self.stop()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(label="변경", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self._close()
        try:
            text = await self._on_confirm()
        except Exception as e:  # 오류도 같은 메시지에 보여준다
            text = user_message_for(e)
            if text == GENERIC_ERROR:
                log.exception("확인 처리 실패")
        await interaction.response.edit_message(content=text, embed=None, view=self)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self._close()
        await interaction.response.edit_message(
            content="변경하지 않았습니다.", embed=None, view=self
        )

    async def on_timeout(self) -> None:
        if self.done or self.message is None:
            return
        self._close()
        try:
            await self.message.edit(content="시간이 지나 취소되었습니다.", embed=None, view=self)
        except discord.HTTPException:
            pass
