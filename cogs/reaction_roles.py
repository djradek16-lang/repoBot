"""
Moduł wyboru ról (Reaction Roles / Button Roles / Select Roles).
Pozwala administratorom tworzyć interaktywne panele z przyciskami i menu rozwijanymi ról dla członków serwera.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from views.role_views import DynamicRoleView, DynamicRoleButton, DynamicRoleSelect
from utils.embeds import success_embed, error_embed, info_embed

logger = logging.getLogger("AegisBot.Cogs.ReactionRoles")

class ReactionRolesCog(commands.Cog):
    """Zarządzanie wybieralnymi rolami i panelami przycisków."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(DynamicRoleView())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("role_btn:"):
            return

        try:
            role_id = int(custom_id.split(":", 1)[1])
        except ValueError:
            return

        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            return

        role = guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(embed=error_embed("Błąd", "Ta rola nie istnieje już na serwerze."), ephemeral=True)
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Role Panel (odbieranie)")
                await interaction.response.send_message(embed=success_embed("Odebrano Rolę", f"Odebrano rolę {role.mention}."), ephemeral=True)
            else:
                await member.add_roles(role, reason="Role Panel (nadawanie)")
                await interaction.response.send_message(embed=success_embed("Nadano Rolę", f"Otrzymałeś rolę {role.mention}."), ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd Uprawnień", f"Nie udało się zmienić ról: {e}"), ephemeral=True)

    role_group = app_commands.Group(
        name="rolepanel",
        description="Tworzenie i zarządzanie panelami ról",
        default_permissions=discord.Permissions(administrator=True)
    )

    @role_group.command(name="create", description="Tworzy panel z przyciskiem do nadawania wybranej roli.")
    @app_commands.describe(role="Rola do wyboru", title="Tytuł panelu", description="Opis panelu ról", emoji="Emoji przycisku (opcjonalnie)")
    @is_administrator()
    async def create_panel(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        title: str = "Wybierz Rolę",
        description: str = "Kliknij poniższy przycisk, aby odebrać lub nadanie roli.",
        emoji: str = None
    ) -> None:
        view = DynamicRoleView()
        button = DynamicRoleButton(role_id=role.id, label=role.name, emoji=emoji)
        view.add_item(button)

        embed = info_embed(title, f"{description}\n\n🎭 **Dostępna rola:** {role.mention}")
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(embed=success_embed("Utworzono Panel", f"Panel ról dla {role.mention} został wysłany."), ephemeral=True)

    @role_group.command(name="create_menu", description="Tworzy panel z rozwijanym menu (Select Menu) do wyboru wielu ról.")
    @app_commands.describe(title="Tytuł panelu", role1="Rola 1", role2="Rola 2 (opcjonalnie)", role3="Rola 3 (opcjonalnie)", role4="Rola 4 (opcjonalnie)")
    @is_administrator()
    async def create_menu(
        self,
        interaction: discord.Interaction,
        title: str,
        role1: discord.Role,
        role2: discord.Role = None,
        role3: discord.Role = None,
        role4: discord.Role = None
    ) -> None:
        roles = [r for r in [role1, role2, role3, role4] if r is not None]
        options = [discord.SelectOption(label=r.name, value=str(r.id), emoji="🎭") for r in roles]

        view = discord.ui.View(timeout=None)
        select = DynamicRoleSelect(options=options, custom_id=f"role_select_menu:{interaction.id}")
        view.add_item(select)

        roles_mentions = "\n".join(f"• {r.mention}" for r in roles)
        embed = info_embed(title, f"Wybierz interesujące Cię role z poniższego menu rozwijanego:\n\n{roles_mentions}")
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(embed=success_embed("Utworzono Menu Ról", "Panel z rozwijanym menu został wysłany."), ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRolesCog(bot))
