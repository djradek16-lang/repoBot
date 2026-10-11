"""
Moduł "Dołącz, aby utworzyć" (Join to Create).

Administrator oznacza jeden, wybrany kanał głosowy jako kanał-wyzwalacz.
Gdy dowolny użytkownik do niego dołączy, bot automatycznie tworzy dla niego
nowy, tymczasowy kanał głosowy, przenosi go tam i nadaje mu pełne uprawnienia
zarządzania tym kanałem (w tym jego wbudowanym czatem tekstowym: wysyłanie
i zarządzanie wiadomościami, zarządzanie kanałem, zarządzanie uprawnieniami,
przenoszenie/wyciszanie innych itd.). Kanał jest usuwany automatycznie, gdy
zrobi się na nim pusto.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import success_embed, error_embed, info_embed
from views.voice_panel_views import VoicePanelView, OWNER_PERMISSION_OVERWRITE

logger = logging.getLogger("AegisBot.Cogs.JoinToCreate")


class JoinToCreateCog(commands.Cog):
    """Automatyczne, tymczasowe kanały głosowe tworzone po dołączeniu do kanału-wyzwalacza."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Rejestracja trwałego widoku panelu zarządzania - dzięki temu przyciski
        # na już istniejących kanałach działają również po restarcie bota.
        self.bot.add_view(VoicePanelView())

        # Po (re)starcie bota sprzątamy ewentualne osierocone kanały, które
        # zdążyły opustoszeć, zanim bot ponownie się połączył.
        try:
            await self._cleanup_orphaned_channels()
        except Exception:
            logger.exception("Błąd podczas wstępnego sprzątania kanałów Join to Create.")

    async def _cleanup_orphaned_channels(self) -> None:
        rows = await self.bot.db.fetchall("SELECT channel_id, guild_id FROM jtc_channels")
        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])
            channel = guild.get_channel(row["channel_id"]) if guild else None

            if channel is None:
                # Kanał już nie istnieje (np. usunięty ręcznie) - czyścimy wpis w bazie.
                await self.bot.db.execute("DELETE FROM jtc_channels WHERE channel_id = ?", (row["channel_id"],))
                continue

            if isinstance(channel, discord.VoiceChannel) and len(channel.members) == 0:
                try:
                    await channel.delete(reason="Join to Create: sprzątanie po restarcie bota (kanał pusty).")
                except discord.HTTPException:
                    pass
                await self.bot.db.execute("DELETE FROM jtc_channels WHERE channel_id = ?", (row["channel_id"],))

    # --- Grupa komend administracyjnych ---

    jtc_group = app_commands.Group(
        name="kanalglosowy",
        description="Konfiguracja systemu automatycznych kanałów głosowych (Join to Create)",
        default_permissions=discord.Permissions(administrator=True)
    )

    @jtc_group.command(name="ustaw", description="Oznacza kanał głosowy jako wyzwalacz tworzący prywatne kanały.")
    @app_commands.describe(
        kanal="Kanał głosowy, do którego dołączenie tworzy nowy, prywatny kanał.",
        kategoria="Kategoria, w której mają powstawać nowe kanały (domyślnie: ta sama co kanał-wyzwalacz).",
        szablon_nazwy="Szablon nazwy nowego kanału. Użyj {user} jako placeholdera na nazwę użytkownika.",
        limit_uzytkownikow="Domyślny limit użytkowników na nowo tworzonych kanałach (0 = brak limitu)."
    )
    @is_administrator()
    async def ustaw(
        self,
        interaction: discord.Interaction,
        kanal: discord.VoiceChannel,
        kategoria: discord.CategoryChannel = None,
        szablon_nazwy: str = "Kanał {user}",
        limit_uzytkownikow: app_commands.Range[int, 0, 99] = 0
    ) -> None:
        if "{user}" not in szablon_nazwy:
            szablon_nazwy += " {user}"

        category_id = kategoria.id if kategoria else (kanal.category.id if kanal.category else None)

        await self.bot.db.execute(
            """
            INSERT INTO jtc_config (guild_id, trigger_channel_id, category_id, name_template, user_limit)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                trigger_channel_id = excluded.trigger_channel_id,
                category_id = excluded.category_id,
                name_template = excluded.name_template,
                user_limit = excluded.user_limit
            """,
            (interaction.guild.id, kanal.id, category_id, szablon_nazwy, limit_uzytkownikow)
        )

        await interaction.response.send_message(
            embed=success_embed(
                "Skonfigurowano Join to Create",
                f"Dołączenie do kanału {kanal.mention} będzie teraz tworzyć nowy, prywatny kanał głosowy.\n\n"
                f"🗂️ **Kategoria:** {kategoria.mention if kategoria else 'taka sama jak kanał-wyzwalacz'}\n"
                f"📝 **Szablon nazwy:** `{szablon_nazwy}`\n"
                f"👥 **Domyślny limit:** {limit_uzytkownikow if limit_uzytkownikow else 'brak'}\n\n"
                "Osoba, która dołączy do kanału-wyzwalacza, zostanie właścicielem nowego kanału i otrzyma "
                "na nim pełne uprawnienia zarządzania (kanał głosowy + jego czat tekstowy)."
            ),
            ephemeral=True
        )

    @jtc_group.command(name="wylacz", description="Wyłącza system automatycznych kanałów głosowych na tym serwerze.")
    @is_administrator()
    async def wylacz(self, interaction: discord.Interaction) -> None:
        cursor = await self.bot.db.execute("DELETE FROM jtc_config WHERE guild_id = ?", (interaction.guild.id,))
        if cursor.rowcount == 0:
            await interaction.response.send_message(
                embed=error_embed("Brak Konfiguracji", "Ten system nie był wcześniej skonfigurowany na tym serwerze."),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=success_embed("Wyłączono", "System Join to Create został wyłączony. Istniejące kanały pozostaną i zostaną usunięte, gdy się opróżnią."),
            ephemeral=True
        )

    @jtc_group.command(name="info", description="Pokazuje aktualną konfigurację systemu Join to Create.")
    @is_administrator()
    async def info(self, interaction: discord.Interaction) -> None:
        row = await self.bot.db.fetchone("SELECT * FROM jtc_config WHERE guild_id = ?", (interaction.guild.id,))
        if row is None:
            await interaction.response.send_message(
                embed=info_embed("Brak Konfiguracji", "System Join to Create nie jest skonfigurowany. Użyj `/kanalglosowy ustaw`."),
                ephemeral=True
            )
            return

        trigger = interaction.guild.get_channel(row["trigger_channel_id"])
        category = interaction.guild.get_channel(row["category_id"]) if row["category_id"] else None
        active_count_row = await self.bot.db.fetchone(
            "SELECT COUNT(*) as cnt FROM jtc_channels WHERE guild_id = ?", (interaction.guild.id,)
        )

        await interaction.response.send_message(
            embed=info_embed(
                "Konfiguracja Join to Create",
                f"🎙️ **Kanał-wyzwalacz:** {trigger.mention if trigger else '⚠️ usunięty'}\n"
                f"🗂️ **Kategoria docelowa:** {category.mention if category else 'taka sama jak kanał-wyzwalacz'}\n"
                f"📝 **Szablon nazwy:** `{row['name_template']}`\n"
                f"👥 **Domyślny limit:** {row['user_limit'] if row['user_limit'] else 'brak'}\n"
                f"📊 **Aktywne kanały tymczasowe:** {active_count_row['cnt']}"
            ),
            ephemeral=True
        )

    # --- Logika tworzenia / usuwania kanałów ---

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ) -> None:
        # Dołączenie do kanału-wyzwalacza -> tworzymy nowy kanał.
        if after.channel is not None and before.channel != after.channel:
            await self._maybe_create_channel(member, after.channel)

        # Opuszczenie kanału tymczasowego -> usuwamy go, jeśli jest pusty.
        if before.channel is not None and before.channel != after.channel:
            await self._maybe_delete_channel(before.channel)

    async def _maybe_create_channel(self, member: discord.Member, trigger_channel: discord.VoiceChannel) -> None:
        guild = member.guild
        config_row = await self.bot.db.fetchone(
            "SELECT * FROM jtc_config WHERE guild_id = ? AND trigger_channel_id = ?",
            (guild.id, trigger_channel.id)
        )
        if config_row is None:
            return

        category = guild.get_channel(config_row["category_id"]) if config_row["category_id"] else trigger_channel.category

        channel_name = config_row["name_template"].format(user=member.display_name)[:100]

        overwrites = dict(category.overwrites) if category else {}
        overwrites[member] = OWNER_PERMISSION_OVERWRITE

        try:
            new_channel = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                user_limit=config_row["user_limit"] or 0,
                overwrites=overwrites,
                reason=f"Join to Create: nowy kanał dla {member} ({member.id})."
            )
            await member.move_to(new_channel, reason="Join to Create: przeniesienie na nowo utworzony kanał.")
        except discord.Forbidden:
            logger.warning(f"Brak uprawnień do utworzenia kanału Join to Create na serwerze {guild.id}.")
            return
        except discord.HTTPException as e:
            logger.error(f"Nie udało się utworzyć kanału Join to Create na serwerze {guild.id}: {e}")
            return

        await self.bot.db.execute(
            "INSERT INTO jtc_channels (channel_id, guild_id, owner_id) VALUES (?, ?, ?)",
            (new_channel.id, guild.id, member.id)
        )
        logger.info(f"Utworzono kanał Join to Create '{new_channel.name}' ({new_channel.id}) dla {member} na serwerze {guild.id}.")

        # Panel zarządzania kanałem - wysyłany na wbudowany czat tekstowy tego kanału głosowego.
        try:
            await new_channel.send(
                embed=info_embed(
                    f"Kanał głosowy: {new_channel.name}",
                    f"{member.mention}, jesteś właścicielem tego kanału i masz nad nim pełną kontrolę.\n\n"
                    "Skorzystaj z przycisków poniżej, aby nim zarządzać:\n"
                    "🔒 **Zablokuj / Odblokuj** — decyduj, kto może dołączyć\n"
                    "✏️ **Zmień nazwę** — nadaj kanałowi własną nazwę\n"
                    "👥 **Limit osób** — ogranicz liczbę uczestników\n"
                    "👑 **Przekaż własność** — oddaj zarządzanie innej osobie na kanale\n"
                    "🚪 **Wyrzuć użytkownika** — rozłącz kogoś z kanału\n"
                    "🗑️ **Usuń kanał** — natychmiast usuń ten kanał"
                ),
                view=VoicePanelView()
            )
        except discord.HTTPException as e:
            logger.warning(f"Nie udało się wysłać panelu zarządzania na kanale {new_channel.id}: {e}")

    async def _maybe_delete_channel(self, channel: discord.VoiceChannel) -> None:
        row = await self.bot.db.fetchone("SELECT channel_id FROM jtc_channels WHERE channel_id = ?", (channel.id,))
        if row is None:
            return

        if len(channel.members) > 0:
            return

        try:
            await channel.delete(reason="Join to Create: kanał opustoszał.")
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            logger.warning(f"Nie udało się usunąć pustego kanału Join to Create {channel.id}: {e}")
            return

        await self.bot.db.execute("DELETE FROM jtc_channels WHERE channel_id = ?", (channel.id,))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JoinToCreateCog(bot))