"""
Moduł administracyjny do konfiguracji serwera (/config).
Umożliwia ustawianie kanałów, ról oraz opcji systemowych w bazie danych guild_config.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import success_embed, error_embed, info_embed
from views.application_views import _get_accept_role_ids, ApplicationStartButton, build_recruitment_post_embed

logger = logging.getLogger("AegisBot.Cogs.Administration")

class AdministrationCog(commands.Cog):
    """Zarządzanie ustawieniami i konfiguracją serwera."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    config_group = app_commands.Group(
        name="config",
        description="Zarządzanie konfiguracją serwera",
        default_permissions=discord.Permissions(administrator=True)
    )

    @config_group.command(name="show", description="Wyświetla bieżącą konfigurację bota dla tego serwera.")
    @is_administrator()
    async def config_show(self, interaction: discord.Interaction) -> None:
        row = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        if not row:
            await interaction.response.send_message(embed=info_embed("Konfiguracja Serwera", "Konfiguracja serwera nie została jeszcze utworzona. Użyj komend `/config set_channel` itp."))
            return

        embed = info_embed(f"⚙️ Konfiguracja Serwera {interaction.guild.name}", "Bieżące ustawienia bota:")
        
        wch = f"<#{row['welcome_channel_id']}>" if row['welcome_channel_id'] else "Nieustawiony"
        gch = f"<#{row['goodbye_channel_id']}>" if row['goodbye_channel_id'] else "Nieustawiony"
        lch = f"<#{row['log_channel_id']}>" if row['log_channel_id'] else "Nieustawiony"
        sch = f"<#{row['suggestion_channel_id']}>" if row['suggestion_channel_id'] else "Nieustawiony"
        tch = f"<#{row['ticket_channel_id']}>" if row['ticket_channel_id'] else "Nieustawiony"
        stch = f"<#{row['stats_channel_id']}>" if row['stats_channel_id'] else "Nieustawiony"
        wstch = f"<#{row['weekly_stats_channel_id']}>" if "weekly_stats_channel_id" in row.keys() and row['weekly_stats_channel_id'] else "Nieustawiony"

        channels_info = (
            f"**Powitalny:** {wch}\n"
            f"**Pożegnalny:** {gch}\n"
            f"**Logi:** {lch}\n"
            f"**Sugestie:** {sch}\n"
            f"**Tickety:** {tch}\n"
            f"**Raport statystyk głosowych (dzienny):** {stch}\n"
            f"**Raport zarządu (środy 23:59):** {wstch}"
        )
        embed.add_field(name="📍 Kanały", value=channels_info, inline=False)

        vrole = f"<@&{row['verified_role_id']}>" if row['verified_role_id'] else "Nieustawiona"
        urole = f"<@&{row['unverified_role_id']}>" if row['unverified_role_id'] else "Nieustawiona"
        arole = f"<@&{row['auto_role_id']}>" if row['auto_role_id'] else "Nieustawiona"
        mrole = f"<@&{row['mod_role_id']}>" if row['mod_role_id'] else "Nieustawiona"

        roles_info = (
            f"**Rola Zweryfikowana:** {vrole}\n"
            f"**Rola Niezweryfikowana:** {urole}\n"
            f"**Auto Rola:** {arole}\n"
            f"**Rola Moderatora:** {mrole}"
        )
        embed.add_field(name="🎭 Role", value=roles_info, inline=False)

        apch = f"<#{row['applications_channel_id']}>" if "applications_channel_id" in row.keys() and row["applications_channel_id"] else "Nieustawiony"
        aprch = f"<#{row['applications_review_channel_id']}>" if "applications_review_channel_id" in row.keys() and row["applications_review_channel_id"] else "Nieustawiony"
        amrole = f"<@&{row['applications_management_role_id']}>" if "applications_management_role_id" in row.keys() and row["applications_management_role_id"] else "Nieustawiona"

        accept_role_ids = await _get_accept_role_ids(self.bot.db, interaction.guild_id)
        if accept_role_ids:
            aaroles = ", ".join(f"<@&{rid}>" for rid in accept_role_ids)
        else:
            aaroles = "Nieustawiona"

        applications_info = (
            f"**Kanał zgłoszeń:** {apch}\n"
            f"**Kanał przeglądu:** {aprch}\n"
            f"**Rola sztabu (ping):** {amrole}\n"
            f"**Role po akceptacji:** {aaroles}"
        )
        embed.add_field(name="📥 Rekrutacja", value=applications_info, inline=False)

        toggles_info = (
            f"**Powitania:** {'✅ Włączone' if row['welcome_enabled'] else '❌ Wyłączone'}\n"
            f"**Pożegnania:** {'✅ Włączone' if row['goodbye_enabled'] else '❌ Wyłączone'}\n"
            f"**Tickety:** {'✅ Włączone' if row['tickets_enabled'] else '❌ Wyłączone'}\n"
            f"**Weryfikacja:** {'✅ Włączone' if row['verification_enabled'] else '❌ Wyłączone'}\n"
            f"**Poziomy XP:** {'✅ Włączone' if row['leveling_enabled'] else '❌ Wyłączone'}\n"
            f"**Ekonomia:** {'✅ Włączone' if row['economy_enabled'] else '❌ Wyłączone'}\n"
            f"**AutoMod:** {'✅ Włączone' if row['automod_enabled'] else '❌ Wyłączone'}"
        )
        embed.add_field(name="🔌 Moduły Systemowe", value=toggles_info, inline=False)

        await interaction.response.send_message(embed=embed)

    @config_group.command(name="set_channel", description="Ustawia dedykowany kanał dla wybranego modułu.")
    @app_commands.describe(module="Wybierz moduł", channel="Kanał tekstowy")
    @app_commands.choices(module=[
        app_commands.Choice(name="Powitania (Welcome)", value="welcome_channel_id"),
        app_commands.Choice(name="Pożegnania (Goodbye)", value="goodbye_channel_id"),
        app_commands.Choice(name="Logi serwera", value="log_channel_id"),
        app_commands.Choice(name="Sugestie", value="suggestion_channel_id"),
        app_commands.Choice(name="Panel Ticketów", value="ticket_channel_id"),
        app_commands.Choice(name="Raport dzienny statystyk głosowych", value="stats_channel_id"),
        app_commands.Choice(name="Raport tygodniowy zarządu (środy 23:59)", value="weekly_stats_channel_id"),
        app_commands.Choice(name="Aplikacje - kanał zgłoszeń", value="applications_channel_id"),
        app_commands.Choice(name="Aplikacje - kanał przeglądu", value="applications_review_channel_id"),
    ])
    @is_administrator()
    async def set_channel(self, interaction: discord.Interaction, module: str, channel: discord.TextChannel) -> None:
        await self.bot.db.execute(
            f"""
            INSERT INTO guild_config (guild_id, {module}) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {module} = ?
            """,
            (interaction.guild_id, channel.id, channel.id)
        )
        await interaction.response.send_message(embed=success_embed("Zaktualizowano Ustawienia", f"Ustawiono kanał {channel.mention} dla modułu `{module}`."))

    @config_group.command(name="post_button", description="Wysyła na skonfigurowany kanał zgłoszeń przycisk otwierający formularz aplikacji.")
    @is_administrator()
    async def post_button(self, interaction: discord.Interaction) -> None:
        row = await self.bot.db.fetchone(
            "SELECT applications_channel_id FROM guild_config WHERE guild_id = ?",
            (interaction.guild_id,)
        )
        channel_id = row["applications_channel_id"] if row and "applications_channel_id" in row.keys() else None
        if not channel_id:
            await interaction.response.send_message(
                embed=error_embed("Kanał Nieustawiony", "Najpierw ustaw kanał zgłoszeń komendą `/config set_channel`."),
                ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message(
                embed=error_embed("Kanał Nie Istnieje", "Skonfigurowany kanał zgłoszeń już nie istnieje."),
                ephemeral=True
            )
            return

        await channel.send(
            embed=build_recruitment_post_embed(interaction.guild),
            view=ApplicationStartButton()
        )
        await interaction.response.send_message(
            embed=success_embed("Wysłano", f"Przycisk formularza został wysłany na {channel.mention}."),
            ephemeral=True
        )

    @config_group.command(name="set_role", description="Ustawia dedykowaną rolę dla wybranego modułu.")
    @app_commands.describe(role_type="Wybierz typ roli", role="Rola serwerowa")
    @app_commands.choices(role_type=[
        app_commands.Choice(name="Rola Zweryfikowana", value="verified_role_id"),
        app_commands.Choice(name="Rola Niezweryfikowana", value="unverified_role_id"),
        app_commands.Choice(name="Auto Rola (Nowi członkowie)", value="auto_role_id"),
        app_commands.Choice(name="Rola Moderatora", value="mod_role_id"),
        app_commands.Choice(name="Aplikacje - rola sztabu (ping)", value="applications_management_role_id"),
    ])
    @is_administrator()
    async def set_role(self, interaction: discord.Interaction, role_type: str, role: discord.Role) -> None:
        await self.bot.db.execute(
            f"""
            INSERT INTO guild_config (guild_id, {role_type}) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {role_type} = ?
            """,
            (interaction.guild_id, role.id, role.id)
        )
        await interaction.response.send_message(embed=success_embed("Zaktualizowano Ustawienia", f"Ustawiono rolę {role.mention} jako `{role_type}`."))

    @config_group.command(name="add_accept_role", description="Dodaje rolę nadawaną po akceptacji aplikacji (można dodać kilka).")
    @app_commands.describe(role="Rola do dodania")
    @is_administrator()
    async def add_accept_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO application_accept_roles (guild_id, role_id) VALUES (?, ?)",
            (interaction.guild_id, role.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Dodano Rolę", f"Rola {role.mention} będzie od teraz nadawana po akceptacji aplikacji.")
        )

    @config_group.command(name="remove_accept_role", description="Usuwa rolę z listy ról nadawanych po akceptacji aplikacji.")
    @app_commands.describe(role="Rola do usunięcia")
    @is_administrator()
    async def remove_accept_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        cursor = await self.bot.db.execute(
            "DELETE FROM application_accept_roles WHERE guild_id = ? AND role_id = ?",
            (interaction.guild_id, role.id)
        )
        # Usuń też starą, pojedynczą rolę, jeśli to właśnie ona była ustawiona (kompatybilność wsteczna).
        await self.bot.db.execute(
            """
            UPDATE guild_config SET applications_accept_role_id = NULL
            WHERE guild_id = ? AND applications_accept_role_id = ?
            """,
            (interaction.guild_id, role.id)
        )
        if cursor.rowcount:
            await interaction.response.send_message(
                embed=success_embed("Usunięto Rolę", f"Rola {role.mention} nie będzie już nadawana po akceptacji aplikacji.")
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("Rola Nie Znaleziona", f"Rola {role.mention} nie znajdowała się na liście ról po akceptacji.")
            )

    @config_group.command(name="list_accept_roles", description="Wyświetla listę ról nadawanych po akceptacji aplikacji.")
    @is_administrator()
    async def list_accept_roles(self, interaction: discord.Interaction) -> None:
        role_ids = await _get_accept_role_ids(self.bot.db, interaction.guild_id)

        if not role_ids:
            await interaction.response.send_message(
                embed=info_embed("Role Po Akceptacji", "Nie skonfigurowano jeszcze żadnej roli — użyj `/config add_accept_role`.")
            )
            return

        lines = "\n".join(f"• <@&{role_id}>" for role_id in role_ids)
        await interaction.response.send_message(embed=info_embed("Role Po Akceptacji", lines))

    @config_group.command(name="set_toggle", description="Włącza lub wyłącza wybrany moduł bota.")
    @app_commands.describe(module="Wybierz moduł", enabled="Włącz (True) / Wyłącz (False)")
    @app_commands.choices(module=[
        app_commands.Choice(name="Powitania", value="welcome_enabled"),
        app_commands.Choice(name="Pożegnania", value="goodbye_enabled"),
        app_commands.Choice(name="Logi Audytowe", value="logs_enabled"),
        app_commands.Choice(name="System Ticketów", value="tickets_enabled"),
        app_commands.Choice(name="Weryfikacja", value="verification_enabled"),
        app_commands.Choice(name="Poziomy XP", value="leveling_enabled"),
        app_commands.Choice(name="Ekonomia", value="economy_enabled"),
        app_commands.Choice(name="Sugestie", value="suggestions_enabled"),
        app_commands.Choice(name="AutoMod", value="automod_enabled"),
    ])
    @is_administrator()
    async def set_toggle(self, interaction: discord.Interaction, module: str, enabled: bool) -> None:
        val = 1 if enabled else 0
        await self.bot.db.execute(
            f"""
            INSERT INTO guild_config (guild_id, {module}) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {module} = ?
            """,
            (interaction.guild_id, val, val)
        )
        status_txt = "włączony ✅" if enabled else "wyłączony ❌"
        await interaction.response.send_message(embed=success_embed("Zaktualizowano Moduł", f"Moduł `{module}` jest od teraz **{status_txt}**."))

    @config_group.command(name="set_message", description="Ustawia szablon wiadomości powitalnej lub pożegnalnej.")
    @app_commands.describe(msg_type="Typ wiadomości", text="Szablon (Dostępne tagi: {user}, {username}, {server}, {count})")
    @app_commands.choices(msg_type=[
        app_commands.Choice(name="Wiadomość Powitalna", value="welcome_message"),
        app_commands.Choice(name="Wiadomość Pożegnalna", value="goodbye_message"),
    ])
    @is_administrator()
    async def set_message(self, interaction: discord.Interaction, msg_type: str, text: str) -> None:
        await self.bot.db.execute(
            f"""
            INSERT INTO guild_config (guild_id, {msg_type}) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {msg_type} = ?
            """,
            (interaction.guild_id, text, text)
        )
        await interaction.response.send_message(embed=success_embed("Ustawiono Szablon", f"Ustawiono szablon dla `{msg_type}`:\n*{text}*"))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdministrationCog(bot))