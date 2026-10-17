"""
Moduł śledzenia czasu spędzonego na kanałach głosowych (MySQL).
Zlicza czas per dzień i udostępnia komendę /stats z interaktywnym rankingiem TOP 10.
"""

import logging
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import info_embed, success_embed, error_embed, create_embed
from core.checks import is_administrator
import config

logger = logging.getLogger("AegisBot.Cogs.Activity")

TIMEZONE = ZoneInfo("Europe/Warsaw")
DAILY_REPORT_TIME = dtime(hour=23, minute=59, tzinfo=TIMEZONE)
MIDNIGHT_RESET_TIME = dtime(hour=0, minute=0, tzinfo=TIMEZONE)

SCOPE_ORDER = ["day", "week", "month", "all"]
SCOPE_LABELS = {"day": "Dzień", "week": "Tydzień", "month": "Miesiąc", "all": "Cały czas"}
SCOPE_TITLES = {"day": "Dzisiaj", "week": "Ostatnie 7 dni", "month": "Ostatnie 30 dni", "all": "Cały czas śledzenia"}
SCOPE_DAYS_BACK = {"day": 0, "week": 6, "month": 29, "all": None}


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _clean_name(name: str) -> str:
    return discord.utils.escape_markdown(name)


def _split_by_day(start: datetime, end: datetime) -> list[tuple[str, int]]:
    start = start.astimezone(TIMEZONE)
    end = end.astimezone(TIMEZONE)
    segments = []
    current = start
    while current.date() < end.date():
        next_midnight = datetime.combine(current.date() + timedelta(days=1), dtime.min, tzinfo=TIMEZONE)
        segments.append((current.date().isoformat(), int((next_midnight - current).total_seconds())))
        current = next_midnight
    segments.append((current.date().isoformat(), int((end - current).total_seconds())))
    return segments


class ScopeButton(discord.ui.Button):
    def __init__(self, cog: "ActivityCog", scope: str, active: bool) -> None:
        super().__init__(
            label=SCOPE_LABELS[scope],
            style=discord.ButtonStyle.success if active else discord.ButtonStyle.secondary,
            custom_id=f"stats_scope_{scope}"
        )
        self.cog = cog
        self.scope = scope

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = await self.cog.build_stats_embed(interaction.guild, self.scope)
        view = StatsView(self.cog, self.scope)
        await interaction.response.edit_message(embed=embed, view=view)


class StatsView(discord.ui.View):
    def __init__(self, cog: "ActivityCog", active_scope: str) -> None:
        super().__init__(timeout=None)
        for scope in SCOPE_ORDER:
            self.add_item(ScopeButton(cog, scope, active=(scope == active_scope)))


class ActivityCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._voice_join_times: dict[tuple[int, int], datetime] = {}

    async def _remember_name(self, guild_id: int, user_id: int, display_name: str) -> None:
        try:
            await self.bot.db.execute(
                """
                INSERT INTO member_names (guild_id, user_id, display_name)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE display_name = VALUES(display_name)
                """,
                (str(guild_id), str(user_id), display_name)
            )
            if hasattr(self.bot.db, "commit"):
                await self.bot.db.commit()
        except Exception as e:
            logger.debug(f"Nie udało się zapisać nicku {user_id} dla serwera {guild_id}: {e}")

    async def _resolve_name(self, guild: discord.Guild, user_id: int) -> tuple[str, str]:
        member = guild.get_member(user_id)
        if member:
            name = _clean_name(member.display_name)
            await self._remember_name(guild.id, user_id, name)
            return member.mention, name

        mention = f"<@{user_id}>"
        try:
            row = await self.bot.db.fetchone(
                "SELECT display_name FROM member_names WHERE guild_id = %s AND user_id = %s",
                (str(guild.id), str(user_id))
            )
        except Exception:
            row = None
        name = row["display_name"] if row and row["display_name"] else f"Użytkownik {user_id}"
        return mention, name

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot:
            return

        await self._remember_name(member.guild.id, member.id, _clean_name(member.display_name))

        key = (member.guild.id, member.id)
        afk_channel = member.guild.afk_channel

        was_tracked = before.channel is not None and before.channel != afk_channel
        should_track = after.channel is not None and after.channel != afk_channel

        if was_tracked and not should_track:
            joined_at = self._voice_join_times.pop(key, None)
            if joined_at is None:
                return
            await self._save_voice_time(member.guild.id, member.id, joined_at, datetime.now(TIMEZONE))
        elif not was_tracked and should_track:
            self._voice_join_times[key] = datetime.now(TIMEZONE)

    async def _save_voice_time(self, guild_id: int, user_id: int, start: datetime, end: datetime) -> None:
        if end <= start:
            return
        for day, seconds in _split_by_day(start, end):
            if seconds <= 0:
                continue
            await self.bot.db.execute(
                """
                INSERT INTO voice_activity (guild_id, user_id, date, seconds)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE seconds = seconds + VALUES(seconds)
                """,
                (str(guild_id), str(user_id), day, seconds)
            )
        if hasattr(self.bot.db, "commit"):
            await self.bot.db.commit()

    async def _flush_all_sessions(self) -> None:
        now = datetime.now(TIMEZONE)
        for key, joined_at in list(self._voice_join_times.items()):
            guild_id, user_id = key
            await self._save_voice_time(guild_id, user_id, joined_at, now)
            self._voice_join_times[key] = now

    async def _recover_active_sessions(self) -> None:
        await self.bot.wait_until_ready()
        now = datetime.now(TIMEZONE)
        recovered = 0
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                if channel == guild.afk_channel:
                    continue
                for member in channel.members:
                    if member.bot:
                        continue
                    await self._remember_name(guild.id, member.id, _clean_name(member.display_name))
                    key = (guild.id, member.id)
                    if key not in self._voice_join_times:
                        self._voice_join_times[key] = now
                        recovered += 1
        if recovered:
            logger.info(f"Odtworzono {recovered} trwających sesji głosowych po (re)połączeniu.")

    async def cog_unload(self) -> None:
        self.daily_report.cancel()
        self.weekly_report.cancel()
        self.flush_voice_sessions.cancel()
        self.midnight_reset.cancel()
        now = datetime.now(TIMEZONE)
        for (guild_id, user_id), joined_at in list(self._voice_join_times.items()):
            await self._save_voice_time(guild_id, user_id, joined_at, now)
        self._voice_join_times.clear()

    async def cog_load(self) -> None:
        # Użycie VARCHAR zamiast TEXT w tabelach z kluczami głównymi (wymóg MySQL)
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_activity (
                guild_id VARCHAR(64) NOT NULL,
                user_id VARCHAR(64) NOT NULL,
                date VARCHAR(32) NOT NULL,
                seconds INT NOT NULL,
                PRIMARY KEY (guild_id, user_id, date)
            )
            """
        )
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS member_names (
                guild_id VARCHAR(64) NOT NULL,
                user_id VARCHAR(64) NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        if hasattr(self.bot.db, "commit"):
            await self.bot.db.commit()

        self.bot.loop.create_task(self._recover_active_sessions())

        self.daily_report.start()
        self.weekly_report.start()
        self.flush_voice_sessions.start()
        self.midnight_reset.start()

    @tasks.loop(minutes=5)
    async def flush_voice_sessions(self) -> None:
        await self._flush_all_sessions()

    @flush_voice_sessions.before_loop
    async def before_flush_voice_sessions(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=MIDNIGHT_RESET_TIME)
    async def midnight_reset(self) -> None:
        await self._flush_all_sessions()

    @midnight_reset.before_loop
    async def before_midnight_reset(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=DAILY_REPORT_TIME)
    async def daily_report(self) -> None:
        for guild in self.bot.guilds:
            try:
                row = await self.bot.db.fetchone(
                    "SELECT stats_channel_id FROM guild_config WHERE guild_id = %s",
                    (str(guild.id),)
                )
                if not row or not row["stats_channel_id"]:
                    continue

                channel = guild.get_channel(row["stats_channel_id"])
                if not channel:
                    continue

                embed = await self.build_stats_embed(guild, "day")
                embed.title = f"{embed.title} — {datetime.now(TIMEZONE).strftime('%d.%m.%Y')}"
                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Błąd wysyłania dziennego raportu aktywności dla serwera {guild.id}: {e}", exc_info=True)

    @daily_report.before_loop
    async def before_daily_report(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=DAILY_REPORT_TIME)
    async def weekly_report(self) -> None:
        now = datetime.now(TIMEZONE)
        if now.weekday() != 2:
            return

        for guild in self.bot.guilds:
            try:
                row = await self.bot.db.fetchone(
                    "SELECT weekly_stats_channel_id FROM guild_config WHERE guild_id = %s",
                    (str(guild.id),)
                )
                if not row or not row["weekly_stats_channel_id"]:
                    continue

                channel = guild.get_channel(row["weekly_stats_channel_id"])
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue

                embed = await self.build_weekly_management_embed(guild)
                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Błąd wysyłania tygodniowego raportu aktywności dla zarządu (serwer {guild.id}): {e}", exc_info=True)

    @weekly_report.before_loop
    async def before_weekly_report(self) -> None:
        await self.bot.wait_until_ready()

    async def build_stats_embed(self, guild: discord.Guild, scope: str) -> discord.Embed:
        label = SCOPE_TITLES[scope]
        days_back = SCOPE_DAYS_BACK[scope]
        guild_id_str = str(guild.id)

        if days_back is None:
            rows = await self.bot.db.fetchall(
                "SELECT user_id, SUM(seconds) AS total_seconds FROM voice_activity WHERE guild_id = %s GROUP BY user_id",
                (guild_id_str,)
            )
            since_date = None
        else:
            since_date = (datetime.now(TIMEZONE).date() - timedelta(days=days_back)).isoformat()
            rows = await self.bot.db.fetchall(
                "SELECT user_id, SUM(seconds) AS total_seconds FROM voice_activity WHERE guild_id = %s AND date >= %s GROUP BY user_id",
                (guild_id_str, since_date)
            )

        totals: dict[int, int] = {int(row["user_id"]): row["total_seconds"] or 0 for row in rows}

        now = datetime.now(TIMEZONE)
        for (g_id, user_id), joined_at in self._voice_join_times.items():
            if g_id != guild.id:
                continue
            segments = _split_by_day(joined_at, now)
            if since_date is None:
                ongoing = sum(s for _, s in segments)
            else:
                ongoing = sum(s for d, s in segments if d >= since_date)
            if ongoing > 0:
                totals[user_id] = totals.get(user_id, 0) + ongoing

        if not totals:
            return info_embed(f"🎙️ Ranking Czasu Głosowego — {label}", "Brak danych dla wybranego okresu.")

        top10 = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:10]

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for idx, (user_id, seconds) in enumerate(top10):
            prefix = medals.get(idx, f"**{idx + 1}.**")
            mention, _name = await self._resolve_name(guild, user_id)
            lines.append(f"{prefix} {mention} — **{_format_duration(seconds)}**")

        embed = info_embed(f"🎙️ Ranking Czasu Głosowego — {label}", "\n".join(lines))
        embed.set_footer(text=config.FOOTER_TEXT)
        return embed

    async def build_weekly_management_embed(self, guild: discord.Guild) -> discord.Embed:
        now = datetime.now(TIMEZONE)
        start_date_obj = now.date() - timedelta(days=6)
        start_date_str = start_date_obj.isoformat()
        date_range_str = f"{start_date_obj.strftime('%d.%m.%Y')} – {now.strftime('%d.%m.%Y')}"
        guild_id_str = str(guild.id)

        rows = await self.bot.db.fetchall(
            "SELECT user_id, SUM(seconds) AS total_seconds FROM voice_activity WHERE guild_id = %s AND date >= %s GROUP BY user_id",
            (guild_id_str, start_date_str)
        )
        totals: dict[int, int] = {int(row["user_id"]): row["total_seconds"] or 0 for row in rows}

        for (g_id, user_id), joined_at in self._voice_join_times.items():
            if g_id != guild.id:
                continue
            segments = _split_by_day(joined_at, now)
            ongoing = sum(s for d, s in segments if d >= start_date_str)
            if ongoing > 0:
                totals[user_id] = totals.get(user_id, 0) + ongoing

        totals = {uid: sec for uid, sec in totals.items() if sec > 0}

        if not totals:
            embed = create_embed(
                title="📊 Tygodniowy Raport Aktywności — Zarząd Organizacji",
                description=f"📅 **Okres rozliczeniowy:** {date_range_str}\n\n*Brak zarejestrowanej aktywności głosowej w minionym tygodniu.*",
                color=0xF1C40F
            )
            embed.set_footer(text=config.FOOTER_TEXT)
            return embed

        top_members = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:15]
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for idx, (user_id, seconds) in enumerate(top_members):
            prefix = medals.get(idx, f"`#{idx + 1:02d}`")
            mention, text_name = await self._resolve_name(guild, user_id)
            lines.append(f"{prefix} {mention} — **{_format_duration(seconds)}** *({text_name})*")

        fields = [
            {
                "name": f"🏆 Ranking Czasu Głosowego (TOP {len(top_members)})",
                "value": "\n".join(lines),
                "inline": False
            }
        ]

        embed = create_embed(
            title="📊 Tygodniowy Raport Aktywności — Zarząd Organizacji",
            description=f"📅 **Okres rozliczeniowy:** {date_range_str}",
            color=0xF1C40F,
            fields=fields
        )
        embed.set_footer(text=config.FOOTER_TEXT)
        return embed

    @app_commands.command(name="stats", description="Ranking TOP 10 użytkowników wg czasu spędzonego na kanale głosowym.")
    async def stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        default_scope = "day"
        embed = await self.build_stats_embed(interaction.guild, default_scope)
        view = StatsView(self, default_scope)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActivityCog(bot))
