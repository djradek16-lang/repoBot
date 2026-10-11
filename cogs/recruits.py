"""
Moduł listy rekrutów.
Komendy /rekrut, /rekrut-edytuj, /rekrut-usun zarządzają zbiorczym embedem na danym kanale.
Każde użycie edytuje jedną, stałą wiadomość zamiast wysyłać nową. Potwierdzenia widoczne
są tylko dla osoby używającej komendy (ephemeral), więc kanał pozostaje czysty.
"""

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

from utils.embeds import error_embed, success_embed
from cogs.projectrpg import _find_forum_profile_url
import config

logger = logging.getLogger("AegisBot.Cogs.Recruits")

MAX_DESCRIPTION_LENGTH = 4000
DIVIDER = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


class RecruitsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS recruit_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                nick TEXT NOT NULL,
                login TEXT NOT NULL,
                uid TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_date TEXT NOT NULL,
                profile_url TEXT
            );
            """
        )
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS recruit_messages (
                channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL
            );
            """
        )

        # Migracja: starsze bazy mogły powstać przed dodaniem kolumny profile_url
        # (link do profilu na forum) — dokładamy ją, jeśli jeszcze jej nie ma.
        existing_columns = await self.bot.db.fetchall("PRAGMA table_info(recruit_entries)")
        column_names = {row["name"] for row in existing_columns}
        if "profile_url" not in column_names:
            await self.bot.db.execute("ALTER TABLE recruit_entries ADD COLUMN profile_url TEXT")

    # ------------------------------------------------------------------ #
    # Wyszukiwanie profilu na forum (nick -> login -> UID)
    # ------------------------------------------------------------------ #

    async def find_recruit_forum_link(self, nick: str, login: str, uid: str) -> str | None:
        """Próbuje znaleźć profil rekruta na forum, kolejno po: nicku, loginie, a na końcu UID.
        Zwraca URL profilu przy pierwszym trafieniu, albo None jeśli żadne zapytanie nic nie znajdzie."""
        for query in (nick, login, uid):
            query = (query or "").strip()
            if not query:
                continue
            try:
                result = await _find_forum_profile_url(query)
            except Exception as e:
                logger.warning(f"Błąd wyszukiwania profilu forum dla rekruta ('{query}'): {e}")
                continue
            if result and result.get("profile_url"):
                return result["profile_url"]
        return None

    # ------------------------------------------------------------------ #
    # Budowanie embedu
    # ------------------------------------------------------------------ #

    def build_embed(self, entries: list, total_count: int, guild: discord.Guild, last_actor: str) -> discord.Embed:
        blocks = []
        start_index = max(total_count - len(entries), 0)
        for offset, row in enumerate(entries):
            numer = start_index + offset + 1
            profile_url = row["profile_url"] if "profile_url" in row.keys() else None
            profile_line = (
                f"> 🔗 [Profil na forum]({profile_url})"
                if profile_url
                else "> 🔗 Profil na forum: *nie znaleziono*"
            )
            blocks.append(
                f"**`#{numer:03}`** 🪪 **{row['nick']}**\n"
                f"> 🔑 Login: `{row['login']}`\n"
                f"> 🆔 UID: `{row['uid']}`\n"
                f"> 📅 Data: {row['added_date']}\n"
                f"{profile_line}"
            )

        description = f"\n{DIVIDER}\n".join(blocks) if blocks else "*Brak wpisów na liście.*"

        omitted_note = None
        if len(description) > MAX_DESCRIPTION_LENGTH:
            trimmed_blocks = []
            running_length = 0
            for block in reversed(blocks):
                sep_len = len(DIVIDER) + 2
                if running_length + len(block) + sep_len > MAX_DESCRIPTION_LENGTH:
                    break
                trimmed_blocks.insert(0, block)
                running_length += len(block) + sep_len
            description = f"\n{DIVIDER}\n".join(trimmed_blocks)
            omitted_note = f"Pokazano {len(trimmed_blocks)} najnowszych z {total_count} wpisów łącznie"

        embed = discord.Embed(
            title="📋 Lista Rekrutów",
            description=description,
            color=config.DEFAULT_EMBED_COLOR,
            timestamp=datetime.now(timezone.utc)
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        footer_text = f"Łącznie wpisów: {total_count}"
        if last_actor:
            footer_text += f" • Ostatnia zmiana: {last_actor}"
        if omitted_note:
            footer_text += f"\n{omitted_note}"
        embed.set_footer(text=footer_text)

        return embed

    async def refresh_recruit_message(
        self,
        channel: discord.abc.Messageable,
        guild: discord.Guild,
        actor_name: str
    ) -> bool:
        """Pobiera aktualne wpisy z bazy i aktualizuje (lub tworzy) zbiorczą wiadomość embedu.
        Zwraca True jeśli operacja się powiodła."""
        entries = await self.bot.db.fetchall(
            "SELECT nick, login, uid, added_date, profile_url FROM recruit_entries WHERE channel_id = ? ORDER BY entry_id ASC",
            (channel.id,)
        )
        total_count = len(entries)
        embed = self.build_embed(entries, total_count, guild, actor_name)

        existing = await self.bot.db.fetchone(
            "SELECT message_id FROM recruit_messages WHERE channel_id = ?",
            (channel.id,)
        )

        message = None
        if existing:
            try:
                message = await channel.fetch_message(existing["message_id"])
            except (discord.NotFound, discord.HTTPException):
                message = None

        try:
            if message:
                await message.edit(embed=embed)
            else:
                new_message = await channel.send(embed=embed)
                await self.bot.db.execute(
                    "INSERT OR REPLACE INTO recruit_messages (channel_id, message_id) VALUES (?, ?)",
                    (channel.id, new_message.id)
                )
        except discord.HTTPException as e:
            logger.error(f"Błąd podczas wysyłania/edycji listy rekrutów: {e}")
            return False

        return True

    # ------------------------------------------------------------------ #
    # Autouzupełnianie wpisów (dla /rekrut-edytuj i /rekrut-usun)
    # ------------------------------------------------------------------ #

    async def wpis_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[int]]:
        channel_id = interaction.channel_id
        rows = await self.bot.db.fetchall(
            "SELECT entry_id, nick, login, uid FROM recruit_entries WHERE channel_id = ? ORDER BY entry_id ASC",
            (channel_id,)
        )

        current_lower = current.lower().strip()
        choices: list[app_commands.Choice[int]] = []
        for row in rows:
            haystack = f"{row['nick']} {row['login']} {row['uid']}".lower()
            if current_lower and current_lower not in haystack:
                continue
            label = f"{row['nick']} • {row['login']} • UID {row['uid']}"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=row["entry_id"]))
            if len(choices) >= 25:
                break

        return choices

    # ------------------------------------------------------------------ #
    # /rekrut
    # ------------------------------------------------------------------ #

    @app_commands.command(name="rekrut", description="Dodaje nowy wpis na listę rekrutów (Nick, Login, UID, Data).")
    @app_commands.describe(
        nick="Nick rekruta",
        login="Login rekruta",
        uid="UID rekruta"
    )
    async def rekrut(self, interaction: discord.Interaction, nick: str, login: str, uid: str) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id
        channel = interaction.channel
        data_dzis = datetime.now().strftime("%d.%m.%Y")

        profile_url = await self.find_recruit_forum_link(nick, login, uid)

        await self.bot.db.execute(
            """
            INSERT INTO recruit_entries (guild_id, channel_id, nick, login, uid, added_by, added_date, profile_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel.id, nick, login, uid, interaction.user.id, data_dzis, profile_url)
        )

        ok = await self.refresh_recruit_message(channel, interaction.guild, f"{interaction.user.display_name} (dodano)")
        if not ok:
            await interaction.followup.send(
                embed=error_embed("Błąd", "Wpis zapisano, ale nie udało się zaktualizować listy na kanale."),
                ephemeral=True
            )
            return

        if profile_url:
            opis = f"**{nick}** został dodany do listy rekrutów.\n🔗 [Profil na forum]({profile_url})"
        else:
            opis = (
                f"**{nick}** został dodany do listy rekrutów.\n"
                "⚠️ Nie znaleziono profilu na forum (sprawdzono nick, login i UID)."
            )

        await interaction.followup.send(
            embed=success_embed("Dodano wpis", opis),
            ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /rekrut-edytuj
    # ------------------------------------------------------------------ #

    @app_commands.command(name="rekrut-edytuj", description="Edytuje istniejący wpis na liście rekrutów.")
    @app_commands.describe(
        wpis="Wybierz wpis do edycji (zacznij pisać nick lub UID)",
        nowy_nick="Nowy nick (pozostaw puste, aby nie zmieniać)",
        nowy_login="Nowy login (pozostaw puste, aby nie zmieniać)",
        nowy_uid="Nowy UID (pozostaw puste, aby nie zmieniać)"
    )
    @app_commands.autocomplete(wpis=wpis_autocomplete)
    async def rekrut_edytuj(
        self,
        interaction: discord.Interaction,
        wpis: int,
        nowy_nick: str | None = None,
        nowy_login: str | None = None,
        nowy_uid: str | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel

        row = await self.bot.db.fetchone(
            "SELECT entry_id, nick, login, uid FROM recruit_entries WHERE entry_id = ? AND channel_id = ?",
            (wpis, channel.id)
        )
        if not row:
            await interaction.followup.send(
                embed=error_embed("Nie znaleziono", "Ten wpis nie istnieje już na tym kanale."),
                ephemeral=True
            )
            return

        if not any([nowy_nick, nowy_login, nowy_uid]):
            await interaction.followup.send(
                embed=error_embed("Brak zmian", "Podaj przynajmniej jedno pole do zmiany (nick, login lub UID)."),
                ephemeral=True
            )
            return

        nick = nowy_nick or row["nick"]
        login = nowy_login or row["login"]
        uid = nowy_uid or row["uid"]

        # Po każdej edycji odświeżamy link do profilu na forum (nick -> login -> UID),
        # żeby np. zmiana nicku od razu zaktualizowała wskazywany profil.
        profile_url = await self.find_recruit_forum_link(nick, login, uid)

        await self.bot.db.execute(
            "UPDATE recruit_entries SET nick = ?, login = ?, uid = ?, profile_url = ? WHERE entry_id = ?",
            (nick, login, uid, profile_url, wpis)
        )

        ok = await self.refresh_recruit_message(channel, interaction.guild, f"{interaction.user.display_name} (edycja)")
        if not ok:
            await interaction.followup.send(
                embed=error_embed("Błąd", "Wpis zapisano, ale nie udało się zaktualizować listy na kanale."),
                ephemeral=True
            )
            return

        if profile_url:
            opis = f"Wpis **{row['nick']}** został zaktualizowany na **{nick}**.\n🔗 [Profil na forum]({profile_url})"
        else:
            opis = (
                f"Wpis **{row['nick']}** został zaktualizowany na **{nick}**.\n"
                "⚠️ Nie znaleziono profilu na forum (sprawdzono nick, login i UID)."
            )

        await interaction.followup.send(
            embed=success_embed("Zaktualizowano wpis", opis),
            ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /rekrut-usun
    # ------------------------------------------------------------------ #

    @app_commands.command(name="rekrut-usun", description="Usuwa wpis z listy rekrutów.")
    @app_commands.describe(wpis="Wybierz wpis do usunięcia (zacznij pisać nick lub UID)")
    @app_commands.autocomplete(wpis=wpis_autocomplete)
    async def rekrut_usun(self, interaction: discord.Interaction, wpis: int) -> None:
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel

        row = await self.bot.db.fetchone(
            "SELECT entry_id, nick FROM recruit_entries WHERE entry_id = ? AND channel_id = ?",
            (wpis, channel.id)
        )
        if not row:
            await interaction.followup.send(
                embed=error_embed("Nie znaleziono", "Ten wpis nie istnieje już na tym kanale."),
                ephemeral=True
            )
            return

        await self.bot.db.execute("DELETE FROM recruit_entries WHERE entry_id = ?", (wpis,))

        ok = await self.refresh_recruit_message(channel, interaction.guild, f"{interaction.user.display_name} (usunięto)")
        if not ok:
            await interaction.followup.send(
                embed=error_embed("Błąd", "Wpis usunięto z bazy, ale nie udało się zaktualizować listy na kanale."),
                ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=success_embed("Usunięto wpis", f"**{row['nick']}** został usunięty z listy rekrutów."),
            ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecruitsCog(bot))