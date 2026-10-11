"""
Moduł aplikacji rekrutacyjnych.

Zgłoszenie składa się przez formularz GUI (Discord Modal), a nie przez
wpisanie wzoru na czacie — patrz views/application_views.py
(ApplicationStartButton, ApplicationModalPart1/Part2). Ten plik odpowiada
już tylko za:
1) migrację kolumn konfiguracyjnych w guild_config,
2) rejestrację trwałych widoków po restarcie bota (przycisk startowy
   formularza + przyciski Akceptuj/Odrzuć na kanale przeglądu).

Cała logika przetwarzania zgłoszenia (weryfikacja forum, budowanie embedów,
wysyłka na kanał przeglądu, DM z potwierdzeniem) mieszka teraz w
views/application_views.py, bo to tam trafia interakcja z modala — nie ma
już surowej wiadomości na kanale ani listenera on_message do przetworzenia.

Przycisk startowy wysyła się na kanał zgłoszeń komendą /config post_button
(patrz cogs/administration.py).
"""

import logging
import sqlite3

import discord
from discord.ext import commands

from views.application_views import (
    ApplicationDecisionButton,
    ApplicationStartButton,
    _decided_embed,
)
from utils.embeds import error_embed

logger = logging.getLogger("AegisBot.Cogs.Applications")


class ApplicationsCog(commands.Cog):
    """Rejestracja trwałych widoków modułu aplikacji rekrutacyjnych (formularz GUI)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Migracja: dodaj kolumny konfiguracyjne do istniejącej tabeli guild_config, jeśli ich brak.
        for column in (
            "applications_channel_id",
            "applications_review_channel_id",
            "applications_management_role_id",
            "applications_accept_role_id",
        ):
            try:
                await self.bot.db.execute(f"ALTER TABLE guild_config ADD COLUMN {column} INTEGER")
            except Exception:
                pass  # Kolumna już istnieje.

        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                nick TEXT,
                login TEXT,
                uid TEXT,
                age TEXT,
                rp_points TEXT,
                recommended_by TEXT,
                rules_agreed TEXT,
                fee_agreed TEXT,
                forum_url TEXT,
                status TEXT DEFAULT 'pending',
                decision_by INTEGER,
                decision_by_name TEXT,
                decision_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            );
            """
        )

        # Rejestracja trwałych widoków (przetrwają restart bota):
        # - przycisk startowy formularza (statyczny custom_id, zwykły persistent View),
        # - przyciski Akceptuj/Odrzuć na kanale przeglądu (dynamiczne, kodują ID zgłaszającego).
        self.bot.add_view(ApplicationStartButton())
        self.bot.add_dynamic_items(ApplicationDecisionButton)

    @commands.Cog.listener("on_member_remove")
    async def on_member_remove(self, member: discord.Member) -> None:
        """Automatycznie odrzuca oczekujące podanie kandydata, jeśli opuścił serwer Discord
        zanim zarząd podjął decyzję, z powodem: Opuszczenie discorda."""
        guild = member.guild
        if guild is None:
            return

        try:
            # 1. Znajdź wszystkie oczekujące aplikacje tego użytkownika na tym serwerze
            pending_rows = await self.bot.db.fetchall(
                """
                SELECT id, user_id, nick, login, uid, age
                FROM applications
                WHERE guild_id = ? AND user_id = ? AND LOWER(status) = 'pending'
                ORDER BY id DESC
                """,
                (guild.id, member.id)
            )

            if not pending_rows:
                return

            reason_text = "Opuszczenie discorda"

            # 2. Zaktualizuj status podań w bazie danych na 'reject' z powodem
            for r in pending_rows:
                app_id = r["id"]
                await self.bot.db.execute(
                    """
                    UPDATE applications
                    SET status = 'reject',
                        decision_by = 0,
                        decision_by_name = 'System (Auto)',
                        decision_reason = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (reason_text, app_id)
                )
                logger.info(
                    f"Aplikacja #{app_id} użytkownika {member} (ID: {member.id}) została automatycznie "
                    f"odrzucona po opuszczeniu serwera '{guild.name}' ({guild.id}) z powodem: '{reason_text}'."
                )

            # 3. Pobierz skonfigurowany kanał przeglądu aplikacji
            cfg_row = await self.bot.db.fetchone(
                "SELECT applications_review_channel_id FROM guild_config WHERE guild_id = ?",
                (guild.id,)
            )
            review_channel_id = cfg_row["applications_review_channel_id"] if cfg_row else None
            if not review_channel_id:
                return

            review_channel = guild.get_channel(review_channel_id)
            if not review_channel or not isinstance(review_channel, discord.TextChannel):
                return

            # 4. Zaktualizuj istniejącą wiadomość zgłoszenia na kanale przeglądu (jeśli znajdziemy w historii)
            found_and_edited = False
            target_suffix = f":{member.id}"
            try:
                async for msg in review_channel.history(limit=50):
                    if msg.author.id == self.bot.user.id and msg.components:
                        has_button = False
                        for action_row in msg.components:
                            for comp in getattr(action_row, "children", []):
                                cid = getattr(comp, "custom_id", None) or ""
                                if cid.startswith(("app_decision:", "app_dec:")) and cid.endswith(target_suffix):
                                    has_button = True
                                    break
                            if has_button:
                                break

                        if has_button and msg.embeds:
                            original_embed = msg.embeds[0]
                            decided_embed = _decided_embed(
                                original_embed,
                                action="reject",
                                decided_by="System (Auto)",
                                reason=reason_text
                            )
                            await msg.edit(embed=decided_embed, view=None)
                            found_and_edited = True
                            logger.info(
                                f"Zaktualizowano embed aplikacji na kanale przeglądu dla {member} (wiadomość {msg.id})."
                            )
                            break
            except Exception as e:
                logger.warning(
                    f"Błąd podczas wyszukiwania/edycji wiadomości aplikacji na kanale przeglądu: {e}"
                )

            # 5. Jeśli wiadomość była starsza lub nie udało się jej edytować w historii, wyślij notatkę na kanale przeglądu
            if not found_and_edited:
                candidate_nick = pending_rows[0]["nick"] or pending_rows[0]["login"] or member.display_name
                uid_info = f" (UID: `{pending_rows[0]['uid']}`)" if pending_rows[0]["uid"] else ""
                notification_embed = error_embed(
                    "Aplikacja Odrzucona Automatycznie",
                    f"Kandydat **{member}** / `{candidate_nick}`{uid_info} (ID: `{member.id}`) **opuścił serwer Discord** z oczekującym podaniem.\n\n"
                    f"❌ **Status podania:** Odrzucone automatycznie\n"
                    f"📝 **Powód:** {reason_text}"
                )
                try:
                    await review_channel.send(embed=notification_embed)
                except Exception as e:
                    logger.error(
                        f"Nie udało się wysłać powiadomienia o opuszczeniu serwera na kanał przeglądu: {e}"
                    )

        except Exception as e:
            logger.error(f"Błąd w obsłudze zdarzenia on_member_remove dla aplikacji: {e}", exc_info=True)

    # ─── KOMENDY SLASH DLA ZARZĄDU (/rekrutacja) ───

    rekrutacja_group = discord.app_commands.Group(
        name="rekrutacja",
        description="Zarządzanie systemem rekrutacji i podaniami do organizacji."
    )

    @rekrutacja_group.command(name="usun", description="Trwale usuwa podanie rekrutacyjne z bazy danych po ID.")
    @discord.app_commands.describe(id="Numer ID podania (np. 24, 32)")
    async def cmd_rekrutacja_usun(self, interaction: discord.Interaction, id: int) -> None:
        from views.application_views import _has_review_permission
        if not await _has_review_permission(interaction):
            await interaction.response.send_message(
                embed=error_embed("Brak Uprawnień", "Nie masz uprawnień do zarządzania rekrutacją."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        row = await self.bot.db.fetchone("SELECT * FROM applications WHERE id = ?", (id,))
        if not row:
            await interaction.followup.send(
                embed=error_embed("Nie Znaleziono", f"Nie odnaleziono w bazie podania o ID **#{id}**."),
                ephemeral=True
            )
            return

        await self.bot.db.execute("DELETE FROM applications WHERE id = ?", (id,))
        logger.info(f"Podanie #{id} zostało usunięte z bazy przez {interaction.user} ({interaction.user.id}).")

        # Spróbuj zaktualizować/wyczyścić wiadomość na kanale przeglądu
        review_edited = False
        guild = interaction.guild
        if guild:
            cfg_row = await self.bot.db.fetchone(
                "SELECT applications_review_channel_id FROM guild_config WHERE guild_id = ?",
                (guild.id,)
            )
            if cfg_row and cfg_row["applications_review_channel_id"]:
                r_channel = guild.get_channel(cfg_row["applications_review_channel_id"])
                if r_channel and isinstance(r_channel, discord.TextChannel):
                    target_suffix = f":{row['user_id']}"
                    try:
                        async for msg in r_channel.history(limit=50):
                            if msg.author.id == self.bot.user.id and msg.components:
                                has_btn = False
                                for action_row in msg.components:
                                    for comp in getattr(action_row, "children", []):
                                        cid = getattr(comp, "custom_id", None) or ""
                                        if cid.startswith(("app_decision:", "app_dec:")) and cid.endswith(target_suffix):
                                            has_btn = True
                                            break
                                    if has_btn:
                                        break
                                if has_btn:
                                    del_embed = msg.embeds[0].copy() if msg.embeds else discord.Embed()
                                    del_embed.color = 0x4f545c
                                    del_embed.title = f"🗑️ Podanie #{id} Usunięte z Bazy"
                                    del_embed.description = f"To zgłoszenie zostało trwale usunięte z bazy danych przez {interaction.user.mention}."
                                    await msg.edit(embed=del_embed, view=None)
                                    review_edited = True
                                    break
                    except Exception as e:
                        logger.warning(f"Nie udało się zaktualizować wiadomości po /rekrutacja usun: {e}")

        c_name = row["nick"] or row["login"] or f"UID: {row['uid']}" or f"User: {row['user_id']}"
        desc = f"Podanie **#{id}** (Kandydat: **{c_name}**) zostało trwale usunięte z bazy danych."
        if review_edited:
            desc += "\n✅ Przyciski na kanale przeglądu zostały zablokowane i usunięte."

        await interaction.followup.send(
            embed=success_embed("Podanie Usunięte", desc),
            ephemeral=True
        )

    @rekrutacja_group.command(name="lista", description="Wyświetla listę podań rekrutacyjnych.")
    @discord.app_commands.describe(status="Filtr statusu podań")
    @discord.app_commands.choices(status=[
        discord.app_commands.Choice(name="Wszystkie", value="all"),
        discord.app_commands.Choice(name="Tylko oczekujące", value="pending"),
        discord.app_commands.Choice(name="Tylko zaakceptowane", value="approved"),
        discord.app_commands.Choice(name="Tylko odrzucone", value="rejected"),
    ])
    async def cmd_rekrutacja_lista(
        self, interaction: discord.Interaction, status: discord.app_commands.Choice[str] = None
    ) -> None:
        from views.application_views import _has_review_permission
        if not await _has_review_permission(interaction):
            await interaction.response.send_message(
                embed=error_embed("Brak Uprawnień", "Nie masz uprawnień do przeglądu rekrutacji."),
                ephemeral=True
            )
            return

        filter_val = status.value if status else "pending"
        guild_id = interaction.guild_id or 0

        query = "SELECT id, user_id, nick, login, uid, age, rp_points, status, created_at FROM applications WHERE guild_id = ?"
        params = [guild_id]
        if filter_val == "pending":
            query += " AND LOWER(status) = 'pending'"
        elif filter_val == "approved":
            query += " AND LOWER(status) IN ('accept', 'approved', 'accepted')"
        elif filter_val == "rejected":
            query += " AND LOWER(status) IN ('reject', 'rejected')"

        query += " ORDER BY id DESC LIMIT 15"
        rows = await self.bot.db.fetchall(query, tuple(params))

        if not rows:
            await interaction.response.send_message(
                embed=warning_embed("Brak Podań", f"Brak podań spełniających filtr: **{filter_val}**."),
                ephemeral=True
            )
            return

        from utils.embeds import info_embed
        embed = info_embed(
            f"📋 Pipeline Podań Rekrutacyjnych ({filter_val.capitalize()})",
            f"Wyświetlono {len(rows)} ostatnich zgłoszeń dla serwera **{interaction.guild.name}**."
        )

        for r in rows:
            st = (r["status"] or "pending").lower()
            st_emoji = "⏳" if st == "pending" else ("✅" if st in ("accept", "approved") else "❌")
            nick = r["nick"] or r["login"] or f"ID: {r['user_id']}"
            date_str = str(r["created_at"]).split()[0] if r["created_at"] else "-"
            embed.add_field(
                name=f"{st_emoji} #{r['id']} • {nick}",
                value=f"UID: `{r['uid'] or '-'}` | Wiek: `{r['age'] or '-'} lat` | RP: `{r['rp_points'] or '0'}`\nData: `{date_str}` | Discord: <@{r['user_id']}>",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rekrutacja_group.command(name="wyczysc_opuszczone", description="Automatycznie odrzuca lub czyści podania osób, które opuściły serwer.")
    async def cmd_rekrutacja_wyczysc_opuszczone(self, interaction: discord.Interaction) -> None:
        from views.application_views import _has_review_permission
        if not await _has_review_permission(interaction):
            await interaction.response.send_message(
                embed=error_embed("Brak Uprawnień", "Nie masz uprawnień do zarządzania rekrutacją."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=error_embed("Błąd", "Komenda dostępna tylko na serwerze."), ephemeral=True)
            return

        rows = await self.bot.db.fetchall(
            "SELECT id, user_id, nick, login FROM applications WHERE guild_id = ? AND LOWER(status) = 'pending'",
            (guild.id,)
        )

        cleaned_count = 0
        for r in rows:
            m = guild.get_member(r["user_id"])
            if m is None:
                await self.bot.db.execute(
                    """
                    UPDATE applications
                    SET status = 'reject', decision_by = ?, decision_by_name = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (interaction.user.id, f"{interaction.user} (/wyczysc_opuszczone)", "Opuszczenie serwera Discord przed decyzją", r["id"])
                )
                cleaned_count += 1

        if cleaned_count > 0:
            await interaction.followup.send(
                embed=success_embed(
                    "Wyczyszczono Opuszczone",
                    f"Pomyślnie zidentyfikowano i oznaczono jako odrzucone **{cleaned_count}** oczekujących podań osób, które nie znajdują się już na serwerze Discord."
                ),
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=info_embed(
                    "Wszystko w Porządku",
                    "Wszyscy kandydaci z oczekującymi podaniami znajdują się obecnie na serwerze Discord."
                ),
                ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationsCog(bot))

