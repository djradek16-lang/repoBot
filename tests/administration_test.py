"""
TESTOWA wersja komend konfiguracyjnych (/config-test) — dotyczy WYŁĄCZNIE
testowego modułu aplikacji rekrutacyjnych (cogs/applications_test.py +
views/application_views_test.py).

Osobna grupa komend (config-test zamiast config), żeby nie kolidować z
istniejącą grupą /config z administration.py — Discord wymaga unikalnych
nazw top-level komend/grup na tym samym poziomie.

Operuje wyłącznie na testowych kolumnach guild_config (prefiks "test_")
oraz na testowej tabeli application_accept_roles_test — konfiguracja
testowa nigdy nie dotyka configu produkcyjnego.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import success_embed, error_embed, info_embed
from views.application_views_test import (
    _get_accept_role_ids_test,
    ApplicationStartButtonTest,
    build_recruitment_post_embed_test,
)

logger = logging.getLogger("AegisBot.Cogs.AdministrationTest")


class AdministrationTestCog(commands.Cog):
    """Zarządzanie TESTOWĄ konfiguracją modułu aplikacji rekrutacyjnych."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    config_test_group = app_commands.Group(
        name="config-test",
        description="Zarządzanie testową konfiguracją rekrutacji",
        default_permissions=discord.Permissions(administrator=True)
    )

    @config_test_group.command(name="show", description="Wyświetla bieżącą TESTOWĄ konfigurację rekrutacji dla tego serwera.")
    @is_administrator()
    async def config_test_show(self, interaction: discord.Interaction) -> None:
        row = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        if not row:
            await interaction.response.send_message(embed=info_embed("Konfiguracja Testowa", "Konfiguracja serwera nie została jeszcze utworzona. Użyj komend `/config-test set_channel` itp."))
            return

        apch = f"<#{row['test_applications_channel_id']}>" if "test_applications_channel_id" in row.keys() and row["test_applications_channel_id"] else "Nieustawiony"
        aprch = f"<#{row['test_applications_review_channel_id']}>" if "test_applications_review_channel_id" in row.keys() and row["test_applications_review_channel_id"] else "Nieustawiony"
        amrole = f"<@&{row['test_applications_management_role_id']}>" if "test_applications_management_role_id" in row.keys() and row["test_applications_management_role_id"] else "Nieustawiona"

        accept_role_ids = await _get_accept_role_ids_test(self.bot.db, interaction.guild_id)
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
        embed = info_embed(f"⚙️ [TEST] Konfiguracja Rekrutacji — {interaction.guild.name}", "")
        embed.add_field(name="📥 Rekrutacja (TEST)", value=applications_info, inline=False)
        await interaction.response.send_message(embed=embed)

    @config_test_group.command(name="set_channel", description="Ustawia dedykowany TESTOWY kanał dla modułu rekrutacji.")
    @app_commands.describe(module="Wybierz moduł", channel="Kanał tekstowy")
    @app_commands.choices(module=[
        app_commands.Choice(name="Aplikacje - kanał zgłoszeń", value="test_applications_channel_id"),
        app_commands.Choice(name="Aplikacje - kanał przeglądu", value="test_applications_review_channel_id"),
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
        await interaction.response.send_message(embed=success_embed("Zaktualizowano Ustawienia Testowe", f"Ustawiono kanał {channel.mention} dla modułu `{module}`."))

    @config_test_group.command(name="set_role", description="Ustawia dedykowaną TESTOWĄ rolę dla modułu rekrutacji.")
    @app_commands.describe(role_type="Wybierz typ roli", role="Rola serwerowa")
    @app_commands.choices(role_type=[
        app_commands.Choice(name="Aplikacje - rola sztabu (ping)", value="test_applications_management_role_id"),
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
        await interaction.response.send_message(embed=success_embed("Zaktualizowano Ustawienia Testowe", f"Ustawiono rolę {role.mention} jako `{role_type}`."))

    @config_test_group.command(name="post_button", description="Wysyła na skonfigurowany testowy kanał zgłoszeń przycisk otwierający formularz aplikacji.")
    @is_administrator()
    async def post_button(self, interaction: discord.Interaction) -> None:
        row = await self.bot.db.fetchone(
            "SELECT test_applications_channel_id FROM guild_config WHERE guild_id = ?",
            (interaction.guild_id,)
        )
        channel_id = row["test_applications_channel_id"] if row and "test_applications_channel_id" in row.keys() else None
        if not channel_id:
            await interaction.response.send_message(
                embed=error_embed("Kanał Nieustawiony", "Najpierw ustaw testowy kanał zgłoszeń komendą `/config-test set_channel`."),
                ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message(
                embed=error_embed("Kanał Nie Istnieje", "Skonfigurowany testowy kanał zgłoszeń już nie istnieje."),
                ephemeral=True
            )
            return

        await channel.send(
            embed=build_recruitment_post_embed_test(interaction.guild),
            view=ApplicationStartButtonTest()
        )
        await interaction.response.send_message(
            embed=success_embed("Wysłano", f"Przycisk formularza został wysłany na {channel.mention}."),
            ephemeral=True
        )

    @config_test_group.command(name="add_accept_role", description="Dodaje TESTOWĄ rolę nadawaną po akceptacji aplikacji (można dodać kilka).")
    @app_commands.describe(role="Rola do dodania")
    @is_administrator()
    async def add_accept_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO application_accept_roles_test (guild_id, role_id) VALUES (?, ?)",
            (interaction.guild_id, role.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Dodano Rolę Testową", f"Rola {role.mention} będzie od teraz nadawana po akceptacji aplikacji testowej.")
        )

    @config_test_group.command(name="remove_accept_role", description="Usuwa rolę z listy TESTOWYCH ról nadawanych po akceptacji aplikacji.")
    @app_commands.describe(role="Rola do usunięcia")
    @is_administrator()
    async def remove_accept_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        cursor = await self.bot.db.execute(
            "DELETE FROM application_accept_roles_test WHERE guild_id = ? AND role_id = ?",
            (interaction.guild_id, role.id)
        )
        # Usuń też starą, pojedynczą rolę testową, jeśli to właśnie ona była ustawiona (kompatybilność wsteczna).
        await self.bot.db.execute(
            """
            UPDATE guild_config SET test_applications_accept_role_id = NULL
            WHERE guild_id = ? AND test_applications_accept_role_id = ?
            """,
            (interaction.guild_id, role.id)
        )
        if cursor.rowcount:
            await interaction.response.send_message(
                embed=success_embed("Usunięto Rolę Testową", f"Rola {role.mention} nie będzie już nadawana po akceptacji aplikacji testowej.")
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("Rola Nie Znaleziona", f"Rola {role.mention} nie znajdowała się na testowej liście ról po akceptacji.")
            )

    @config_test_group.command(name="list_accept_roles", description="Wyświetla listę TESTOWYCH ról nadawanych po akceptacji aplikacji.")
    @is_administrator()
    async def list_accept_roles(self, interaction: discord.Interaction) -> None:
        role_ids = await _get_accept_role_ids_test(self.bot.db, interaction.guild_id)

        if not role_ids:
            await interaction.response.send_message(
                embed=info_embed("Role Testowe Po Akceptacji", "Nie skonfigurowano jeszcze żadnej roli — użyj `/config-test add_accept_role`.")
            )
            return

        lines = "\n".join(f"• <@&{role_id}>" for role_id in role_ids)
        await interaction.response.send_message(embed=info_embed("Role Testowe Po Akceptacji", lines))


async def setup(bot: commands.Bot) -> None:
    # Migracja: tabela na wiele testowych ról po akceptacji (analogiczna do
    # application_accept_roles produkcyjnej, ale w pełni osobna).
    await bot.db.execute(
        """
        CREATE TABLE IF NOT EXISTS application_accept_roles_test (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, role_id)
        )
        """
    )
    await bot.add_cog(AdministrationTestCog(bot))
