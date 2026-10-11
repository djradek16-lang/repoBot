"""
Serwis zarządzający nakładaniem kar, zapisem do bazy danych, wysyłaniem wiadomości prywatnych oraz logowaniem zdarzeń.
"""

import logging
from datetime import datetime, timedelta
import discord

from core.database import Database
from core.constants import ModerationAction
from utils.embeds import moderation_embed, log_embed
from utils.time_parser import format_time

logger = logging.getLogger("AegisBot.PunishmentService")

class PunishmentService:
    """Zarządza aplikowaniem kar, tworzeniem spraw w bazie i powiadomieniami."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def apply_punishment(
        self,
        guild: discord.Guild,
        moderator: discord.User | discord.Member,
        target: discord.User | discord.Member,
        action: ModerationAction,
        reason: str,
        duration_seconds: int | None = None
    ) -> int:
        """
        Główna metoda nakładająca karę:
        1. Wysyła DM do użytkownika (jeśli możliwe)
        2. Zapisuje przypadek w bazie danych
        3. Wykonuje odpowiednią akcję na Discordzie
        4. Loguje przypadek na kanale logów
        5. Zwraca case_id
        """
        now = datetime.now()
        expires_at = (now + timedelta(seconds=duration_seconds)) if duration_seconds else None
        duration_str = format_time(duration_seconds) if duration_seconds else None

        # 1. Próba wysłania powiadomienia DM do ukaranego
        try:
            dm_embed = moderation_embed(
                action=action.value,
                target=target,
                moderator=moderator,
                reason=reason,
                duration=duration_str
            )
            dm_embed.title = f"🔔 Otrzymałeś karę na serwerze {guild.name}"
            await target.send(embed=dm_embed)
        except Exception:
            logger.info(f"Nie udało się wysłać DM o karze do użytkownika {target.id}")

        # 2. Zapis w bazie danych
        cursor = await self.db.execute(
            """
            INSERT INTO moderation_cases (guild_id, user_id, moderator_id, action, reason, duration, expires_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (guild.id, target.id, moderator.id, action.value, reason, duration_seconds, expires_at)
        )
        case_id = cursor.lastrowid

        # 3. Wykonanie akcji Discord API (jeśli target jest na serwerze)
        if isinstance(target, discord.Member):
            try:
                if action == ModerationAction.BAN:
                    await target.ban(reason=f"[{moderator}] {reason}")
                elif action == ModerationAction.SOFTBAN:
                    await target.ban(reason=f"[{moderator}] Softban: {reason}", delete_message_days=1)
                    await guild.unban(target, reason="Softban automatyczne odbanowanie")
                elif action == ModerationAction.KICK:
                    await target.kick(reason=f"[{moderator}] {reason}")
                elif action == ModerationAction.TIMEOUT:
                    if duration_seconds:
                        until = discord.utils.utcnow() + timedelta(seconds=duration_seconds)
                        await target.timeout(until, reason=f"[{moderator}] {reason}")
                elif action == ModerationAction.UNTIMEOUT:
                    await target.timeout(None, reason=f"[{moderator}] {reason}")
                elif action == ModerationAction.WARN:
                    await self.db.execute(
                        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
                        (guild.id, target.id, moderator.id, reason)
                    )
                    # Sprawdzenie progu ostrzeżeń
                    cfg = await self.db.fetchone("SELECT max_warnings FROM guild_config WHERE guild_id = ?", (guild.id,))
                    max_warns = cfg["max_warnings"] if cfg and cfg["max_warnings"] else 3
                    warn_count_row = await self.db.fetchone("SELECT COUNT(*) as count FROM warnings WHERE guild_id = ? AND user_id = ?", (guild.id, target.id))
                    warn_count = warn_count_row["count"] if warn_count_row else 1

                    if warn_count >= max_warns:
                        until = discord.utils.utcnow() + timedelta(hours=1)
                        await target.timeout(until, reason=f"[AutoMod Escalation] Osiągnięto limit {max_warns} ostrzeżeń")
            except discord.HTTPException as e:
                logger.error(f"Nie udało się wykonać akcji {action} na członku {target.id}: {e}")

        # 4. Wysyłanie loga na skonfigurowany kanał logów
        await self.send_mod_log(guild, action.value, target, moderator, reason, case_id, duration_str)

        return case_id

    async def send_mod_log(
        self,
        guild: discord.Guild,
        action: str,
        target: discord.User | discord.Member,
        moderator: discord.User | discord.Member,
        reason: str,
        case_id: int,
        duration_str: str | None = None
    ) -> None:
        """Wysyła log kary na oficjalny kanał logów serwera."""
        cfg = await self.db.fetchone(
            "SELECT log_channel_id, logs_enabled FROM guild_config WHERE guild_id = ?",
            (guild.id,)
        )
        if not cfg or not cfg["logs_enabled"] or not cfg["log_channel_id"]:
            return

        channel = guild.get_channel(cfg["log_channel_id"])
        if isinstance(channel, discord.TextChannel):
            embed = moderation_embed(
                action=action,
                target=target,
                moderator=moderator,
                reason=reason,
                case_id=case_id,
                duration=duration_str
            )
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass
