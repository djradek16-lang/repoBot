"""
Moduł komend moderacyjnych.
Obsługuje karanie użytkowników (ban, softban, tempban, kick, timeout/mute, warn)
oraz zarządza historią ostrzeżeń, wyciszeniami i czyszczeniem wiadomości.

UWAGA (mute/unmute):
System wyciszeń NIE korzysta już z natywnego Discordowego "timeoutu"
(communication_disabled_until), ponieważ ten mechanizm blokuje użytkownikowi
również DOŁĄCZANIE do kanałów głosowych. Zamiast tego bot tworzy własną rolę
"Wyciszony", która:
  - na kanałach tekstowych blokuje wysyłanie wiadomości, reakcje i wątki,
  - na kanałach głosowych blokuje mówienie (speak/request_to_speak),
    ale NIE blokuje dołączania (connect pozostaje dozwolone).

Wymaga to w `core/constants.py` dodania dwóch nowych wartości do enuma
ModerationAction, jeśli jeszcze ich nie ma:

    class ModerationAction(Enum):
        ...
        MUTE = "mute"
        UNMUTE = "unmute"

Bot musi też mieć uprawnienie "Zarządzaj rolami" (manage_roles) i jego
najwyższa rola musi znajdować się WYŻEJ niż rola "Wyciszony" w hierarchii
serwera, inaczej nadawanie/zdejmowanie roli zakończy się błędem Forbidden.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.constants import ModerationAction
from core.checks import is_administrator
from services.punishment_service import PunishmentService
from services.permission_service import PermissionService
from utils.embeds import success_embed, error_embed, info_embed, moderation_embed, warning_embed
from utils.time_parser import parse_time, format_time

logger = logging.getLogger("AegisBot.Cogs.Moderation")


class ModerationCog(commands.Cog):
    """Komendy moderacyjne i zarządzanie karami."""

    MUTED_ROLE_NAME = "Wyciszony"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Śledzi zaplanowane zadania automatycznego zdjęcia wyciszenia,
        # aby móc je anulować przy ręcznym /unmute lub przy wyładowaniu coga.
        self._mute_tasks: Dict[Tuple[int, int], asyncio.Task] = {}

    @property
    def punishment_service(self) -> PunishmentService:
        return PunishmentService(self.bot.db)

    async def cog_unload(self) -> None:
        for task in self._mute_tasks.values():
            if not task.done():
                task.cancel()
        self._mute_tasks.clear()

    # ------------------------------------------------------------------ #
    # Zarządzanie rolą wyciszenia
    # ------------------------------------------------------------------ #

    async def _configure_channel_for_muted_role(
        self, channel: discord.abc.GuildChannel, role: discord.Role
    ) -> None:
        """Ustawia nadpisania uprawnień roli wyciszenia na pojedynczym kanale."""
        try:
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                await channel.set_permissions(
                    role,
                    send_messages=False,
                    add_reactions=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                    reason="Konfiguracja roli wyciszenia (AegisBot)"
                )
            elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                await channel.set_permissions(
                    role,
                    connect=True,       # użytkownik nadal może dołączyć do kanału głosowego
                    speak=False,        # ale nie może mówić
                    request_to_speak=False,
                    reason="Konfiguracja roli wyciszenia (AegisBot)"
                )
        except discord.Forbidden:
            logger.warning(
                f"Brak uprawnień do skonfigurowania roli wyciszenia na kanale {channel.id} "
                f"(serwer: {channel.guild.id})."
            )
        except discord.HTTPException as e:
            logger.error(f"Błąd HTTP przy konfiguracji kanału {channel.id} pod rolę wyciszenia: {e}")

    async def _get_or_create_muted_role(self, guild: discord.Guild) -> discord.Role:
        """Zwraca rolę wyciszenia na serwerze, tworząc ją i konfigurując, jeśli nie istnieje."""
        role = discord.utils.get(guild.roles, name=self.MUTED_ROLE_NAME)
        if role is not None:
            return role

        role = await guild.create_role(
            name=self.MUTED_ROLE_NAME,
            color=discord.Color.dark_gray(),
            hoist=False,
            mentionable=False,
            reason="Automatyczne utworzenie roli wyciszenia przez AegisBot"
        )

        try:
            target_position = max(guild.me.top_role.position - 1, 1)
            await role.edit(position=target_position)
        except discord.HTTPException:
            # Brak krytycznego znaczenia - nadpisania kanałów i tak zadziałają
            # niezależnie od pozycji roli na liście.
            pass

        results = await asyncio.gather(
            *(self._configure_channel_for_muted_role(ch, role) for ch in guild.channels),
            return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Błąd podczas wstępnej konfiguracji roli wyciszenia: {result}")

        return role

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """Automatycznie stosuje nadpisania uprawnień na nowo utworzonych kanałach,
        jeśli rola wyciszenia już istnieje na serwerze."""
        role = discord.utils.get(channel.guild.roles, name=self.MUTED_ROLE_NAME)
        if role is not None:
            await self._configure_channel_for_muted_role(channel, role)

    def _schedule_unmute(
        self, guild: discord.Guild, target: discord.Member, role: discord.Role, seconds: int
    ) -> None:
        """Planuje automatyczne zdjęcie roli wyciszenia po upływie danego czasu."""
        key = (guild.id, target.id)

        existing = self._mute_tasks.pop(key, None)
        if existing and not existing.done():
            existing.cancel()

        async def _unmute_timer() -> None:
            try:
                await asyncio.sleep(seconds)
                member = guild.get_member(target.id)
                if member is not None and role in member.roles:
                    await member.remove_roles(role, reason="Automatyczne wygaśnięcie wyciszenia")
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(f"Błąd podczas automatycznego zdejmowania wyciszenia dla {target.id}")
            finally:
                self._mute_tasks.pop(key, None)

        self._mute_tasks[key] = asyncio.create_task(_unmute_timer())

    # ------------------------------------------------------------------ #
    # Komendy karania
    # ------------------------------------------------------------------ #

    @app_commands.command(name="ban", description="Banuje użytkownika z serwera.")
    @app_commands.describe(target="Użytkownik do zbanowania", reason="Powód bana")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, target: discord.Member, reason: str = "Brak podanego powodu") -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.BAN,
            reason=reason
        )

        embed = moderation_embed("BAN", target, interaction.user, reason, case_id=case_id)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="softban", description="Wyrzuca użytkownika i czyści jego wiadomości z ostatnich 24h.")
    @app_commands.describe(target="Użytkownik", reason="Powód softbana")
    @app_commands.checks.has_permissions(ban_members=True)
    async def softban(self, interaction: discord.Interaction, target: discord.Member, reason: str = "Brak powodu") -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.SOFTBAN,
            reason=reason
        )
        embed = moderation_embed("SOFTBAN", target, interaction.user, reason, case_id=case_id)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="tempban", description="Banuje użytkownika tymczasowo na określony czas.")
    @app_commands.describe(target="Użytkownik", duration="Czas trwania (np. 1d, 12h)", reason="Powód")
    @app_commands.checks.has_permissions(ban_members=True)
    async def tempban(self, interaction: discord.Interaction, target: discord.Member, duration: str, reason: str = "Brak powodu") -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        seconds = parse_time(duration)
        if not seconds:
            await interaction.response.send_message(embed=error_embed("Błędny Format Czasu", "Podaj czas w formacie np. `12h`, `1d`, `3d`."), ephemeral=True)
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.BAN,
            reason=f"[Tempban: {duration}] {reason}",
            duration_seconds=seconds
        )

        embed = moderation_embed("TEMPBAN", target, interaction.user, reason, case_id=case_id, duration=format_time(seconds))
        await interaction.response.send_message(embed=embed)

        # Asynchroniczne wyczekanie na wygaśnięcie bana
        async def unban_timer():
            await asyncio.sleep(seconds)
            try:
                await interaction.guild.unban(target, reason="Automatyczne wygaśnięcie tempbana")
            except Exception:
                pass

        asyncio.create_task(unban_timer())

    @app_commands.command(name="unban", description="Cofa bana dla podanego ID użytkownika.")
    @app_commands.describe(user_id="ID zbanowanego użytkownika", reason="Powód odbanowania")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "Brak powodu") -> None:
        try:
            uid = int(user_id)
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(user, reason=f"[{interaction.user}] {reason}")
            await interaction.response.send_message(embed=success_embed("Odbanowano Użytkownika", f"Pomyślnie cofnięto bana dla **{user}** (`{uid}`)."))
        except (ValueError, discord.NotFound):
            await interaction.response.send_message(embed=error_embed("Błąd", "Nie odnaleziono zbanowanego użytkownika o podanym ID."), ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd API", f"Nie udało się odbanować użytkownika: {e}"), ephemeral=True)

    @app_commands.command(name="kick", description="Wyrzuca użytkownika z serwera.")
    @app_commands.describe(target="Użytkownik do wyrzucenia", reason="Powód wyrzucenia")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, target: discord.Member, reason: str = "Brak podanego powodu") -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.KICK,
            reason=reason
        )

        embed = moderation_embed("KICK", target, interaction.user, reason, case_id=case_id)
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ #
    # Wyciszenia (mute / unmute) - własna rola, nie natywny timeout
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="mute",
        description="Wycisza użytkownika (blokada czatu, kanały głosowe bez możliwości mówienia)."
    )
    @app_commands.describe(target="Użytkownik do wyciszenia", duration="Czas trwania (np. 10m, 2h, 1d)", reason="Powód wyciszenia")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, target: discord.Member, duration: str, reason: str = "Brak powodu") -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        seconds = parse_time(duration)
        if not seconds:
            await interaction.response.send_message(embed=error_embed("Błędny Format Czasu", "Podaj czas w formacie np. `10m`, `2h`, `1d`."), ephemeral=True)
            return

        await interaction.response.defer()

        muted_role = await self._get_or_create_muted_role(interaction.guild)

        if muted_role in target.roles:
            await interaction.followup.send(
                embed=error_embed("Nie można wykonać akcji", f"{target.mention} jest już wyciszony."),
                ephemeral=True
            )
            return

        try:
            await target.add_roles(muted_role, reason=f"[{interaction.user}] {reason}")
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed(
                    "Brak Uprawnień",
                    "Bot nie ma uprawnień do nadania roli wyciszenia. Sprawdź, czy rola bota "
                    f"znajduje się wyżej niż rola **{self.MUTED_ROLE_NAME}** w hierarchii serwera."
                ),
                ephemeral=True
            )
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.MUTE,
            reason=reason,
            duration_seconds=seconds
        )

        self._schedule_unmute(interaction.guild, target, muted_role, seconds)

        embed = moderation_embed("MUTE", target, interaction.user, reason, case_id=case_id, duration=format_time(seconds))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="unmute", description="Zdejmuje wyciszenie z użytkownika.")
    @app_commands.describe(target="Użytkownik do odciszenia", reason="Powód zdjęcia wyciszenia")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, target: discord.Member, reason: str = "Brak powodu") -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        muted_role = discord.utils.get(interaction.guild.roles, name=self.MUTED_ROLE_NAME)
        if muted_role is None or muted_role not in target.roles:
            await interaction.response.send_message(
                embed=error_embed("Błąd", f"{target.mention} nie jest obecnie wyciszony."),
                ephemeral=True
            )
            return

        key = (interaction.guild.id, target.id)
        task = self._mute_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

        try:
            await target.remove_roles(muted_role, reason=f"[{interaction.user}] {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Brak Uprawnień", "Bot nie ma uprawnień do zdjęcia roli wyciszenia."),
                ephemeral=True
            )
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.UNMUTE,
            reason=reason
        )

        embed = moderation_embed("UNMUTE", target, interaction.user, reason, case_id=case_id)
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ #
    # Ostrzeżenia
    # ------------------------------------------------------------------ #

    @app_commands.command(name="warn", description="NADAJE ostrzeżenie użytkownikowi.")
    @app_commands.describe(target="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, target: discord.Member, reason: str) -> None:
        can_mod, err_msg = PermissionService.can_moderate(interaction.user, target, interaction.guild.me)
        if not can_mod:
            await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", err_msg), ephemeral=True)
            return

        case_id = await self.punishment_service.apply_punishment(
            guild=interaction.guild,
            moderator=interaction.user,
            target=target,
            action=ModerationAction.WARN,
            reason=reason
        )

        embed = moderation_embed("WARN", target, interaction.user, reason, case_id=case_id)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="warnings", description="Wyświetla listę ostrzeżeń użytkownika.")
    @app_commands.describe(target="Użytkownik do sprawdzenia")
    async def warnings(self, interaction: discord.Interaction, target: discord.Member = None) -> None:
        target = target or interaction.user
        rows = await self.bot.db.fetchall(
            "SELECT warning_id, moderator_id, reason, created_at FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY warning_id DESC",
            (interaction.guild_id, target.id)
        )

        if not rows:
            await interaction.response.send_message(embed=info_embed("Ostrzeżenia", f"Użytkownik {target.mention} nie posiada żadnych ostrzeżeń."))
            return

        fields = []
        for r in rows:
            mod = interaction.guild.get_member(r["moderator_id"])
            mod_str = mod.mention if mod else f"ID: {r['moderator_id']}"
            fields.append({
                "name": f"Ostrzeżenie #{r['warning_id']} | {r['created_at']}",
                "value": f"**Moderator:** {mod_str}\n**Powód:** {r['reason']}",
                "inline": False
            })

        embed = info_embed(f"Ostrzeżenia {target.display_name}", f"Łączna liczba ostrzeżeń: **{len(rows)}**")
        for f in fields[:25]:
            embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delwarn", description="Usuwa konkretne ostrzeżenie według jego ID.")
    @app_commands.describe(warning_id="ID ostrzeżenia")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def delwarn(self, interaction: discord.Interaction, warning_id: int) -> None:
        cursor = await self.bot.db.execute("DELETE FROM warnings WHERE warning_id = ? AND guild_id = ?", (warning_id, interaction.guild_id))
        if cursor.rowcount > 0:
            await interaction.response.send_message(embed=success_embed("Usunięto Ostrzeżenie", f"Ostrzeżenie #{warning_id} zostało usunięte z bazy danych."))
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie odnaleziono ostrzeżenia o ID #{warning_id}."), ephemeral=True)

    @app_commands.command(name="clearwarns", description="Usuwa wszystkie ostrzeżenia wskazanego użytkownika.")
    @app_commands.describe(target="Użytkownik")
    @app_commands.checks.has_permissions(administrator=True)
    async def clearwarns(self, interaction: discord.Interaction, target: discord.Member) -> None:
        cursor = await self.bot.db.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (interaction.guild_id, target.id)
        )
        await interaction.response.send_message(embed=success_embed("Wyczyszczono Ostrzeżenia", f"Usunięto **{cursor.rowcount}** ostrzeżeń dla {target.mention}."))

    # ------------------------------------------------------------------ #
    # Zarządzanie kanałem
    # ------------------------------------------------------------------ #

    @app_commands.command(name="clear", description="Usuwa określoną liczbę wiadomości z kanału.")
    @app_commands.describe(amount="Liczba wiadomości do usunięcia (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, amount: int) -> None:
        if amount < 1 or amount > 100:
            await interaction.response.send_message(embed=error_embed("Błąd", "Liczba wiadomości musi znajdować się w przedziale 1 - 100."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(embed=success_embed("Wyczyszczono Kanał", f"Pomyślnie usunięto **{len(deleted)}** wiadomości."))

    @app_commands.command(name="slowmode", description="Ustawia opóźnienie wysyłania wiadomości (Slowmode) na kanale.")
    @app_commands.describe(seconds="Liczba sekund opóźnienia (0 wyłącza slowmode)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: int) -> None:
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return

        await ch.edit(slowmode_delay=seconds)
        if seconds > 0:
            await interaction.response.send_message(embed=success_embed("Ustawiono Slowmode", f"Ustawiono opóźnienie kanału na **{seconds} sekund**."))
        else:
            await interaction.response.send_message(embed=success_embed("Wyłączono Slowmode", "Opóźnienie wysyłania wiadomości zostało wyłączone."))

    @app_commands.command(name="lock", description="Zablokowuje możliwość pisania na kanale dla @everyone.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction) -> None:
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return

        overwrite = ch.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=warning_embed("Kanał Zablokowany 🔒", "Pisanie na tym kanale zostało zablokowane dla roli @everyone."))

    @app_commands.command(name="unlock", description="Odblokowuje możliwość pisania na kanale dla @everyone.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction) -> None:
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return

        overwrite = ch.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=success_embed("Kanał Odblokowany 🔓", "Pisanie na tym kanale zostało przywrócone dla roli @everyone."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
