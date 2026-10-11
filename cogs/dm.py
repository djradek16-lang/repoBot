"""
Moduł wysyłania prywatnych wiadomości (DM) do graczy w formie prostego embeda.
Przeznaczony dla zarządu/administracji do przekazywania graczom indywidualnych
wiadomości (np. decyzji, ostrzeżeń pozaregulaminowych, informacji) bez
konieczności pisania z prywatnego konta.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import create_embed, success_embed, error_embed
import config

logger = logging.getLogger("AegisBot.Cogs.DM")


class DMCog(commands.Cog):
    """Wysyłanie prywatnych wiadomości do graczy w formie embeda."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="dm", description="Wysyła graczowi prywatną wiadomość w formie embeda.")
    @app_commands.describe(
        gracz="Gracz, do którego ma trafić wiadomość.",
        tytul="Tytuł wiadomości.",
        tresc="Treść wiadomości.",
    )
    @app_commands.default_permissions(administrator=True)
    @is_administrator()
    async def dm(
        self,
        interaction: discord.Interaction,
        gracz: discord.Member,
        tytul: str,
        tresc: str,
    ) -> None:
        if gracz.bot:
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Nie można wysłać prywatnej wiadomości do bota."),
                ephemeral=True
            )
            return

        embed = create_embed(
            title=tytul,
            description=tresc,
            color=config.DEFAULT_EMBED_COLOR,
        )
        embed.set_author(
            name=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        embed.set_footer(text=f"Wiadomość od zarządu • {interaction.guild.name}")

        try:
            await gracz.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Nie udało się wysłać DM do {gracz} ({gracz.id}): {e}")
            await interaction.response.send_message(
                embed=error_embed(
                    "Nie udało się wysłać wiadomości",
                    f"{gracz.mention} ma najprawdopodobniej zablokowane wiadomości prywatne od członków serwera."
                ),
                ephemeral=True
            )
            return

        logger.info(f"{interaction.user} ({interaction.user.id}) wysłał DM do {gracz} ({gracz.id}): '{tytul}'")
        await interaction.response.send_message(
            embed=success_embed("Wiadomość wysłana", f"Wiadomość została wysłana do {gracz.mention}."),
            ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DMCog(bot))