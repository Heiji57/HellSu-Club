from collections.abc import Awaitable, Callable

import discord

PageRenderer = Callable[[int], Awaitable[tuple[discord.Embed, int, int]]]
"""페이지 번호(1부터) → (Embed, 실제 페이지, 전체 페이지 수)."""


class PaginationView(discord.ui.View):
    """이전/다음 버튼. 명령을 실행한 사람만 누를 수 있고, 시간이 지나면 버튼을 끈다."""

    def __init__(
        self, *, owner_id: int, page: int, total_pages: int, render: PageRenderer, timeout=300
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.page = page
        self.total_pages = total_pages
        self._render = render
        self.message: discord.Message | None = None
        self._refresh()

    def _refresh(self) -> None:
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= self.total_pages
        self.page_label.label = f"{self.page} / {self.total_pages}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "명령어를 실행한 사람만 넘길 수 있습니다.", ephemeral=True
            )
            return False
        return True

    async def _go(self, interaction: discord.Interaction, page: int) -> None:
        embed, self.page, self.total_pages = await self._render(page)
        self._refresh()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._go(interaction, self.page - 1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._go(interaction, self.page + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # 메시지가 지워졌거나 토큰이 만료됨
