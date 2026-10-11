"""
Moduł zaawansowanych powitań i pożegnań użytkowników (Welcome & Goodbye).
Obsługuje wysyłanie czystych, przejrzystych i estetycznych embedów z oznaczeniem, nickiem w nawiasie,
czasem spędzonym na Discordzie, śledzeniem zaproszeń (Invite Tracker), automatycznym zapisem ról (Role Saver)
wraz z interaktywnymi przyciskami pod embedem (Przywróć role / Nie przywracaj) oraz ID użytkownika w stopce.
"""

import io
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import info_embed, success_embed, error_embed
from utils.card_generator import create_welcome_card, create_goodbye_card

logger = logging.getLogger("AegisBot.Cogs.Welcome")


class RestoreRolesView(discord.ui.View):
    """Widok z przyciskami decyzji o przywróceniu poprzednich ról użytkownika."""

    def __init__(self, member_id: int, role_count: int, timeout: Optional[float] = 86400) -> None:
        super().__init__(timeout=timeout)
        self.member_id = member_id

        self.add_item(discord.ui.Button(
            label=f"Przywróć role ({role_count})",
            style=discord.ButtonStyle.success,
            emoji="🔄",
            custom_id=f"roles_action:restore:{member_id}"
        ))
        self.add_item(discord.ui.Button(
            label="Nie przywracaj",
            style=discord.ButtonStyle.danger,
            emoji="✖️",
            custom_id=f"roles_action:dismiss:{member_id}"
        ))


class WelcomeCog(commands.Cog):
    """Zaawansowane zarządzanie zdarzeniami dołączania, opuszczania, Invite Trackerem i Role Saverem."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Pamięć podręczna zaproszeń: {guild_id: {invite_code: uses}}
        self.invites_cache: dict[int, dict[str, int]] = {}

    async def cog_load(self) -> None:
        """Inicjalizacja tabel bazy danych dla zaproszeń i ról oraz załadowanie pamięci podręcznej."""
        # 1. Tabela zaproszeń
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS member_invites (
                guild_id INTEGER NOT NULL,
                inviter_id INTEGER NOT NULL,
                invited_id INTEGER NOT NULL,
                code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, invited_id)
            );
            """
        )
        await self.bot.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_member_invites_inviter ON member_invites (guild_id, inviter_id);"
        )

        # 2. Tabela zapisanych ról użytkowników (Role Saver)
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_member_roles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                roles_json TEXT NOT NULL,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )

        if self.bot.is_ready():
            for guild in self.bot.guilds:
                await self._cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Zapisuje aktualne zaproszenia wszystkich serwerów do pamięci podręcznej bota."""
        for guild in self.bot.guilds:
            await self._cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        """Aktualizuje pamięć podręczną przy utworzeniu nowego zaproszenia."""
        if invite.guild:
            cached = self.invites_cache.setdefault(invite.guild.id, {})
            cached[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        """Usuwa zaproszenie z pamięci podręcznej po jego skasowaniu."""
        if invite.guild and invite.guild.id in self.invites_cache:
            self.invites_cache[invite.guild.id].pop(invite.code, None)

    async def _cache_guild_invites(self, guild: discord.Guild) -> None:
        """Pobiera i zapisuje wszystkie zaproszenia danej gildii."""
        if not guild.me.guild_permissions.manage_guild:
            return
        try:
            invites = await guild.invites()
            self.invites_cache[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}
        except (discord.Forbidden, discord.HTTPException):
            pass
        except Exception as e:
            logger.debug(f"Błąd pobierania zaproszeń dla gildii {guild.id}: {e}")

    async def _find_inviter(
        self,
        guild: discord.Guild
    ) -> tuple[Optional[discord.User | discord.Member], Optional[str]]:
        """
        Porównuje aktualny stan zaproszeń z pamięcią podręczną bota,
        znajduje zaproszenie, którego licznik użyć wzrósł, i aktualizuje cache.
        """
        if not guild.me.guild_permissions.manage_guild:
            return None, None

        old_invites = self.invites_cache.get(guild.id, {})
        try:
            current_invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None, None

        matched_inviter = None
        matched_code = None

        for inv in current_invites:
            code = inv.code
            uses = inv.uses or 0
            old_uses = old_invites.get(code, 0)
            if uses > old_uses:
                matched_inviter = inv.inviter
                matched_code = code
                break

        # Aktualizacja pamięci podręcznej po dołączeniu
        self.invites_cache[guild.id] = {inv.code: (inv.uses or 0) for inv in current_invites}
        return matched_inviter, matched_code

    async def _fetch_avatar_bytes(self, user: discord.User | discord.Member) -> bytes | None:
        """Bezpiecznie pobiera bajty awatara użytkownika."""
        try:
            return await user.display_avatar.read()
        except Exception as e:
            logger.debug(f"Nie udało się pobrać awatara dla {user.id}: {e}")
            return None

    async def _find_recent_audit_entry(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
        within_seconds: float = 6.0
    ) -> Optional[discord.AuditLogEntry]:
        """Szuka najświeższego wpisu w Audit Logu dla wskazanego użytkownika."""
        try:
            async for entry in guild.audit_logs(action=action, limit=5):
                if entry.target and getattr(entry.target, "id", None) == target_id:
                    age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                    if age <= within_seconds:
                        return entry
        except (discord.Forbidden, discord.HTTPException):
            pass
        except Exception as e:
            logger.debug(f"Błąd podczas odczytu Audit Log dla {target_id}: {e}")
        return None

    async def build_welcome_payload(
        self,
        member: discord.Member,
        raw_message: Optional[str] = None,
        roles_added: Optional[list[discord.Role]] = None,
        invite_info: Optional[str] = None,
        saved_roles: Optional[list[discord.Role]] = None,
        include_card: bool = False
    ) -> tuple[discord.Embed, Optional[discord.File]]:
        """Buduje czysty, estetyczny embed powitalny."""
        guild = member.guild
        count = guild.member_count
        now = discord.utils.utcnow()

        # Obliczanie wieku konta / czasu spędzonego na Discordzie
        account_created_ts = int(member.created_at.timestamp())
        account_age_days = (now - member.created_at).days
        account_age_str = f"<t:{account_created_ts}:D> (<t:{account_created_ts}:R>)"
        if account_age_days < 3:
            account_age_str += " ⚠️ **Nowe konto!**"

        # Formatowanie treści wiadomości
        template = raw_message or "Witaj {user} w społeczności serwera **{server}**! Rozgość się i zapoznaj z regulaminem."
        formatted_msg = template.format(
            user=member.mention,
            username=member.name,
            display_name=member.display_name,
            server=guild.name,
            count=count,
            account_age=f"{account_age_days} dni"
        )

        embed = discord.Embed(
            title="👋 Nowy członek na serwerze!",
            description=formatted_msg,
            color=0x2ECC71, # Estetyczna zieleń
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # Użytkownik: Nick + oznaczenie + pisemny nick w nawiasie
        if member.display_name != member.name:
            user_val = f"{member.display_name} — {member.mention} (`{member.name}`)"
        else:
            user_val = f"{member.mention} (`{member.name}`)"

        embed.add_field(
            name="👤 Użytkownik",
            value=user_val,
            inline=False
        )
        embed.add_field(
            name="🕒 Czas spędzony na Discordzie",
            value=account_age_str,
            inline=False
        )

        # Invite Tracker (Kto zaprosił)
        if invite_info:
            embed.add_field(
                name="🔗 Zaproszenie",
                value=invite_info,
                inline=False
            )

        # Informacja o poprzednich rolach (Role Saver)
        if saved_roles:
            roles_prev_str = ", ".join(r.mention for r in saved_roles)
            embed.add_field(
                name="🔄 Poprzednie role (użytkownik powrócił)",
                value=f"{roles_prev_str}\n*Kliknij przycisk poniżej, aby zdecydować o ich przywróceniu.*",
                inline=False
            )

        if roles_added:
            roles_str = ", ".join(r.mention for r in roles_added)
            embed.add_field(
                name="🛡️ Nadane role",
                value=roles_str,
                inline=False
            )

        embed.set_footer(
            text=f"ID użytkownika: {member.id}",
            icon_url=guild.icon.url if guild.icon else None
        )

        file = None
        if include_card:
            filename = f"welcome-{member.id}.png"
            avatar_bytes = await self._fetch_avatar_bytes(member)
            card_buf = await asyncio.to_thread(
                create_welcome_card,
                username=member.display_name,
                server_name=guild.name,
                avatar_bytes=avatar_bytes,
                member_count=count
            )
            file = discord.File(card_buf, filename=filename)
            embed.set_image(url=f"attachment://{filename}")

        return embed, file

    async def build_goodbye_payload(
        self,
        member: discord.Member,
        raw_message: Optional[str] = None,
        audit_entry: Optional[discord.AuditLogEntry] = None,
        include_card: bool = False
    ) -> tuple[discord.Embed, Optional[discord.File]]:
        """Buduje czysty embed pożegnalny (z nickiem, oznaczeniem, czasem na Discordzie, stażem, rolami i ID w stopce)."""
        guild = member.guild
        count = guild.member_count
        now = discord.utils.utcnow()

        # Obliczanie czasu spędzonego na Discordzie
        account_created_ts = int(member.created_at.timestamp())
        account_discord_str = f"<t:{account_created_ts}:D> (<t:{account_created_ts}:R>)"

        # Obliczanie stażu na serwerze
        stay_days = None
        if member.joined_at:
            stay_days = (now - member.joined_at).days
            joined_ts = int(member.joined_at.timestamp())
            if stay_days == 0:
                stay_str = f"<t:{joined_ts}:D> (<t:{joined_ts}:R> • mniej niż 1 dzień)"
            elif stay_days == 1:
                stay_str = f"<t:{joined_ts}:D> (<t:{joined_ts}:R> • 1 dzień)"
            else:
                stay_str = f"<t:{joined_ts}:D> (<t:{joined_ts}:R> • **{stay_days} dni**)"
        else:
            stay_str = "Brak informacji o dacie dołączenia"

        # Formatowanie treści
        template = raw_message or "Użytkownik **{username}** opuścił nasz serwer. Dziękujemy za wspólny czas!"
        formatted_msg = template.format(
            user=member.mention,
            username=member.name,
            display_name=member.display_name,
            server=guild.name,
            count=count,
            stay_duration=f"{stay_days} dni" if stay_days is not None else "Nieznany"
        )

        title = "🚪 Użytkownik opuścił serwer"
        color = 0xE74C3C # Czysta czerwień

        if audit_entry and audit_entry.action == discord.AuditLogAction.kick:
            title = "👢 Użytkownik został wyrzucony (Kick)"
            color = 0xEF4444
        elif audit_entry and audit_entry.action == discord.AuditLogAction.ban:
            title = "🔨 Użytkownik został zbanowany (Ban)"
            color = 0xDC2626

        embed = discord.Embed(
            title=title,
            description=formatted_msg,
            color=color,
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # Użytkownik: Nick + oznaczenie + pisemny nick w nawiasie
        if member.display_name != member.name:
            user_val = f"{member.display_name} — {member.mention} (`{member.name}`)"
        else:
            user_val = f"{member.mention} (`{member.name}`)"

        embed.add_field(
            name="👤 Użytkownik",
            value=user_val,
            inline=False
        )
        embed.add_field(
            name="🕒 Czas spędzony na Discordzie",
            value=account_discord_str,
            inline=False
        )
        embed.add_field(
            name="⏱️ Czas spędzony na serwerze",
            value=stay_str,
            inline=False
        )

        # Jakie miał role przed opuszczeniem serwera
        user_roles = [r.mention for r in member.roles if not r.is_default()]
        roles_text = ", ".join(user_roles) if user_roles else "*Brak ról*"
        if len(roles_text) > 1024:
            roles_text = roles_text[:1020] + "..."
        embed.add_field(
            name="🎭 Role",
            value=roles_text,
            inline=False
        )

        embed.add_field(
            name="👥 Pozostało członków",
            value=f"**{count}** osób",
            inline=True
        )

        if audit_entry and audit_entry.user:
            reason_text = audit_entry.reason or "Brak podanego powodu"
            embed.add_field(
                name="🛡️ Moderator",
                value=f"{audit_entry.user.mention} (`{reason_text}`)",
                inline=False
            )

        embed.set_footer(
            text=f"ID użytkownika: {member.id}",
            icon_url=guild.icon.url if guild.icon else None
        )

        file = None
        if include_card:
            filename = f"goodbye-{member.id}.png"
            avatar_bytes = await self._fetch_avatar_bytes(member)
            card_buf = await asyncio.to_thread(
                create_goodbye_card,
                username=member.display_name,
                server_name=guild.name,
                avatar_bytes=avatar_bytes,
                member_count=count,
                stay_days=stay_days
            )
            file = discord.File(card_buf, filename=filename)
            embed.set_image(url=f"attachment://{filename}")

        return embed, file

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Obsługuje dołączenie nowego użytkownika: nadanie ról, Invite Tracker, Role Saver i wysłanie powitania."""
        guild = member.guild

        # 1. Śledzenie zaproszenia (Invite Tracker)
        inviter, invite_code = await self._find_inviter(guild)
        invite_info = None

        if inviter:
            try:
                await self.bot.db.execute(
                    """
                    INSERT INTO member_invites (guild_id, inviter_id, invited_id, code)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, invited_id) DO UPDATE SET inviter_id = excluded.inviter_id, code = excluded.code
                    """,
                    (guild.id, inviter.id, member.id, invite_code)
                )
                row = await self.bot.db.fetchone(
                    "SELECT COUNT(*) as c FROM member_invites WHERE guild_id = ? AND inviter_id = ?",
                    (guild.id, inviter.id)
                )
                total_invited = row["c"] if row else 1
                invite_info = f"{inviter.mention} (`{inviter.name}`) • Kod: `{invite_code}` (Razem: **{total_invited}** osób)"
            except Exception as e:
                logger.error(f"Błąd zapisu zaproszenia w bazie danych: {e}")
                invite_info = f"{inviter.mention} (`{inviter.name}`) • Kod: `{invite_code}`"
        else:
            if guild.vanity_url_code:
                invite_info = f"Link niestandardowy (Vanity: `/{guild.vanity_url_code}`)"
            else:
                invite_info = "*Nieznane / Link bezpośredni*"

        # 2. Sprawdzenie poprzednich ról (Role Saver)
        saved_roles_row = await self.bot.db.fetchone(
            "SELECT roles_json FROM saved_member_roles WHERE guild_id = ? AND user_id = ?",
            (guild.id, member.id)
        )
        previous_roles: list[discord.Role] = []
        if saved_roles_row and saved_roles_row["roles_json"]:
            try:
                saved_role_ids = json.loads(saved_roles_row["roles_json"])
                for rid in saved_role_ids:
                    r = guild.get_role(rid)
                    if r and r < guild.me.top_role:
                        previous_roles.append(r)
            except Exception as e:
                logger.debug(f"Błąd odczytu poprzednich ról dla {member.id}: {e}")

        # 3. Konfiguracja gildii i autorole
        cfg = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (guild.id,))
        if not cfg:
            return

        roles_to_add: list[discord.Role] = []
        if cfg["auto_role_id"]:
            role = guild.get_role(cfg["auto_role_id"])
            if role and role < guild.me.top_role:
                roles_to_add.append(role)
        if cfg["verification_enabled"] and cfg["unverified_role_id"]:
            role = guild.get_role(cfg["unverified_role_id"])
            if role and role < guild.me.top_role:
                roles_to_add.append(role)

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="Automatyczna rola powitalna dla nowego członka")
            except discord.HTTPException as e:
                logger.error(f"Nie udało się nadać autoroli nowemu użytkownikowi {member.id}: {e}")

        # 4. Wysłanie wiadomości powitalnej
        if cfg["welcome_enabled"] and cfg["welcome_channel_id"]:
            channel = guild.get_channel(cfg["welcome_channel_id"])
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    embed, file = await self.build_welcome_payload(
                        member=member,
                        raw_message=cfg["welcome_message"],
                        roles_added=roles_to_add,
                        invite_info=invite_info,
                        saved_roles=previous_roles
                    )

                    restore_view = None
                    if previous_roles:
                        restore_view = RestoreRolesView(member_id=member.id, role_count=len(previous_roles))

                    if file:
                        await channel.send(embed=embed, file=file, view=restore_view)
                    else:
                        await channel.send(embed=embed, view=restore_view)
                except discord.HTTPException as e:
                    logger.error(f"Nie udało się wysłać powitania na kanał {channel.id}: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Obsługuje opuszczenie serwera przez użytkownika: zapisuje role do Role Savera i wysyła embed."""
        guild = member.guild

        # 1. Zapisanie ról użytkownika do Role Savera (bez @everyone oraz ról zarządzanych przez boty)
        user_role_ids = [r.id for r in member.roles if not r.is_default() and not r.managed]
        if user_role_ids:
            try:
                await self.bot.db.execute(
                    """
                    INSERT INTO saved_member_roles (guild_id, user_id, roles_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET roles_json = excluded.roles_json, saved_at = CURRENT_TIMESTAMP
                    """,
                    (guild.id, member.id, json.dumps(user_role_ids))
                )
            except Exception as e:
                logger.error(f"Błąd zapisu ról do bazy dla {member.id}: {e}")

        # 2. Wysłanie wiadomości pożegnalnej
        cfg = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (guild.id,))
        if not cfg or not cfg["goodbye_enabled"] or not cfg["goodbye_channel_id"]:
            return

        channel = guild.get_channel(cfg["goodbye_channel_id"])
        if channel and isinstance(channel, discord.TextChannel):
            audit_entry = await self._find_recent_audit_entry(guild, discord.AuditLogAction.kick, member.id)
            if not audit_entry:
                audit_entry = await self._find_recent_audit_entry(guild, discord.AuditLogAction.ban, member.id)

            try:
                embed, file = await self.build_goodbye_payload(
                    member=member,
                    raw_message=cfg["goodbye_message"],
                    audit_entry=audit_entry
                )
                if file:
                    await channel.send(embed=embed, file=file)
                else:
                    await channel.send(embed=embed)
            except discord.HTTPException as e:
                logger.error(f"Nie udało się wysłać pożegnania na kanał {channel.id}: {e}")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Obsługa kliknięć przycisków Role Savera (Przywróć role / Nie przywracaj)."""
        if not interaction.data or interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("roles_action:"):
            return

        parts = custom_id.split(":")
        if len(parts) != 3:
            return
        action, target_user_id_str = parts[1], parts[2]
        target_user_id = int(target_user_id_str)

        # Sprawdzenie uprawnień administracyjnych
        if not interaction.user.guild_permissions.manage_roles and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Tylko zarząd może decydować o przywróceniu ról.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if not guild:
            return

        target_member = guild.get_member(target_user_id)

        if action == "restore":
            if not target_member:
                await interaction.response.send_message("❌ Użytkownika nie ma już na serwerze.", ephemeral=True)
                return

            row = await self.bot.db.fetchone(
                "SELECT roles_json FROM saved_member_roles WHERE guild_id = ? AND user_id = ?",
                (guild.id, target_user_id)
            )
            if not row:
                await interaction.response.send_message("ℹ️ Role tego użytkownika zostały już wcześniej przetworzone.", ephemeral=True)
                return

            try:
                role_ids = json.loads(row["roles_json"])
                roles_to_add = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid) and guild.get_role(rid) < guild.me.top_role]
            except Exception:
                roles_to_add = []

            if roles_to_add:
                try:
                    await target_member.add_roles(*roles_to_add, reason=f"Przywrócenie poprzednich ról przez {interaction.user}")
                    await self.bot.db.execute(
                        "DELETE FROM saved_member_roles WHERE guild_id = ? AND user_id = ?",
                        (guild.id, target_user_id)
                    )

                    disabled_view = discord.ui.View()
                    disabled_view.add_item(discord.ui.Button(
                        label=f"Przywrócono role przez {interaction.user.display_name}",
                        style=discord.ButtonStyle.secondary,
                        emoji="✅",
                        disabled=True
                    ))
                    await interaction.response.edit_message(view=disabled_view)
                    await interaction.followup.send(
                        f"✅ Pomyślnie przywrócono **{len(roles_to_add)}** ról dla {target_member.mention}.",
                        ephemeral=True
                    )
                except discord.HTTPException as e:
                    await interaction.response.send_message(f"❌ Nie udało się nadać ról: {e}", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ Żadna z zapisanych ról nie mogła zostać przywrócona.", ephemeral=True)

        elif action == "dismiss":
            await self.bot.db.execute(
                "DELETE FROM saved_member_roles WHERE guild_id = ? AND user_id = ?",
                (guild.id, target_user_id)
            )
            disabled_view = discord.ui.View()
            disabled_view.add_item(discord.ui.Button(
                label=f"Odrzucono przywrócenie przez {interaction.user.display_name}",
                style=discord.ButtonStyle.secondary,
                emoji="❌",
                disabled=True
            ))
            await interaction.response.edit_message(view=disabled_view)
            await interaction.followup.send(f"Odrzucono przywrócenie poprzednich ról dla użytkownika.", ephemeral=True)

    # =========================================================================
    # KOMENDY SLASH DLA GRUPY /welcome
    # =========================================================================

    welcome_group = app_commands.Group(
        name="welcome",
        description="Zarządzanie zaawansowanym modułem powitań",
        default_permissions=discord.Permissions(administrator=True)
    )

    @welcome_group.command(
        name="test",
        description="Generuje testowy embed powitania lub pożegnania na bieżącym kanale."
    )
    @app_commands.describe(
        typ="Wybierz, czy chcesz przetestować Powitanie czy Pożegnanie",
        uzytkownik="Opcjonalny użytkownik, dla którego ma zostać wygenerowany test (domyślnie Ty)",
        z_karta="Czy załączyć również wygenerowaną grafikę PNG (domyślnie: Nie, sam czysty embed)",
        test_przyciski_rol="Czy dołączyć testowe przyciski przywracania ról pod embedem (domyślnie: Nie)"
    )
    @is_administrator()
    async def test_welcome(
        self,
        interaction: discord.Interaction,
        typ: Literal["Powitanie (Join)", "Pożegnanie (Leave)"] = "Powitanie (Join)",
        uzytkownik: Optional[discord.Member] = None,
        z_karta: bool = False,
        test_przyciski_rol: bool = False
    ) -> None:
        """Testuje embed powitania/pożegnania z nickiem, oznaczeniem, czasem na Discordzie, zaproszeniem i rolami."""
        await interaction.response.defer()
        target_member = uzytkownik or interaction.user
        if not isinstance(target_member, discord.Member):
            target_member = interaction.guild.get_member(target_member.id) or interaction.user

        cfg = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))

        if typ == "Powitanie (Join)":
            raw_msg = cfg["welcome_message"] if cfg else None
            test_roles = []
            if cfg and cfg["auto_role_id"]:
                role = interaction.guild.get_role(cfg["auto_role_id"])
                if role:
                    test_roles.append(role)

            test_invite_info = f"{interaction.user.mention} (`{interaction.user.name}`) • Kod: `rpg2026` (Razem: **5** osób)"

            # Opcjonalne przykładowe role do testu przycisków
            mock_saved_roles = None
            test_view = None
            if test_przyciski_rol:
                mock_saved_roles = [r for r in interaction.guild.roles if not r.is_default()][:2]
                test_view = RestoreRolesView(member_id=target_member.id, role_count=len(mock_saved_roles) or 1)

            embed, file = await self.build_welcome_payload(
                member=target_member,
                raw_message=raw_msg,
                roles_added=test_roles,
                invite_info=test_invite_info,
                saved_roles=mock_saved_roles,
                include_card=z_karta
            )
            embed.title = f"🧪 [TEST] {embed.title}"
            kwargs: dict = {"embed": embed}
            if file:
                kwargs["file"] = file
            if test_view is not None:
                kwargs["view"] = test_view
            await interaction.followup.send(**kwargs)
        else:
            raw_msg = cfg["goodbye_message"] if cfg else None
            embed, file = await self.build_goodbye_payload(
                member=target_member,
                raw_message=raw_msg,
                include_card=z_karta
            )
            embed.title = f"🧪 [TEST] {embed.title}"
            if file:
                await interaction.followup.send(embed=embed, file=file)
            else:
                await interaction.followup.send(embed=embed)

    @welcome_group.command(name="set_channel", description="Ustawia kanał, na który będą wysyłane powitania.")
    @app_commands.describe(kanal="Kanał tekstowy dla wiadomości powitalnych")
    @is_administrator()
    async def set_welcome_channel(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, welcome_channel_id, welcome_enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id = excluded.welcome_channel_id, welcome_enabled = 1
            """,
            (interaction.guild_id, kanal.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Kanał powitań", f"Wiadomości powitalne będą teraz wysyłane na {kanal.mention}."),
            ephemeral=True
        )

    @welcome_group.command(name="set_message", description="Ustawia niestandardowy szablon wiadomości powitalnej.")
    @app_commands.describe(tresc="Dostępne tagi: {user}, {username}, {display_name}, {server}, {count}, {account_age}")
    @is_administrator()
    async def set_welcome_message(self, interaction: discord.Interaction, tresc: str) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, welcome_message)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET welcome_message = excluded.welcome_message
            """,
            (interaction.guild_id, tresc)
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Szablon powitania zaktualizowany",
                f"Nowy szablon wiadomości:\n>>> {tresc}\n\n*Dostępne zmienne: `{'{user}'}`, `{'{username}'}`, `{'{server}'}`, `{'{count}'}`, `{'{account_age}'}`*"
            ),
            ephemeral=True
        )

    @welcome_group.command(name="toggle", description="Włącza lub wyłącza wysyłanie powitań.")
    @is_administrator()
    async def toggle_welcome(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.fetchone("SELECT welcome_enabled FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        current_state = bool(cfg["welcome_enabled"]) if cfg and cfg["welcome_enabled"] is not None else False
        new_state = not current_state

        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, welcome_enabled)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET welcome_enabled = excluded.welcome_enabled
            """,
            (interaction.guild_id, 1 if new_state else 0)
        )
        stan_txt = "WŁĄCZONE ✅" if new_state else "WYŁĄCZONE ❌"
        await interaction.response.send_message(
            embed=info_embed("Moduł powitań", f"Moduł powitań jest teraz: **{stan_txt}**."),
            ephemeral=True
        )

    @welcome_group.command(name="status", description="Pokazuje aktualny stan konfiguracji powitań i pożegnań.")
    @is_administrator()
    async def welcome_status(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        guild = interaction.guild

        w_channel = f"<#{cfg['welcome_channel_id']}>" if cfg and cfg["welcome_channel_id"] else "*Brak (nieustawiony)*"
        g_channel = f"<#{cfg['goodbye_channel_id']}>" if cfg and cfg["goodbye_channel_id"] else "*Brak (nieustawiony)*"
        auto_role = f"<@&{cfg['auto_role_id']}>" if cfg and cfg["auto_role_id"] else "*Brak*"
        
        w_enabled = "🟢 Włączone" if cfg and cfg["welcome_enabled"] else "🔴 Wyłączone"
        g_enabled = "🟢 Włączone" if cfg and cfg["goodbye_enabled"] else "🔴 Wyłączone"

        embed = discord.Embed(
            title=f"⚙️ Status Modułu Powitań & Pożegnań — {guild.name}",
            color=0x3B82F6,
            timestamp=datetime.now()
        )
        embed.add_field(name="👋 Powitania (Welcome)", value=f"**Status:** {w_enabled}\n**Kanał:** {w_channel}", inline=True)
        embed.add_field(name="🚪 Pożegnania (Goodbye)", value=f"**Status:** {g_enabled}\n**Kanał:** {g_channel}", inline=True)
        embed.add_field(name="🛡️ Auto-Rola dla nowych", value=auto_role, inline=False)
        
        w_msg = cfg["welcome_message"] if cfg and cfg["welcome_message"] else "Witaj {user} w społeczności {server}!"
        g_msg = cfg["goodbye_message"] if cfg and cfg["goodbye_message"] else "Użytkownik {username} opuścił serwer."
        
        embed.add_field(name="📝 Szablon powitań", value=f"```{w_msg[:300]}```", inline=False)
        embed.add_field(name="📝 Szablon pożegnań", value=f"```{g_msg[:300]}```", inline=False)
        embed.set_footer(text="Użyj /welcome test lub /goodbye test, aby zobaczyć podgląd na żywo!")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =========================================================================
    # KOMENDY SLASH DLA GRUPY /goodbye
    # =========================================================================

    goodbye_group = app_commands.Group(
        name="goodbye",
        description="Zarządzanie zaawansowanym modułem pożegnań",
        default_permissions=discord.Permissions(administrator=True)
    )

    @goodbye_group.command(name="test", description="Generuje testowy embed pożegnania na bieżącym kanale.")
    @app_commands.describe(
        uzytkownik="Opcjonalny użytkownik do przetestowania (domyślnie Ty)",
        z_karta="Czy załączyć również wygenerowaną grafikę PNG (domyślnie: Nie)"
    )
    @is_administrator()
    async def test_goodbye(
        self,
        interaction: discord.Interaction,
        uzytkownik: Optional[discord.Member] = None,
        z_karta: bool = False
    ) -> None:
        """Dedykowana komenda testowania embeda pożegnania."""
        await interaction.response.defer()
        target_member = uzytkownik or interaction.user
        if not isinstance(target_member, discord.Member):
            target_member = interaction.guild.get_member(target_member.id) or interaction.user

        cfg = await self.bot.db.fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        raw_msg = cfg["goodbye_message"] if cfg else None

        embed, file = await self.build_goodbye_payload(
            member=target_member,
            raw_message=raw_msg,
            include_card=z_karta
        )
        embed.title = f"🧪 [TEST] {embed.title}"
        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)

    @goodbye_group.command(name="set_channel", description="Ustawia kanał, na który będą wysyłane pożegnania.")
    @app_commands.describe(kanal="Kanał tekstowy dla wiadomości pożegnalnych")
    @is_administrator()
    async def set_goodbye_channel(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, goodbye_channel_id, goodbye_enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET goodbye_channel_id = excluded.goodbye_channel_id, goodbye_enabled = 1
            """,
            (interaction.guild_id, kanal.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Kanał pożegnań", f"Wiadomości pożegnalne będą teraz wysyłane na {kanal.mention}."),
            ephemeral=True
        )

    @goodbye_group.command(name="set_message", description="Ustawia niestandardowy szablon wiadomości pożegnalnej.")
    @app_commands.describe(tresc="Dostępne tagi: {user}, {username}, {display_name}, {server}, {count}, {stay_duration}")
    @is_administrator()
    async def set_goodbye_message(self, interaction: discord.Interaction, tresc: str) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, goodbye_message)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET goodbye_message = excluded.goodbye_message
            """,
            (interaction.guild_id, tresc)
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Szablon pożegnania zaktualizowany",
                f"Nowy szablon wiadomości pożegnalnej:\n>>> {tresc}\n\n*Dostępne zmienne: `{'{user}'}`, `{'{username}'}`, `{'{server}'}`, `{'{count}'}`, `{'{stay_duration}'}`*"
            ),
            ephemeral=True
        )

    @goodbye_group.command(name="toggle", description="Włącza lub wyłącza wysyłanie pożegnań.")
    @is_administrator()
    async def toggle_goodbye(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.fetchone("SELECT goodbye_enabled FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        current_state = bool(cfg["goodbye_enabled"]) if cfg and cfg["goodbye_enabled"] is not None else False
        new_state = not current_state

        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, goodbye_enabled)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET goodbye_enabled = excluded.goodbye_enabled
            """,
            (interaction.guild_id, 1 if new_state else 0)
        )
        stan_txt = "WŁĄCZONE ✅" if new_state else "WYŁĄCZONE ❌"
        await interaction.response.send_message(
            embed=info_embed("Moduł pożegnań", f"Moduł pożegnań jest teraz: **{stan_txt}**."),
            ephemeral=True
        )

    # =========================================================================
    # KOMENDA SPRAWDZANIA ZAPROSZEŃ
    # =========================================================================

    @app_commands.command(name="invites", description="Sprawdza statystyki zaproszeń użytkownika na serwerze.")
    @app_commands.describe(uzytkownik="Użytkownik, którego zaproszenia chcesz sprawdzić (domyślnie Ty)")
    async def check_invites(
        self,
        interaction: discord.Interaction,
        uzytkownik: Optional[discord.Member] = None
    ) -> None:
        """Wyświetla liczbę osób zaproszonych przez wskazanego użytkownika."""
        target = uzytkownik or interaction.user
        guild_id = interaction.guild_id

        # Pobieranie liczby zaproszonych
        count_row = await self.bot.db.fetchone(
            "SELECT COUNT(*) as c FROM member_invites WHERE guild_id = ? AND inviter_id = ?",
            (guild_id, target.id)
        )
        total_invited = count_row["c"] if count_row else 0

        # Pobieranie ostatnich 5 zaproszonych osób
        rows = await self.bot.db.fetchall(
            """
            SELECT invited_id, code, created_at
            FROM member_invites
            WHERE guild_id = ? AND inviter_id = ?
            ORDER BY created_at DESC LIMIT 5
            """,
            (guild_id, target.id)
        )

        embed = discord.Embed(
            title=f"🔗 Statystyki Zaproszeń — {target.display_name}",
            color=0x3B82F6,
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="📊 Liczba zaproszonych osób",
            value=f"**{total_invited}** członków",
            inline=False
        )

        if rows:
            recent_list = []
            for r in rows:
                user_id = r["invited_id"]
                code = r["code"] or "brak"
                recent_list.append(f"• <@{user_id}> (kod: `{code}`)")
            embed.add_field(
                name="👥 Ostatnio dołączyli z jego linku",
                value="\n".join(recent_list),
                inline=False
            )
        else:
            embed.add_field(
                name="👥 Ostatnio dołączyli",
                value="*Brak zarejestrowanych dołączeń z linków tego użytkownika.*",
                inline=False
            )

        embed.set_footer(text=f"ID użytkownika: {target.id}")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
