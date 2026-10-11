"""
Moduł śledzenia czasu spędzonego na kanałach głosowych.
Zlicza czas per dzień i udostępnia komendę /stats z interaktywnym rankingiem TOP 10 —
wybór okresu (Dzień / Tydzień / Miesiąc / Cały czas) odbywa się przyciskami pod embedem.
Dodatkowo generuje codzienny raport o 23:59 oraz cotygodniowy raport dla Zarządu Organizacji
wysyłany w każdą środę o godzinie 23:59 na dedykowany kanał ukryty.
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

# Strefa czasowa, wg której liczone są "dni" (00:00-24:00) niezależnie od tego,
# w jakiej strefie/UTC działa serwer hostujący bota. Bez tego dzień w bazie
# przełączał się o godzinie systemowej serwera (np. 4:00 zamiast 0:00 czasu PL).
TIMEZONE = ZoneInfo("Europe/Warsaw")

# Godzina wysyłki automatycznego raportu dziennego i tygodniowego (czas w strefie TIMEZONE)
DAILY_REPORT_TIME = dtime(hour=23, minute=59, tzinfo=TIMEZONE)

# Godzina resetu dnia / wymuszonego zapisu trwających sesji (czas w strefie TIMEZONE)
MIDNIGHT_RESET_TIME = dtime(hour=0, minute=0, tzinfo=TIMEZONE)

# Kolejność, etykiety przycisków, tytuły w embedzie oraz liczba dni wstecz dla każdego zakresu
SCOPE_ORDER = ["day", "week", "month", "all"]
SCOPE_LABELS = {"day": "Dzień", "week": "Tydzień", "month": "Miesiąc", "all": "Cały czas"}
SCOPE_TITLES = {"day": "Dzisiaj", "week": "Ostatnie 7 dni", "month": "Ostatnie 30 dni", "all": "Cały czas śledzenia"}
SCOPE_DAYS_BACK = {"day": 0, "week": 6, "month": 29, "all": None}


def _format_duration(seconds: int) -> str:
    """Formatuje czas w sekundach do czytelnej postaci, np. 12h 34m."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _clean_name(name: str) -> str:
    """Escapuje markdown w nicku, żeby np. '_' czy '*' nie psuły formatowania embeda."""
    return discord.utils.escape_markdown(name)


def _split_by_day(start: datetime, end: datetime) -> list[tuple[str, int]]:
    """Dzieli przedział czasu na segmenty per dzień (na wypadek sesji przechodzącej przez północ).
    Granica dnia to północ w strefie TIMEZONE, niezależnie od strefy wejściowych datetime."""
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
    """Pojedynczy przycisk wyboru okresu rankingu — podświetla się na zielono, gdy jest aktywny."""

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
    """Rząd przycisków pod embedem rankingu, umożliwiający przełączanie okresu."""

    def __init__(self, cog: "ActivityCog", active_scope: str) -> None:
        super().__init__(timeout=None)
        for scope in SCOPE_ORDER:
            self.add_item(ScopeButton(cog, scope, active=(scope == active_scope)))


class ActivityCog(commands.Cog):
    """Śledzi czas spędzony na kanałach głosowych i udostępnia interaktywny ranking TOP 10 (/stats)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # (guild_id, user_id) -> moment dołączenia do kanału głosowego (sesje jeszcze niezapisane do bazy)
        self._voice_join_times: dict[tuple[int, int], datetime] = {}

    async def _remember_name(self, guild_id: int, user_id: int, display_name: str) -> None:
        """Zapamiętuje ostatnio znany nick użytkownika w bazie. Dzięki temu, gdy ktoś
        opuści serwer (lub wypadnie z cache Discorda) i jego oznaczenie @ przestanie się
        renderować, w raportach nadal można pokazać jego prawdziwy nick zamiast gołego ID."""
        try:
            await self.bot.db.execute(
                """
                INSERT INTO member_names (guild_id, user_id, display_name)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET display_name = excluded.display_name
                """,
                (guild_id, user_id, display_name)
            )
        except Exception as e:
            logger.debug(f"Nie udało się zapisać nicku {user_id} dla serwera {guild_id}: {e}")

    async def _resolve_name(self, guild: discord.Guild, user_id: int) -> tuple[str, str]:
        """Zwraca (mention, nazwa_tekstowa) dla danego użytkownika.
        Jeśli member jest w cache - normalny mention + jego aktualny nick (przy okazji
        zapisywany do bazy jako 'ostatnio znany'). Jeśli NIE ma go w cache (np. wyszedł
        z serwera) - mention i tak wyjdzie jako gołe <@id> (Discord tego nie naprawi),
        ale nazwę tekstową bierzemy z bazy (ostatni znany nick) zamiast bezużytecznego ID."""
        member = guild.get_member(user_id)
        if member:
            name = _clean_name(member.display_name)
            await self._remember_name(guild.id, user_id, name)
            return member.mention, name

        mention = f"<@{user_id}>"
        try:
            row = await self.bot.db.fetchone(
                "SELECT display_name FROM member_names WHERE guild_id = ? AND user_id = ?",
                (guild.id, user_id)
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

        # Czy dany stan liczy się jako "obecny i śledzony" - czyli na kanale głosowym,
        # ale NIE na kanale AFK (czas spędzony na AFK nie powinien się naliczać).
        was_tracked = before.channel is not None and before.channel != afk_channel
        should_track = after.channel is not None and after.channel != afk_channel

        if was_tracked and not should_track:
            # Zakończenie śledzonej sesji: całkowite opuszczenie kanału głosowego
            # albo przejście (ręczne lub automatyczne po czasie bezczynności) na kanał AFK.
            joined_at = self._voice_join_times.pop(key, None)
            if joined_at is None:
                return
            await self._save_voice_time(member.guild.id, member.id, joined_at, datetime.now(TIMEZONE))
        elif not was_tracked and should_track:
            # Start nowej śledzonej sesji: dołączenie z braku kanału albo powrót
            # z kanału AFK na "normalny" kanał głosowy.
            self._voice_join_times[key] = datetime.now(TIMEZONE)
        # else: brak zmiany stanu śledzenia (np. zmiana między dwoma zwykłymi kanałami
        # - sesja trwa dalej bez przerywania, albo osoba była i nadal jest na AFK).

    async def _save_voice_time(self, guild_id: int, user_id: int, start: datetime, end: datetime) -> None:
        if end <= start:
            return
        for day, seconds in _split_by_day(start, end):
            if seconds <= 0:
                continue
            await self.bot.db.execute(
                """
                INSERT INTO voice_activity (guild_id, user_id, date, seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, date) DO UPDATE SET seconds = seconds + excluded.seconds
                """,
                (guild_id, user_id, day, seconds)
            )

    async def _flush_all_sessions(self) -> None:
        """Zapisuje do bazy dotychczasowy czas WSZYSTKICH trwających sesji głosowych
        i resetuje ich punkt startowy na 'teraz'. Wspólna logika używana zarówno przez
        cykliczny flush co 5 minut, jak i przez wymuszony flush dokładnie o północy
        (patrz midnight_reset) - w obu przypadkach _save_voice_time + _split_by_day
        i tak poprawnie rozdzielą sekundy między dni kalendarzowe; różnica jest tylko
        w tym, JAK CZĘSTO/KIEDY ten zapis faktycznie trafia do bazy."""
        now = datetime.now(TIMEZONE)
        for key, joined_at in list(self._voice_join_times.items()):
            guild_id, user_id = key
            await self._save_voice_time(guild_id, user_id, joined_at, now)
            self._voice_join_times[key] = now

    async def cog_unload(self) -> None:
        """Zapisuje niedokończone sesje głosowe i zatrzymuje harmonogram przy wyłączeniu/przeładowaniu bota."""
        self.daily_report.cancel()
        self.weekly_report.cancel()
        self.flush_voice_sessions.cancel()
        self.midnight_reset.cancel()
        now = datetime.now(TIMEZONE)
        for (guild_id, user_id), joined_at in list(self._voice_join_times.items()):
            await self._save_voice_time(guild_id, user_id, joined_at, now)
        self._voice_join_times.clear()

    async def cog_load(self) -> None:
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS member_names (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self.daily_report.start()
        self.weekly_report.start()
        self.flush_voice_sessions.start()
        self.midnight_reset.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        Odtwarza stan osób AKTUALNIE obecnych na kanałach głosowych po (re)połączeniu z Discordem.
        Bez tego, sesje osób, które siedziały na kanale już przed startem/restartem bota,
        nigdy nie zostałyby policzone — Discord nie wysyła ponownie zdarzenia 'dołączył
        do kanału' dla osób już tam obecnych. To główna przyczyna niespójnego liczenia.
        """
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

    @tasks.loop(minutes=5)
    async def flush_voice_sessions(self) -> None:
        """
        Co 5 minut zapisuje dotychczasowy czas trwających sesji głosowych do bazy
        i resetuje punkt startowy na 'teraz'. Dzięki temu nagłe zamknięcie procesu
        (crash, restart hosta bez Graceful Shutdown, awaria) traci maksymalnie
        kilka minut danych zamiast całej trwającej sesji.
        """
        await self._flush_all_sessions()

    @flush_voice_sessions.before_loop
    async def before_flush_voice_sessions(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=MIDNIGHT_RESET_TIME)
    async def midnight_reset(self) -> None:
        """
        Wymusza NATYCHMIASTOWY zapis wszystkich trwających sesji głosowych dokładnie
        o północy. Sam podział sekund między dni kalendarzowe działa już poprawnie
        przy KAŻDYM zapisie (patrz _split_by_day).
        """
        await self._flush_all_sessions()

    @midnight_reset.before_loop
    async def before_midnight_reset(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=DAILY_REPORT_TIME)
    async def daily_report(self) -> None:
        """Wysyła ranking czasu głosowego z bieżącego dnia na skonfigurowany kanał, dla każdego serwera."""
        for guild in self.bot.guilds:
            try:
                row = await self.bot.db.fetchone(
                    "SELECT stats_channel_id FROM guild_config WHERE guild_id = ?",
                    (guild.id,)
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
        """Co tydzień w środę o 23:59 wysyła podsumowanie tygodniowe dla zarządu organizacji."""
        now = datetime.now(TIMEZONE)
        # 0=Poniedziałek, 1=Wtorek, 2=Środa, 3=Czwartek, 4=Piątek, 5=Sobota, 6=Niedziela
        if now.weekday() != 2:
            return

        for guild in self.bot.guilds:
            try:
                row = await self.bot.db.fetchone(
                    "SELECT weekly_stats_channel_id FROM guild_config WHERE guild_id = ?",
                    (guild.id,)
                )
                if not row or not row["weekly_stats_channel_id"]:
                    continue

                channel = guild.get_channel(row["weekly_stats_channel_id"])
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue

                embed = await self.build_weekly_management_embed(guild)
                await channel.send(embed=embed)
                logger.info(f"Wysłano tygodniowy raport aktywności dla zarządu na serwerze {guild.name} ({guild.id}).")
            except Exception as e:
                logger.error(f"Błąd wysyłania tygodniowego raportu aktywności dla zarządu (serwer {guild.id}): {e}", exc_info=True)

    @weekly_report.before_loop
    async def before_weekly_report(self) -> None:
        await self.bot.wait_until_ready()

    async def build_stats_embed(self, guild: discord.Guild, scope: str) -> discord.Embed:
        """Buduje embed rankingu TOP 10 dla wskazanego zakresu czasu."""
        label = SCOPE_TITLES[scope]
        days_back = SCOPE_DAYS_BACK[scope]
        guild_id = guild.id

        if days_back is None:
            rows = await self.bot.db.fetchall(
                "SELECT user_id, SUM(seconds) AS total_seconds FROM voice_activity WHERE guild_id = ? GROUP BY user_id",
                (guild_id,)
            )
            since_date = None
        else:
            since_date = (datetime.now(TIMEZONE).date() - timedelta(days=days_back)).isoformat()
            rows = await self.bot.db.fetchall(
                "SELECT user_id, SUM(seconds) AS total_seconds FROM voice_activity WHERE guild_id = ? AND date >= ? GROUP BY user_id",
                (guild_id, since_date)
            )

        totals: dict[int, int] = {row["user_id"]: row["total_seconds"] or 0 for row in rows}

        # Doliczenie aktualnie trwających (jeszcze niezapisanych w bazie) sesji głosowych
        now = datetime.now(TIMEZONE)
        for (g_id, user_id), joined_at in self._voice_join_times.items():
            if g_id != guild_id:
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
        """Buduje rozszerzony embed podsumowania tygodniowego dla Zarządu Organizacji."""
        now = datetime.now(TIMEZONE)
        start_date_obj = now.date() - timedelta(days=6)
        start_date_str = start_date_obj.isoformat()
        date_range_str = f"{start_date_obj.strftime('%d.%m.%Y')} – {now.strftime('%d.%m.%Y')}"
        guild_id = guild.id

        rows = await self.bot.db.fetchall(
            "SELECT user_id, SUM(seconds) AS total_seconds FROM voice_activity WHERE guild_id = ? AND date >= ? GROUP BY user_id",
            (guild_id, start_date_str)
        )
        totals: dict[int, int] = {row["user_id"]: row["total_seconds"] or 0 for row in rows}

        # Doliczenie aktualnie trwających (jeszcze niezapisanych w bazie) sesji głosowych
        for (g_id, user_id), joined_at in self._voice_join_times.items():
            if g_id != guild_id:
                continue
            segments = _split_by_day(joined_at, now)
            ongoing = sum(s for d, s in segments if d >= start_date_str)
            if ongoing > 0:
                totals[user_id] = totals.get(user_id, 0) + ongoing

        # Odfiltrowanie zerowych czasów
        totals = {uid: sec for uid, sec in totals.items() if sec > 0}

        if not totals:
            embed = create_embed(
                title="📊 Tygodniowy Raport Aktywności — Zarząd Organizacji",
                description=f"📅 **Okres rozliczeniowy:** {date_range_str}\n\n*Brak zarejestrowanej aktywności głosowej w minionym tygodniu.*",
                color=0xF1C40F
            )
            embed.set_footer(text=config.FOOTER_TEXT)
            return embed

        total_org_seconds = sum(totals.values())
        active_members_count = len(totals)
        avg_seconds = total_org_seconds // active_members_count if active_members_count else 0

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

    @app_commands.command(
        name="ustaw_kanal_zarzadu",
        description="Ustawia ukryty kanał zarządu, na który co środę o 23:59 trafia tygodniowy raport aktywności."
    )
    @app_commands.describe(kanal="Wybierz kanał tekstowy dla Zarządu")
    @is_administrator()
    async def ustaw_kanal_zarzadu(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        permissions = kanal.permissions_for(interaction.guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            await interaction.response.send_message(
                embed=error_embed(
                    "Brak Uprawnień",
                    f"Bot nie ma uprawnień do wysyłania wiadomości lub dołączania embedów na kanale {kanal.mention}. Upewnij się, że bot ma uprawnienia `Wysyłanie wiadomości` oraz `Dołączanie linków` na tym kanale."
                ),
                ephemeral=True
            )
            return

        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, weekly_stats_channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET weekly_stats_channel_id = ?
            """,
            (interaction.guild_id, kanal.id, kanal.id)
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Ustawiono Kanał Zarządu",
                f"Kanał {kanal.mention} został pomyślnie ustawiony jako kanał raportów dla Zarządu.\n"
                f"📊 Podsumowanie tygodniowej aktywności będzie pojawiać się tutaj **w każdą środę o godzinie 23:59**."
            )
        )

    @app_commands.command(
        name="stats_zarzad_test",
        description="[Admin] Wysyła próbny tygodniowy raport statystyk na skonfigurowany kanał zarządu."
    )
    @is_administrator()
    async def stats_zarzad_test(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        row = await self.bot.db.fetchone(
            "SELECT weekly_stats_channel_id FROM guild_config WHERE guild_id = ?",
            (interaction.guild_id,)
        )
        if not row or not row["weekly_stats_channel_id"]:
            await interaction.followup.send(
                embed=error_embed("Kanał Nieustawiony", "Najpierw ustaw kanał zarządu komendą `/ustaw_kanal_zarzadu`."),
                ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(row["weekly_stats_channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                embed=error_embed("Kanał Nie Istnieje", "Skonfigurowany kanał zarządu nie istnieje lub bot nie ma do niego dostępu."),
                ephemeral=True
            )
            return

        embed = await self.build_weekly_management_embed(interaction.guild)
        try:
            await channel.send(embed=embed)
            await interaction.followup.send(
                embed=success_embed("Wysłano Raport Testowy", f"Próbny raport tygodniowy został pomyślnie wysłany na {channel.mention}."),
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                embed=error_embed("Błąd Wysyłania", f"Nie udało się wysłać wiadomości: `{e}`"),
                ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActivityCog(bot))