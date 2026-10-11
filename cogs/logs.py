"""
Moduł rejestracji zdarzeń serwera (Audit Logging).
Rejestruje wiadomości, członków, kary, głos, strukturę serwera (kanały/role),
ustawienia serwera, emoji/naklejki, wątki, zaproszenia, webhooki, wydarzenia,
instancje sceny oraz akcje AutoMod. Każda kategoria zdarzeń ma własny kolor embeda.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import create_embed

logger = logging.getLogger("AegisBot.Cogs.Logs")

FIELD_LIMIT = 1024
CACHE_TTL_SECONDS = 60


class Color:
    """Paleta kolorów embedów, pogrupowana wg charakteru zdarzenia."""
    CREATE = 0x34C759    # zielony  - coś powstało
    DELETE = 0xFF3B30    # czerwony - coś usunięto
    UPDATE = 0xFF9500    # pomarańcz - coś zmieniono
    JOIN = 0x0A84FF       # niebieski - dołączenie
    LEAVE = 0x8E8E93      # szary - wyjście
    VOICE = 0xBF5AF2      # fiolet - aktywność głosowa
    PUNISH = 0xD70015     # ciemny czerwony - kara
    RESTORE = 0x30D5C8    # turkus - zdjęcie kary / unban
    AUTOMOD = 0xFFD60A    # żółty - AutoMod


def _truncate(text: str | None, placeholder: str = "*Brak treści*") -> str:
    if not text:
        return placeholder
    if len(text) <= FIELD_LIMIT:
        return text
    return f"{text[: FIELD_LIMIT - 6]} [...]"


class LogsCog(commands.GroupCog, name="logs", description="Konfiguracja i przegląd logów serwera."):
    """Automatyczne logowanie aktywności na serwerze."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # (guild_id, kategoria) -> (kanał_lub_None, timestamp_pobrania)
        self._channel_cache: dict[tuple[int, str], tuple[discord.TextChannel | None, float]] = {}

    async def cog_load(self) -> None:
        """Wywoływane automatycznie przy dodaniu coga — dokłada brakujące kolumny w bazie."""
        await self._ensure_schema()

    async def _ensure_schema(self) -> None:
        """
        Samo-naprawiająca się migracja: dokłada kolumnę member_log_channel_id do
        guild_config, jeśli jeszcze jej nie ma. SQLite zgłasza wtedy błąd
        "duplicate column name" gdy kolumna już istnieje — traktujemy to jako
        potwierdzenie, że schemat jest aktualny, a nie jako awarię.
        """
        try:
            await self.bot.db.execute(
                "ALTER TABLE guild_config ADD COLUMN member_log_channel_id INTEGER"
            )
            logger.info("Dodano brakującą kolumnę member_log_channel_id do guild_config.")
        except Exception as exc:
            if "duplicate column" in str(exc).lower():
                logger.debug("Kolumna member_log_channel_id już istnieje w guild_config — pomijam migrację.")
            else:
                logger.exception(
                    "Nie udało się sprawdzić/dodać kolumny member_log_channel_id w guild_config. "
                    "Logi wejść/wyjść będą działać w trybie awaryjnym (bez osobnego kanału) do czasu naprawy."
                )

    # ------------------------------------------------------------------ #
    # Pomocnicze
    # ------------------------------------------------------------------ #

    async def get_log_channel(self, guild_id: int, category: str = "default") -> discord.TextChannel | None:
        """
        category="default"  -> zwykłe logi (wiadomości, role, kanały, itd.)
        category="members"  -> osobny kanał na wejścia/wyjścia (join/leave).
                                Jeśli member_log_channel_id nie jest ustawiony w bazie,
                                spada z powrotem na główny kanał logów.
        """
        cache_key = (guild_id, category)
        cached = self._channel_cache.get(cache_key)
        if cached and (time.monotonic() - cached[1]) < CACHE_TTL_SECONDS:
            return cached[0]

        try:
            cfg = await self.bot.db.fetchone(
                "SELECT log_channel_id, logs_enabled, member_log_channel_id "
                "FROM guild_config WHERE guild_id = ?",
                (guild_id,),
            )
        except Exception as exc:
            if "no such column" in str(exc).lower() and "member_log_channel_id" in str(exc):
                # Migracja z cog_load nie zdążyła/nie mogła się wykonać — spadamy na starą kolumnę,
                # kategoria "members" po prostu użyje głównego kanału logów.
                logger.warning(
                    "Kolumna member_log_channel_id nadal nie istnieje w guild_config (guild_id=%s) — "
                    "logi wejść/wyjść idą tymczasowo na główny kanał.",
                    guild_id,
                )
                try:
                    cfg = await self.bot.db.fetchone(
                        "SELECT log_channel_id, logs_enabled FROM guild_config WHERE guild_id = ?",
                        (guild_id,),
                    )
                    if cfg is not None:
                        cfg = {**dict(cfg), "member_log_channel_id": None}
                except Exception:
                    logger.exception("Nie udało się pobrać konfiguracji logów (fallback) dla guild_id=%s", guild_id)
                    return None
            else:
                logger.exception("Nie udało się pobrać konfiguracji logów dla guild_id=%s", guild_id)
                return None

        channel: discord.TextChannel | None = None
        if cfg and cfg["logs_enabled"]:
            if category == "members":
                target_channel_id = cfg["member_log_channel_id"] or cfg["log_channel_id"]
            else:
                target_channel_id = cfg["log_channel_id"]

            if target_channel_id:
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    logger.warning("Bot nie widzi guild_id=%s przy próbie pobrania kanału logów", guild_id)
                else:
                    resolved = guild.get_channel(target_channel_id)
                    if isinstance(resolved, discord.TextChannel):
                        channel = resolved
                    else:
                        logger.warning(
                            "Skonfigurowany kanał logów (%s, kategoria=%s) w guild_id=%s nie istnieje "
                            "lub nie jest kanałem tekstowym",
                            target_channel_id, category, guild_id,
                        )

        self._channel_cache[cache_key] = (channel, time.monotonic())
        return channel

    def invalidate_cache(self, guild_id: int) -> None:
        """Wywołuj po zmianie configu (np. z panelu ustawień), by od razu odświeżyć kanały tej gildii."""
        for key in [k for k in self._channel_cache if k[0] == guild_id]:
            self._channel_cache.pop(key, None)

    async def _find_audit_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
        within_seconds: float = 5.0,
    ) -> discord.AuditLogEntry | None:
        """
        Szuka najświeższego wpisu Audit Log pasującego do danego targetu, wykonanego w ciągu
        ostatnich `within_seconds` sekund — tak, by nie podpiąć przypadkiem starej, niepowiązanej akcji.
        Wymaga uprawnienia bota "Wyświetl Dziennik Audytu"; jeśli go brak, po cichu zwraca None.
        """
        try:
            async for entry in guild.audit_logs(action=action, limit=5):
                if entry.target is not None and getattr(entry.target, "id", None) == target_id:
                    age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                    if age <= within_seconds:
                        return entry
            return None
        except discord.Forbidden:
            return None
        except Exception:
            logger.exception(
                "Błąd podczas odczytu Audit Log (action=%s, guild_id=%s)", action, guild.id
            )
            return None

    async def _send_log(
        self,
        guild_id: int,
        title: str,
        fields: list[dict[str, Any]],
        *,
        color: int,
        description: str = "",
        category: str = "default",
        thumbnail_url: str | None = None,
    ) -> None:
        channel = await self.get_log_channel(guild_id, category)
        if not channel:
            return

        embed = create_embed(
            title=title, description=description, color=color, fields=fields, thumbnail_url=thumbnail_url
        )
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(
                "Brak uprawnień do wysłania logu '%s' na kanale #%s (guild_id=%s)",
                title, channel.id, guild_id,
            )
        except discord.HTTPException:
            logger.exception(
                "Nie udało się wysłać logu '%s' na kanale #%s (guild_id=%s)",
                title, channel.id, guild_id,
            )

    # ------------------------------------------------------------------ #
    # Wiadomości
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        fields = [
            {"name": "Autor", "value": f"{message.author.mention} (`{message.author.id}`)", "inline": True},
            {"name": "Kanał", "value": message.channel.mention, "inline": True},
            {"name": "Treść", "value": _truncate(message.content, "*Brak treści tekstowej (załącznik/embed)*"), "inline": False},
        ]

        entry = await self._find_audit_actor(message.guild, discord.AuditLogAction.message_delete, message.author.id)
        if entry is not None and entry.user is not None and entry.user.id != message.author.id:
            fields.append({"name": "Usunięte Przez", "value": f"{entry.user.mention} (`{entry.user.id}`)", "inline": True})

        await self._send_log(
            message.guild.id, "🗑️ Usunięto Wiadomość", fields, color=Color.DELETE,
            thumbnail_url=message.author.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or not messages[0].guild:
            return
        guild, channel = messages[0].guild, messages[0].channel
        fields = [
            {"name": "Kanał", "value": channel.mention, "inline": True},
            {"name": "Liczba Wiadomości", "value": str(len(messages)), "inline": True},
        ]
        await self._send_log(guild.id, "🧹 Zbiorcze Usunięcie Wiadomości", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author.bot or not before.guild or before.content == after.content:
            return
        fields = [
            {"name": "Autor", "value": f"{before.author.mention} (`{before.author.id}`)", "inline": True},
            {"name": "Kanał", "value": before.channel.mention, "inline": True},
            {"name": "Przed edycją", "value": _truncate(before.content), "inline": False},
            {"name": "Po edycji", "value": _truncate(after.content), "inline": False},
        ]
        await self._send_log(
            before.guild.id, "✏️ Edytowano Wiadomość", fields, color=Color.UPDATE,
            thumbnail_url=before.author.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_reaction_clear(self, message: discord.Message, reactions: list[discord.Reaction]) -> None:
        if not message.guild:
            return
        fields = [
            {"name": "Kanał", "value": message.channel.mention, "inline": True},
            {"name": "Liczba Usuniętych Reakcji", "value": str(len(reactions)), "inline": True},
        ]
        await self._send_log(message.guild.id, "🧼 Wyczyszczono Reakcje", fields, color=Color.DELETE)

    # ------------------------------------------------------------------ #
    # Członkowie
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        age_days = (discord.utils.utcnow() - member.created_at).days
        fields = [
            {"name": "Użytkownik", "value": f"{member.mention} (`{member.id}`)", "inline": True},
            {"name": "Wiek Konta", "value": f"{age_days} dni", "inline": True},
        ]
        await self._send_log(
            member.guild.id, "📥 Nowy Członek", fields, color=Color.JOIN, category="members",
            thumbnail_url=member.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        roles = ", ".join(r.mention for r in member.roles if not r.is_default()) or "*Brak*"
        fields = [
            {"name": "Użytkownik", "value": f"{member} (`{member.id}`)", "inline": True},
            {"name": "Role", "value": _truncate(roles), "inline": False},
        ]

        kick_entry = await self._find_audit_actor(member.guild, discord.AuditLogAction.kick, member.id)
        if kick_entry is not None and kick_entry.user is not None:
            fields.insert(1, {"name": "Wyrzucony Przez", "value": f"{kick_entry.user.mention} (`{kick_entry.user.id}`)", "inline": True})
            fields.insert(2, {"name": "Powód", "value": _truncate(kick_entry.reason, "*Brak podanego powodu*"), "inline": False})
            title, color = "👢 Wyrzucono Użytkownika (Kick)", Color.PUNISH
        else:
            title, color = "📤 Członek Opuścił Serwer", Color.LEAVE

        await self._send_log(
            member.guild.id, title, fields, color=color, category="members",
            thumbnail_url=member.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        fields = [{"name": "Użytkownik", "value": f"{user} (`{user.id}`)", "inline": True}]

        ban_entry = await self._find_audit_actor(guild, discord.AuditLogAction.ban, user.id)
        if ban_entry is not None and ban_entry.user is not None:
            fields.append({"name": "Zbanowany Przez", "value": f"{ban_entry.user.mention} (`{ban_entry.user.id}`)", "inline": True})
            fields.append({"name": "Powód", "value": _truncate(ban_entry.reason, "*Brak podanego powodu*"), "inline": False})

        await self._send_log(
            guild.id, "🔨 Zbanowano Użytkownika", fields, color=Color.PUNISH,
            thumbnail_url=user.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        fields = [{"name": "Użytkownik", "value": f"{user} (`{user.id}`)", "inline": True}]
        await self._send_log(
            guild.id, "🕊️ Odbanowano Użytkownika", fields, color=Color.RESTORE,
            thumbnail_url=user.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            fields = [
                {"name": "Użytkownik", "value": f"{after.mention} (`{after.id}`)", "inline": True},
                {"name": "Stary Pseudonim", "value": before.nick or before.name, "inline": True},
                {"name": "Nowy Pseudonim", "value": after.nick or after.name, "inline": True},
            ]
            await self._send_log(
                before.guild.id, "📝 Zmiana Pseudonimu", fields, color=Color.UPDATE,
                thumbnail_url=after.display_avatar.url,
            )

        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]
            if added or removed:
                fields = [{"name": "Użytkownik", "value": f"{after.mention} (`{after.id}`)", "inline": True}]
                if added:
                    fields.append({"name": "Dodane Role", "value": ", ".join(added), "inline": False})
                if removed:
                    fields.append({"name": "Odebrane Role", "value": ", ".join(removed), "inline": False})
                await self._send_log(
                    before.guild.id, "🎭 Aktualizacja Ról Członka", fields, color=Color.UPDATE,
                    thumbnail_url=after.display_avatar.url,
                )

        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until and after.is_timed_out():
                fields = [
                    {"name": "Użytkownik", "value": f"{after.mention} (`{after.id}`)", "inline": True},
                    {"name": "Wyciszony Do", "value": discord.utils.format_dt(after.timed_out_until, "F"), "inline": True},
                ]
                timeout_entry = await self._find_audit_actor(before.guild, discord.AuditLogAction.member_update, after.id)
                if timeout_entry is not None and timeout_entry.user is not None:
                    fields.append({"name": "Wyciszony Przez", "value": f"{timeout_entry.user.mention}", "inline": True})
                    if timeout_entry.reason:
                        fields.append({"name": "Powód", "value": _truncate(timeout_entry.reason), "inline": False})
                await self._send_log(
                    before.guild.id, "🔇 Nałożono Timeout", fields, color=Color.PUNISH,
                    thumbnail_url=after.display_avatar.url,
                )
            elif before.timed_out_until:
                fields = [{"name": "Użytkownik", "value": f"{after.mention} (`{after.id}`)", "inline": True}]
                await self._send_log(
                    before.guild.id, "🔊 Zdjęto Timeout", fields, color=Color.RESTORE,
                    thumbnail_url=after.display_avatar.url,
                )

        if before.guild_avatar != after.guild_avatar:
            fields = [{"name": "Użytkownik", "value": f"{after.mention} (`{after.id}`)", "inline": True}]
            await self._send_log(
                before.guild.id, "🖼️ Zmieniono Avatar Serwerowy", fields, color=Color.UPDATE,
                thumbnail_url=after.display_avatar.url,
            )

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        """Zmiany globalne (nazwa użytkownika / avatar) - logowane w każdej wspólnej gildii."""
        changes = []
        if before.name != after.name:
            changes.append(("Nazwa Użytkownika", f"{before.name} → {after.name}"))
        if before.global_name != after.global_name:
            changes.append(("Nazwa Wyświetlana", f"{before.global_name} → {after.global_name}"))
        if before.avatar != after.avatar:
            changes.append(("Avatar", "Zmieniono globalny avatar"))
        if not changes:
            return

        for guild in self.bot.guilds:
            if guild.get_member(after.id) is None:
                continue
            fields = [{"name": "Użytkownik", "value": f"{after.mention} (`{after.id}`)", "inline": False}]
            for name, value in changes:
                fields.append({"name": name, "value": _truncate(value), "inline": True})
            await self._send_log(guild.id, "👤 Aktualizacja Profilu Użytkownika", fields, color=Color.UPDATE)

    # ------------------------------------------------------------------ #
    # Kanały głosowe
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return

        if before.channel != after.channel:
            if before.channel is None and after.channel is not None:
                fields = [
                    {"name": "Użytkownik", "value": f"{member.mention} (`{member.id}`)", "inline": True},
                    {"name": "Kanał Głosowy", "value": after.channel.mention, "inline": True},
                ]
                await self._send_log(member.guild.id, "🎙️ Dołączono do Kanału Głosowego", fields, color=Color.VOICE)
            elif before.channel is not None and after.channel is None:
                fields = [
                    {"name": "Użytkownik", "value": f"{member.mention} (`{member.id}`)", "inline": True},
                    {"name": "Kanał Głosowy", "value": before.channel.mention, "inline": True},
                ]
                await self._send_log(member.guild.id, "🔇 Opuszczono Kanał Głosowy", fields, color=Color.VOICE)
            else:
                fields = [
                    {"name": "Użytkownik", "value": f"{member.mention} (`{member.id}`)", "inline": True},
                    {"name": "Z Kanału", "value": before.channel.mention, "inline": True},
                    {"name": "Na Kanał", "value": after.channel.mention, "inline": True},
                ]
                await self._send_log(member.guild.id, "🔀 Przełączono Kanał Głosowy", fields, color=Color.VOICE)
            return

        toggles = [
            ("self_mute", "🔈 Wyciszył(a) Mikrofon", "🔈 Włączył(a) Mikrofon"),
            ("self_deaf", "🔕 Wyciszył(a) Dźwięk", "🔔 Włączył(a) Dźwięk"),
            ("self_stream", "🖥️ Rozpoczął(ęła) Stream", "🖥️ Zakończył(a) Stream"),
        ]
        for attr, on_title, off_title in toggles:
            if getattr(before, attr) != getattr(after, attr) and after.channel is not None:
                title = on_title if getattr(after, attr) else off_title
                fields = [
                    {"name": "Użytkownik", "value": f"{member.mention} (`{member.id}`)", "inline": True},
                    {"name": "Kanał", "value": after.channel.mention, "inline": True},
                ]
                await self._send_log(member.guild.id, title, fields, color=Color.VOICE)

    # ------------------------------------------------------------------ #
    # Kanały i kategorie
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        fields = [
            {"name": "Kanał", "value": f"{channel.mention} (`{channel.id}`)", "inline": True},
            {"name": "Typ", "value": str(channel.type), "inline": True},
        ]
        await self._send_log(channel.guild.id, "➕ Utworzono Kanał", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        fields = [
            {"name": "Kanał", "value": f"#{channel.name} (`{channel.id}`)", "inline": True},
            {"name": "Typ", "value": str(channel.type), "inline": True},
        ]
        await self._send_log(channel.guild.id, "➖ Usunięto Kanał", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Nazwa", f"{before.name} → {after.name}"))
        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
            if before.topic != after.topic:
                changes.append(("Temat", f"{_truncate(before.topic)} → {_truncate(after.topic)}"))
            if before.slowmode_delay != after.slowmode_delay:
                changes.append(("Slowmode", f"{before.slowmode_delay}s → {after.slowmode_delay}s"))
            if before.nsfw != after.nsfw:
                changes.append(("NSFW", f"{before.nsfw} → {after.nsfw}"))
        if before.category != after.category:
            changes.append(("Kategoria", f"{before.category or '—'} → {after.category or '—'}"))
        if before.overwrites != after.overwrites:
            changes.append(("Uprawnienia", "Zmieniono uprawnienia kanału"))
        if not changes:
            return

        fields = [{"name": "Kanał", "value": f"{after.mention} (`{after.id}`)", "inline": False}]
        for name, value in changes:
            fields.append({"name": name, "value": _truncate(value), "inline": True})
        await self._send_log(after.guild.id, "🔧 Zaktualizowano Kanał", fields, color=Color.UPDATE)

    # ------------------------------------------------------------------ #
    # Role
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        fields = [{"name": "Rola", "value": f"{role.mention} (`{role.id}`)", "inline": True}]
        await self._send_log(role.guild.id, "➕ Utworzono Rolę", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        fields = [{"name": "Rola", "value": f"@{role.name} (`{role.id}`)", "inline": True}]
        await self._send_log(role.guild.id, "➖ Usunięto Rolę", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Nazwa", f"{before.name} → {after.name}"))
        if before.color != after.color:
            changes.append(("Kolor", f"{before.color} → {after.color}"))
        if before.hoist != after.hoist:
            changes.append(("Wyświetlana Osobno", f"{before.hoist} → {after.hoist}"))
        if before.mentionable != after.mentionable:
            changes.append(("Można Oznaczyć", f"{before.mentionable} → {after.mentionable}"))
        if before.permissions != after.permissions:
            changes.append(("Uprawnienia", "Zmieniono uprawnienia roli"))
        if before.position != after.position:
            changes.append(("Pozycja", f"{before.position} → {after.position}"))
        if not changes:
            return

        fields = [{"name": "Rola", "value": f"{after.mention} (`{after.id}`)", "inline": False}]
        for name, value in changes:
            fields.append({"name": name, "value": _truncate(value), "inline": True})
        await self._send_log(after.guild.id, "🎭 Zaktualizowano Rolę", fields, color=Color.UPDATE)

    # ------------------------------------------------------------------ #
    # Emoji / naklejki
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_emojis_update(
        self, guild: discord.Guild, before: list[discord.Emoji], after: list[discord.Emoji]
    ) -> None:
        added = [e for e in after if e not in before]
        removed = [e for e in before if e not in after]
        if added:
            fields = [{"name": "Dodane Emoji", "value": ", ".join(str(e) for e in added), "inline": False}]
            await self._send_log(guild.id, "😀 Dodano Emoji", fields, color=Color.CREATE)
        if removed:
            fields = [{"name": "Usunięte Emoji", "value": ", ".join(f":{e.name}:" for e in removed), "inline": False}]
            await self._send_log(guild.id, "🗑️ Usunięto Emoji", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_guild_stickers_update(
        self, guild: discord.Guild, before: list[discord.GuildSticker], after: list[discord.GuildSticker]
    ) -> None:
        added = [s for s in after if s not in before]
        removed = [s for s in before if s not in after]
        if added:
            fields = [{"name": "Dodane Naklejki", "value": ", ".join(s.name for s in added), "inline": False}]
            await self._send_log(guild.id, "🏷️ Dodano Naklejkę", fields, color=Color.CREATE)
        if removed:
            fields = [{"name": "Usunięte Naklejki", "value": ", ".join(s.name for s in removed), "inline": False}]
            await self._send_log(guild.id, "🗑️ Usunięto Naklejkę", fields, color=Color.DELETE)

    # ------------------------------------------------------------------ #
    # Wątki
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        fields = [
            {"name": "Wątek", "value": f"{thread.mention} (`{thread.id}`)", "inline": True},
            {"name": "Kanał Nadrzędny", "value": thread.parent.mention if thread.parent else "—", "inline": True},
        ]
        await self._send_log(thread.guild.id, "🧵 Utworzono Wątek", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        fields = [{"name": "Wątek", "value": f"#{thread.name} (`{thread.id}`)", "inline": True}]
        await self._send_log(thread.guild.id, "🗑️ Usunięto Wątek", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Nazwa", f"{before.name} → {after.name}"))
        if before.archived != after.archived:
            changes.append(("Zarchiwizowany", f"{before.archived} → {after.archived}"))
        if before.locked != after.locked:
            changes.append(("Zablokowany", f"{before.locked} → {after.locked}"))
        if not changes:
            return
        fields = [{"name": "Wątek", "value": f"{after.mention} (`{after.id}`)", "inline": False}]
        for name, value in changes:
            fields.append({"name": name, "value": value, "inline": True})
        await self._send_log(after.guild.id, "🧵 Zaktualizowano Wątek", fields, color=Color.UPDATE)

    # ------------------------------------------------------------------ #
    # Zaproszenia / webhooki
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        fields = [
            {"name": "Kod", "value": invite.code, "inline": True},
            {"name": "Kanał", "value": invite.channel.mention if invite.channel else "—", "inline": True},
            {"name": "Autor", "value": f"{invite.inviter}" if invite.inviter else "Nieznany", "inline": True},
        ]
        await self._send_log(invite.guild.id, "🔗 Utworzono Zaproszenie", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        fields = [{"name": "Kod", "value": invite.code, "inline": True}]
        await self._send_log(invite.guild.id, "🔗 Usunięto Zaproszenie", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        fields = [{"name": "Kanał", "value": channel.mention, "inline": True}]
        await self._send_log(channel.guild.id, "🪝 Zaktualizowano Webhooki Kanału", fields, color=Color.UPDATE)

    # ------------------------------------------------------------------ #
    # Serwer / wydarzenia / sceny
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Nazwa Serwera", f"{before.name} → {after.name}"))
        if before.owner_id != after.owner_id:
            changes.append(("Właściciel", f"<@{before.owner_id}> → <@{after.owner_id}>"))
        if before.icon != after.icon:
            changes.append(("Ikona", "Zmieniono ikonę serwera"))
        if before.verification_level != after.verification_level:
            changes.append(("Poziom Weryfikacji", f"{before.verification_level} → {after.verification_level}"))
        if not changes:
            return
        fields = [{"name": name, "value": _truncate(value), "inline": True} for name, value in changes]
        await self._send_log(
            after.id, "🏰 Zaktualizowano Serwer", fields, color=Color.UPDATE,
            thumbnail_url=after.icon.url if after.icon else None,
        )

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent) -> None:
        fields = [{"name": "Wydarzenie", "value": f"{event.name} (`{event.id}`)", "inline": False}]
        await self._send_log(event.guild.id, "📅 Utworzono Wydarzenie", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent) -> None:
        fields = [{"name": "Wydarzenie", "value": event.name, "inline": False}]
        await self._send_log(event.guild.id, "🗑️ Usunięto Wydarzenie", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ) -> None:
        if before.status == after.status:
            return
        fields = [
            {"name": "Wydarzenie", "value": after.name, "inline": True},
            {"name": "Status", "value": f"{before.status} → {after.status}", "inline": True},
        ]
        await self._send_log(after.guild.id, "📅 Zmieniono Status Wydarzenia", fields, color=Color.UPDATE)

    @commands.Cog.listener()
    async def on_stage_instance_create(self, stage_instance: discord.StageInstance) -> None:
        fields = [{"name": "Temat", "value": stage_instance.topic, "inline": True}]
        await self._send_log(stage_instance.guild.id, "🎤 Rozpoczęto Scenę", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_stage_instance_delete(self, stage_instance: discord.StageInstance) -> None:
        fields = [{"name": "Temat", "value": stage_instance.topic, "inline": True}]
        await self._send_log(stage_instance.guild.id, "🎤 Zakończono Scenę", fields, color=Color.DELETE)

    # ------------------------------------------------------------------ #
    # Komendy konfiguracyjne (/logs ...)
    # ------------------------------------------------------------------ #

    @app_commands.command(name="setmemberchannel", description="Ustaw osobny kanał na logi wejść/wyjść członków.")
    @app_commands.describe(channel="Kanał tekstowy, na który mają iść logi dołączeń i wyjść.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_member_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        assert interaction.guild is not None
        try:
            await self.bot.db.execute(
                "INSERT INTO guild_config (guild_id, member_log_channel_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET member_log_channel_id = excluded.member_log_channel_id",
                (interaction.guild.id, channel.id),
            )
        except Exception:
            logger.exception("Nie udało się zapisać member_log_channel_id dla guild_id=%s", interaction.guild.id)
            await interaction.response.send_message(
                "❌ Nie udało się zapisać ustawienia w bazie. Spróbuj ponownie później.", ephemeral=True
            )
            return

        self.invalidate_cache(interaction.guild.id)
        embed = create_embed(
            title="✅ Ustawiono Kanał Wejść/Wyjść",
            description=f"Logi dołączeń i wyjść członków będą teraz wysyłane na {channel.mention}.",
            color=Color.CREATE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="resetmemberchannel",
        description="Wyłącz osobny kanał wejść/wyjść — logi wrócą na główny kanał logów.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def reset_member_channel(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            await self.bot.db.execute(
                "UPDATE guild_config SET member_log_channel_id = NULL WHERE guild_id = ?",
                (interaction.guild.id,),
            )
        except Exception:
            logger.exception("Nie udało się wyczyścić member_log_channel_id dla guild_id=%s", interaction.guild.id)
            await interaction.response.send_message(
                "❌ Nie udało się zapisać ustawienia w bazie. Spróbuj ponownie później.", ephemeral=True
            )
            return

        self.invalidate_cache(interaction.guild.id)
        embed = create_embed(
            title="✅ Zresetowano Kanał Wejść/Wyjść",
            description="Logi dołączeń i wyjść wrócą na główny kanał logów.",
            color=Color.RESTORE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "🚫 Potrzebujesz uprawnienia **Zarządzaj Serwerem**, by użyć tej komendy.", ephemeral=True
            )
            return
        logger.exception("Nieobsłużony błąd komendy /logs", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Wystąpił nieoczekiwany błąd.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Wystąpił nieoczekiwany błąd.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # AutoMod
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule: discord.AutoModRule) -> None:
        fields = [{"name": "Reguła", "value": rule.name, "inline": True}]
        await self._send_log(rule.guild.id, "🛡️ Utworzono Regułę AutoMod", fields, color=Color.CREATE)

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule: discord.AutoModRule) -> None:
        fields = [{"name": "Reguła", "value": rule.name, "inline": True}]
        await self._send_log(rule.guild.id, "🛡️ Usunięto Regułę AutoMod", fields, color=Color.DELETE)

    @commands.Cog.listener()
    async def on_automod_rule_update(self, rule: discord.AutoModRule) -> None:
        fields = [{"name": "Reguła", "value": rule.name, "inline": True}]
        await self._send_log(rule.guild.id, "🛡️ Zaktualizowano Regułę AutoMod", fields, color=Color.UPDATE)

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction) -> None:
        fields = [
            {"name": "Użytkownik", "value": f"<@{execution.user_id}> (`{execution.user_id}`)", "inline": True},
            {"name": "Kanał", "value": f"<#{execution.channel_id}>" if execution.channel_id else "—", "inline": True},
            {"name": "Reguła", "value": str(execution.rule_id), "inline": True},
            {"name": "Treść", "value": _truncate(execution.content), "inline": False},
        ]
        await self._send_log(execution.guild_id, "🛡️ AutoMod Zablokował Wiadomość", fields, color=Color.AUTOMOD)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LogsCog(bot))
