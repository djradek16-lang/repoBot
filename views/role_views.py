"""
Trwałe widoki paneli wybieralnych ról (Reaction Roles / Button Roles / Select Roles).
"""

import logging
import discord

from utils.embeds import success_embed, error_embed

logger = logging.getLogger("AegisBot.RoleViews")

class DynamicRoleButton(discord.ui.Button):
    """Dynamiczny przycisk nadający/odbierający konkretną rolę."""

    def __init__(self, role_id: int, label: str, emoji: str | None = None, style: discord.ButtonStyle = discord.ButtonStyle.primary) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"role_btn:{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user

        if not guild or not isinstance(member, discord.Member):
            return

        role = guild.get_role(self.role_id)
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


class DynamicRoleSelect(discord.ui.Select):
    """Menu rozwijane z rolami do wyboru."""

    def __init__(self, options: list[discord.SelectOption], custom_id: str = "dynamic_role_select") -> None:
        super().__init__(
            placeholder="Wybierz role...",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=custom_id
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            return

        selected_role_ids = [int(v) for v in self.values]
        
        # Pobierz wszystkie role ze znanych opcji
        all_option_role_ids = [int(opt.value) for opt in self.options]

        added, removed = [], []

        for rid in all_option_role_ids:
            role = guild.get_role(rid)
            if not role:
                continue

            if rid in selected_role_ids and role not in member.roles:
                await member.add_roles(role, reason="Role Select Panel")
                added.append(role.mention)
            elif rid not in selected_role_ids and role in member.roles:
                await member.remove_roles(role, reason="Role Select Panel")
                removed.append(role.mention)

        msg = "Zaktualizowano Twoje role!"
        if added:
            msg += f"\n**Dodane:** {', '.join(added)}"
        if removed:
            msg += f"\n**Usunięte:** {', '.join(removed)}"

        await interaction.response.send_message(embed=success_embed("Zaktualizowano Role", msg), ephemeral=True)


class DynamicRoleView(discord.ui.View):
    """Trwały widok zawierający listę przycisków ról."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
