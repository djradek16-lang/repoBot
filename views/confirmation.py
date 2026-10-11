"""
Widok dialogowy dwustanowych przycisków potwierdzenia (Tak / Nie).
"""

import discord

class ConfirmView(discord.ui.View):
    """Przyciskowe okno dialogowe z zapytaniem o potwierdzenie akcji."""

    def __init__(self, author_id: int, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ta interakcja należy do innej osoby.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Tak, potwierdzam", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Anuluj", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        self.stop()
        await interaction.response.defer()
