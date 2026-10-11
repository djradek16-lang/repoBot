"""
Uniwersalny widok paginacji embedów z przyciskami (Nawigacja stronicowana).
"""

import discord

class PaginationView(discord.ui.View):
    """Paginator embedów dla list, tabel, skrótów i rankingów."""

    def __init__(self, pages: list[discord.Embed], author_id: int, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.pages = pages
        self.author_id = author_id
        self.current_page = 0

        self.update_buttons()

    def update_buttons(self) -> None:
        """Odświeża stan aktywności przycisków w zależności od bieżącej strony."""
        total = len(self.pages)
        self.btn_first.disabled = self.current_page == 0
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page >= total - 1
        self.btn_last.disabled = self.current_page >= total - 1
        self.btn_counter.label = f"{self.current_page + 1} / {total}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ta nawigacja należy do innego użytkownika.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
    async def btn_first(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page > 0:
            self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def btn_counter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def btn_last(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = len(self.pages) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
