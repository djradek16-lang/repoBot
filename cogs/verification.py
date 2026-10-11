"""
Moduł weryfikacji nowych użytkowników.
Wysyła panele weryfikacyjne oraz zarządza przypisywaniem ról dostępowych po weryfikacji.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from views.verification_views import VerificationButtonView
from utils.embeds import success_embed, error_embed, info_embed

logger = logging.getLogger("AegisBot.Cogs.Verification")

class VerificationCog(commands.Cog):
    """Zarządzanie procesem weryfikacji użytkowników."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Rejestracja trwałego widoku przycisku weryfikacyjnego."""
        self.bot.add_view(VerificationButtonView())

    verify_group = app_commands.Group(
        name="verify",
        description="Zarządzanie weryfikacją serwera",
        default_permissions=discord.Permissions(administrator=True)
    )

    @verify_group.command(name="setup", description="Wysyła panel weryfikacyjny z przyciskiem na ten kanał.")
    @app_commands.describe(mode="Tryb weryfikacji (przycisk, matematyczny, kod captcha)")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Zwykły Przycisk", value="button"),
        app_commands.Choice(name="Zadanie Matematyczne", value="math"),
        app_commands.Choice(name="Kod CAPTCHA", value="code"),
    ])
    @is_administrator()
    async def setup_verification(self, interaction: discord.Interaction, mode: str = "button") -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, verification_mode, verification_enabled) VALUES (?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET verification_mode = ?, verification_enabled = 1
            """,
            (interaction.guild_id, mode, mode)
        )

        embed = info_embed(
            "🔒 Weryfikacja Użytkowników",
            "Kliknij poniższy przycisk **'Zweryfikuj się'**, aby przejść weryfikację i uzyskać pełny dostęp do kanałów serwera."
        )
        embed.set_footer(text="AegisBot Verification")

        await interaction.channel.send(embed=embed, view=VerificationButtonView())
        await interaction.response.send_message(embed=success_embed("Skonfigurowano Weryfikację", f"Panel weryfikacyjny (tryb: `{mode}`) został pomyślnie wysłany."), ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VerificationCog(bot))
