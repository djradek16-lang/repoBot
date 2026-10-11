"""
Moduł Panelu Członka i Statystyk Stażu All-Time (MemberPanelCog).

Profesjonalny, minimalistyczny interfejs karty członkowskiej:
- Saper jako bazowa rola początkowa organizacji.
- Elegancka, stonowana estetyka (Discord Dark Graphite / Minimalist Slate).
- Spójne wskaźniki all-time (czas spędzony na VC, wysłane wiadomości, staż).
- Dynamiczny, minimalistyczny pasek postępu do kolejnego stopnia.
- Przejrzyste zakładki: Karta, Drabinka rang, Szczegóły aktywności, Podium.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Any

import discord
from discord import app_commands
from discord.ext import commands

from services.rank_service import RankService
from utils.embeds import create_embed, info_embed
from utils.helpers import format_number
import config

logger = logging.getLogger("AegisBot.Cogs.MemberPanel")

STRIKE_LIMIT = 3
PANEL_COLOR = 0x2B2D31  # Elegancki, ciemny grafit Discorda
ACCENT_COLOR = 0x5865F2  # Blurple akcent


def _format_time_short(seconds: int) -> str:
    """Zwięzłe formatowanie czasu: '142h 20m' lub '5d 12h'."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return f"{seconds}s"


def _format_time_full(seconds: int) -> str:
    """Precyzyjne formatowanie czasu z dniami: '5d 14h 23m'."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts) if parts else f"{seconds}s"


def _format_seniority(days: int) -> str:
    """Minimalistyczny opis stażu: '142 dni (4 mies. 22 dni)'."""
    if days <= 0:
        return "0 dni (dołączył dzisiaj)"
    years, rem_days = divmod(days, 365)
    months, rem_days = divmod(rem_days, 30)
    parts = []
    if years:
        parts.append(f"{years}r")
    if months:
        parts.append(f"{months}m")
    if rem_days or not parts:
        parts.append(f"{rem_days}d")
    return f"**{days}** dni ({' '.join(parts)})"


def _sleek_progress_bar(current: int, total: int, length: int = 10) -> tuple[str, int]:
    """
    Minimalistyczny pasek postępu z użyciem znaków ▰ i ▱.
    Zwraca: (pasek, procent)
    """
    if total <= 0:
        return f"{'▰' * length}", 100
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(round(ratio * length))
    empty = length - filled
    percent = int(round(ratio * 100))
    bar = f"{'▰' * filled}{'▱' * empty}"
    return bar, percent


def _plusy_suffix(n: int) -> str:
    n = abs(n)
    if n == 1:
        return "plus"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "plusy"
    return "plusów"


class MemberPanelView(discord.ui.View):
    """Minimalistyczne przyciski przełączania zakładek panelu."""

    def __init__(self, cog: "MemberPanelCog", member: discord.Member, author: discord.abc.User) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.member = member
        self.author = author
        self.active_tab = "profile"

    @discord.ui.button(label="Karta", emoji="👤", style=discord.ButtonStyle.primary, row=0)
    async def tab_profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.active_tab = "profile"
        self._update_styles()
        embed = await self.cog.build_profile_embed(self.member)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Drabinka", emoji="🎖️", style=discord.ButtonStyle.secondary, row=0)
    async def tab_ladder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.active_tab = "ladder"
        self._update_styles()
        embed = await self.cog.build_ladder_embed(self.member.guild, highlight_member=self.member)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Statystyki", emoji="📊", style=discord.ButtonStyle.secondary, row=0)
    async def tab_stats(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.active_tab = "stats"
        self._update_styles()
        embed = await self.cog.build_detailed_stats_embed(self.member)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Podium", emoji="🏆", style=discord.ButtonStyle.secondary, row=0)
    async def tab_leaderboards(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.active_tab = "leaderboards"
        self._update_styles()
        embed = await self.cog.build_mini_leaderboards_embed(self.member.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.active_tab == "profile":
            embed = await self.cog.build_profile_embed(self.member)
        elif self.active_tab == "ladder":
            embed = await self.cog.build_ladder_embed(self.member.guild, highlight_member=self.member)
        elif self.active_tab == "stats":
            embed = await self.cog.build_detailed_stats_embed(self.member)
        else:
            embed = await self.cog.build_mini_leaderboards_embed(self.member.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    def _update_styles(self) -> None:
        self.tab_profile.style = discord.ButtonStyle.primary if self.active_tab == "profile" else discord.ButtonStyle.secondary
        self.tab_ladder.style = discord.ButtonStyle.primary if self.active_tab == "ladder" else discord.ButtonStyle.secondary
        self.tab_stats.style = discord.ButtonStyle.primary if self.active_tab == "stats" else discord.ButtonStyle.secondary
        self.tab_leaderboards.style = discord.ButtonStyle.primary if self.active_tab == "leaderboards" else discord.ButtonStyle.secondary


class MemberPanelCog(commands.Cog):
    """Minimalistyczny i profesjonalny panel członka organizacji."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.rank_service = RankService(bot.db)

    async def cog_load(self) -> None:
        await self.rank_service._ensure_tables()

    # -----------------------------------------------------------------------
    # Rozpoznawanie danych
    # -----------------------------------------------------------------------

    def get_saper_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        """Wyszukuje domyślną rolę bazową 'Saper' na serwerze."""
        return discord.utils.find(lambda r: r.name.strip().lower() == "saper", guild.roles)

    async def get_live_voice_seconds(self, guild_id: int, user_id: int) -> int:
        """Zwraca całkowity czas na VC all-time łącznie z trwającą sesją."""
        base = await self.rank_service.get_alltime_voice_seconds(guild_id, user_id)
        activity_cog = self.bot.get_cog("ActivityCog")
        if activity_cog and hasattr(activity_cog, "_voice_join_times"):
            key = (guild_id, user_id)
            if key in activity_cog._voice_join_times:
                join_time = activity_cog._voice_join_times[key]
                live_sec = int((datetime.now(timezone.utc) - join_time.astimezone(timezone.utc)).total_seconds())
                base += max(0, live_sec)
        return base

    async def get_member_rankings(self, guild: discord.Guild, member: discord.Member) -> dict[str, Any]:
        """Oblicza pozycje członka w rankingach organizacji."""
        guild_id = guild.id
        user_id = member.id

        # Staż
        sorted_members = sorted(
            [m for m in guild.members if not m.bot and m.joined_at is not None],
            key=lambda m: m.joined_at
        )
        seniority_rank = next((i + 1 for i, m in enumerate(sorted_members) if m.id == user_id), None)

        # Czas na VC
        vc_rows = await self.bot.db.fetchall(
            "SELECT user_id, SUM(seconds) as total_sec FROM voice_activity WHERE guild_id = ? GROUP BY user_id ORDER BY total_sec DESC",
            (guild_id,)
        )
        vc_rank = next((i + 1 for i, r in enumerate(vc_rows) if r["user_id"] == user_id), None)

        # Wiadomości
        msg_rows = await self.bot.db.fetchall(
            "SELECT user_id, SUM(count) as total_msg FROM message_activity WHERE guild_id = ? GROUP BY user_id ORDER BY total_msg DESC",
            (guild_id,)
        )
        msg_rank = next((i + 1 for i, r in enumerate(msg_rows) if r["user_id"] == user_id), None)

        return {
            "seniority_rank": seniority_rank,
            "seniority_total": len(sorted_members),
            "vc_rank": vc_rank,
            "vc_total": len(vc_rows),
            "msg_rank": msg_rank,
            "msg_total": len(msg_rows),
        }

    async def get_mta_identity(self, guild_id: int, user_id: int) -> tuple[str | None, str | None]:
        """Pobiera zarejestrowany nick i login w MTA."""
        row = await self.bot.db.fetchone(
            "SELECT nick, login FROM donations WHERE guild_id = ? AND user_id = ? AND nick IS NOT NULL AND nick != '' ORDER BY created_at DESC LIMIT 1",
            (guild_id, user_id)
        )
        if row and row["nick"]:
            return row["nick"], row["login"]

        row2 = await self.bot.db.fetchone(
            "SELECT nick, login FROM recruit_entries WHERE guild_id = ? AND (added_by = ? OR uid = ?) ORDER BY entry_id DESC LIMIT 1",
            (guild_id, user_id, str(user_id))
        )
        if row2 and row2["nick"]:
            return row2["nick"], row2["login"]

        return None, None

    # -----------------------------------------------------------------------
    # Minimalistyczne Embedy
    # -----------------------------------------------------------------------

    async def build_profile_embed(self, member: discord.Member) -> discord.Embed:
        """Buduje czystą, minimalistyczną kartę członka organizacji."""
        guild = member.guild
        guild_id = guild.id
        user_id = member.id

        # Staż
        joined_at = member.joined_at or datetime.now(timezone.utc)
        days_in_org = (datetime.now(timezone.utc) - joined_at).days
        joined_str = joined_at.strftime("%d.%m.%Y")

        # Ranga i plusy (z domyślnym Saperem)
        points = await self.rank_service.get_points(guild_id, user_id)
        ladder = await self.rank_service.get_ladder(guild_id)
        current = RankService.current_rank(ladder, points)
        following = RankService.next_rank(ladder, points)
        saper_role = self.get_saper_role(guild)

        if current:
            rank_name = current["rank_name"]
            role_obj = guild.get_role(current["role_id"])
            rank_display = role_obj.mention if role_obj else f"**{rank_name}**"
        else:
            rank_name = "Saper"
            rank_display = saper_role.mention if saper_role else "**Saper**"

        # Pasek postępu do awansu
        if following:
            prev_threshold = current["threshold"] if current else 0
            needed_from_prev = following["threshold"] - prev_threshold
            earned_from_prev = points - prev_threshold
            missing = following["threshold"] - points
            bar, percent = _sleek_progress_bar(earned_from_prev, needed_from_prev, length=10)
            next_role = guild.get_role(following["role_id"])
            next_name = next_role.mention if next_role else f"**{following['rank_name']}**"
            progress_line = f"`{bar}` **{percent}%** • Awans: {next_name} *(brakuje +{missing})*"
        else:
            if ladder:
                progress_line = "`▰▰▰▰▰▰▰▰▰▰` **100%** • Najwyższy stopień osiągnięty"
            else:
                progress_line = "*Brak skonfigurowanych kolejnych stopni w drabince*"

        # Aktywność All-Time
        voice_seconds = await self.get_live_voice_seconds(guild_id, user_id)
        messages_count = await self.rank_service.get_alltime_messages(guild_id, user_id)
        strikes = await self.rank_service.get_strike_count(guild_id, user_id)
        rankings = await self.get_member_rankings(guild, member)

        staz_rank_str = f"#{rankings['seniority_rank']}" if rankings["seniority_rank"] else "-"
        vc_rank_str = f"#{rankings['vc_rank']}" if rankings["vc_rank"] else "-"
        msg_rank_str = f"#{rankings['msg_rank']}" if rankings["msg_rank"] else "-"

        # MTA
        mta_nick, mta_login = await self.get_mta_identity(guild_id, user_id)
        mta_str = f"`{mta_nick}`" if mta_nick else "*Brak*"

        # Status VC na żywo
        vc_active_badge = ""
        if member.voice and member.voice.channel:
            vc_active_badge = f" • 🟢 {member.voice.channel.name}"

        description = f"{member.mention} • {rank_display}{vc_active_badge}"

        # Czysty, 3-kolumnowy minimalistyczny układ
        fields = [
            {
                "name": "Staż",
                "value": f"{_format_seniority(days_in_org)}\n`Od {joined_str}` • Top {staz_rank_str}",
                "inline": True
            },
            {
                "name": "Ranga i Plusy",
                "value": f"{rank_display}\n**+{points}** {_plusy_suffix(points)} • Minusy: `{strikes}/{STRIKE_LIMIT}`",
                "inline": True
            },
            {
                "name": "Tożsamość MTA",
                "value": f"{mta_str}\nLogin: `{mta_login or '-'}`",
                "inline": True
            },
            {
                "name": "Postęp Awansu",
                "value": progress_line,
                "inline": False
            },
            {
                "name": "Głosowy (All-Time)",
                "value": f"**{_format_time_short(voice_seconds)}**\n({_format_time_full(voice_seconds)}) • Top {vc_rank_str}",
                "inline": True
            },
            {
                "name": "Wiadomości (All-Time)",
                "value": f"**{format_number(messages_count)}** msgs\nTop {msg_rank_str}",
                "inline": True
            },
            {
                "name": "Status Kar",
                "value": f"`{strikes} / {STRIKE_LIMIT}` minusów" + (" ⚠️" if strikes >= 2 else " *(Czysto)*" if strikes == 0 else ""),
                "inline": True
            }
        ]

        embed = discord.Embed(
            title=f"Karta Członka • {member.display_name}",
            description=description,
            color=PANEL_COLOR
        )
        for f in fields:
            embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Ewidencja Stażu Team77 • ID: {member.id}")
        return embed

    async def build_ladder_embed(self, guild: discord.Guild, highlight_member: Optional[discord.Member] = None) -> discord.Embed:
        """Minimalistyczna drabinka rang z Saperem jako stopniem zerowym."""
        ladder = await self.rank_service.get_ladder(guild.id)
        saper_role = self.get_saper_role(guild)
        saper_mention = saper_role.mention if saper_role else "@Saper"
        saper_count = len(saper_role.members) if saper_role else 0

        user_points = None
        if highlight_member:
            user_points = await self.rank_service.get_points(guild.id, highlight_member.id)

        lines = []
        is_user_saper = False
        if highlight_member and user_points is not None:
            curr_rank = RankService.current_rank(ladder, user_points)
            is_user_saper = curr_rank is None

        # Bazowa rola Saper
        saper_marker = "● " if is_user_saper else "○ "
        saper_tag = " `[TWOJA RANGA]`" if is_user_saper else ""
        lines.append(f"{saper_marker}**Saper** — {saper_mention}{saper_tag}\n   `Wymóg: 0 plusów` • Osób: **{saper_count}**")

        for r in ladder:
            role = guild.get_role(r["role_id"])
            role_mention = role.mention if role else f"@{r['rank_name']}"
            count = len(role.members) if role else 0

            is_current = False
            if highlight_member and user_points is not None:
                curr_rank = RankService.current_rank(ladder, user_points)
                if curr_rank and curr_rank["rank_name"] == r["rank_name"]:
                    is_current = True

            marker = "● " if is_current else "○ "
            badge = " `[TWOJA RANGA]`" if is_current else ""
            lines.append(
                f"{marker}**{r['rank_name']}** — {role_mention}{badge}\n"
                f"   `Wymóg: +{r['threshold']}` • Osób: **{count}**"
            )

        embed = discord.Embed(
            title="Drabinka Rang Organizacji",
            description="Hierarchia stopni wojskowych. Awans następuje po uzyskaniu odpowiedniej liczby plusów.\n\n" + "\n\n".join(lines),
            color=PANEL_COLOR
        )
        embed.set_footer(text="Team77 • System Stopni Organizacyjnych")
        return embed

    async def build_detailed_stats_embed(self, member: discord.Member) -> discord.Embed:
        """Minimalistyczne szczegóły aktywności all-time."""
        guild = member.guild
        guild_id = guild.id
        user_id = member.id

        voice_seconds = await self.get_live_voice_seconds(guild_id, user_id)
        messages_count = await self.rank_service.get_alltime_messages(guild_id, user_id)
        days_in_org = max(1, (datetime.now(timezone.utc) - (member.joined_at or datetime.now(timezone.utc))).days)

        daily_vc_sec = voice_seconds // days_in_org
        daily_msg = messages_count / days_in_org

        active_vc_days = await self.bot.db.fetchone(
            "SELECT COUNT(DISTINCT date) as cnt FROM voice_activity WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        best_vc_day = await self.bot.db.fetchone(
            "SELECT date, seconds FROM voice_activity WHERE guild_id = ? AND user_id = ? ORDER BY seconds DESC LIMIT 1",
            (guild_id, user_id)
        )

        vc_days_cnt = active_vc_days["cnt"] if active_vc_days else 0
        best_str = f"{best_vc_day['date']} ({_format_time_short(best_vc_day['seconds'])})" if best_vc_day else "-"

        fields = [
            {"name": "Całkowity czas VC", "value": f"**{_format_time_full(voice_seconds)}**", "inline": True},
            {"name": "Wysłane wiadomości", "value": f"**{messages_count:,}**", "inline": True},
            {"name": "Dni w organizacji", "value": f"**{days_in_org}**", "inline": True},
            {"name": "Średnia dzienna VC", "value": f"{_format_time_short(daily_vc_sec)} / dzień", "inline": True},
            {"name": "Średnia wiadomości", "value": f"{daily_msg:.1f} / dzień", "inline": True},
            {"name": "Aktywne dni na VC", "value": f"{vc_days_cnt} dni", "inline": True},
            {"name": "Rekordowy dzień na VC", "value": f"`{best_str}`", "inline": False},
        ]

        embed = discord.Embed(
            title=f"Statystyki All-Time • {member.display_name}",
            color=PANEL_COLOR
        )
        for f in fields:
            embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Team77 • Szczegółowa Analityka Członka")
        return embed

    async def build_mini_leaderboards_embed(self, guild: discord.Guild) -> discord.Embed:
        """Minimalistyczne podium TOP 5 w trzech kategoriach all-time."""
        guild_id = guild.id

        # Staż
        top_seniority = sorted(
            [m for m in guild.members if not m.bot and m.joined_at is not None],
            key=lambda m: m.joined_at
        )[:5]
        sen_lines = [f"`{i+1}.` {m.mention} — **{(datetime.now(timezone.utc) - m.joined_at).days}** dni" for i, m in enumerate(top_seniority)]

        # Głosowy
        top_vc = await self.bot.db.fetchall(
            "SELECT user_id, SUM(seconds) as total_sec FROM voice_activity WHERE guild_id = ? GROUP BY user_id ORDER BY total_sec DESC LIMIT 5",
            (guild_id,)
        )
        vc_lines = [f"`{i+1}.` <@{r['user_id']}> — **{_format_time_short(r['total_sec'])}**" for i, r in enumerate(top_vc)]

        # Wiadomości
        top_msg = await self.bot.db.fetchall(
            "SELECT user_id, SUM(count) as total_msg FROM message_activity WHERE guild_id = ? GROUP BY user_id ORDER BY total_msg DESC LIMIT 5",
            (guild_id,)
        )
        msg_lines = [f"`{i+1}.` <@{r['user_id']}> — **{format_number(r['total_msg'])}**" for i, r in enumerate(top_msg)]

        embed = discord.Embed(
            title="Podium Organizacji • Rekordy All-Time",
            color=PANEL_COLOR
        )
        embed.add_field(name="Najdłuższy Staż", value="\n".join(sen_lines) if sen_lines else "*Brak danych*", inline=False)
        embed.add_field(name="Czas na Kanałach Głosowych", value="\n".join(vc_lines) if vc_lines else "*Brak danych*", inline=False)
        embed.add_field(name="Wiadomości na Czacie", value="\n".join(msg_lines) if msg_lines else "*Brak danych*", inline=False)
        embed.set_footer(text="Team77 • Rankingi Historyczne All-Time")
        return embed

    # -----------------------------------------------------------------------
    # Komendy Slash
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="profil",
        description="Wyświetla minimalistyczny panel członka: staż, ranga (Saper+), czas VC i awans."
    )
    @app_commands.describe(uzytkownik="Wybierz członka organizacji (domyślnie Twój profil)")
    async def profil(self, interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None) -> None:
        target = uzytkownik or interaction.user
        if not isinstance(target, discord.Member):
            target = interaction.guild.get_member(target.id) if interaction.guild else None

        if not target:
            await interaction.response.send_message(
                embed=info_embed("Błąd", "Nie znaleziono wskazanego użytkownika na serwerze."),
                ephemeral=True
            )
            return

        embed = await self.build_profile_embed(target)
        view = MemberPanelView(self, target, interaction.user)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(
        name="czlonek",
        description="Alias do /profil — Karta członkowska, staż, plusy i ranga."
    )
    @app_commands.describe(uzytkownik="Wybierz członka organizacji (domyślnie Twój profil)")
    async def czlonek(self, interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None) -> None:
        await self.profil.callback(self, interaction, uzytkownik)

    @app_commands.command(
        name="top_staz",
        description="Ranking TOP 10 członków z najdłuższym stażem w organizacji (all-time)."
    )
    async def top_staz(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return

        members = sorted(
            [m for m in guild.members if not m.bot and m.joined_at is not None],
            key=lambda m: m.joined_at
        )[:10]

        if not members:
            await interaction.response.send_message(
                embed=info_embed("Ranking Stażu", "Brak danych o stażu członków."),
                ephemeral=True
            )
            return

        lines = []
        for i, m in enumerate(members):
            days = (datetime.now(timezone.utc) - m.joined_at).days
            joined_fmt = m.joined_at.strftime("%d.%m.%Y")
            lines.append(f"`#{i + 1:02d}` {m.mention} — **{days}** dni *(od {joined_fmt})*")

        embed = discord.Embed(
            title="Ranking Stażu w Organizacji",
            description="\n".join(lines),
            color=PANEL_COLOR
        )
        embed.set_footer(text="Team77 • Staż All-Time")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="top_glosowy",
        description="Ranking TOP 10 spędzonego czasu na kanałach głosowych (all-time)."
    )
    async def top_glosowy(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            """
            SELECT user_id, SUM(seconds) as total_sec FROM voice_activity
            WHERE guild_id = ?
            GROUP BY user_id
            HAVING total_sec > 0
            ORDER BY total_sec DESC
            LIMIT 10
            """,
            (interaction.guild_id,)
        )

        if not rows:
            await interaction.response.send_message(
                embed=info_embed("Ranking Głosowy", "Brak zarejestrowanej aktywności głosowej."),
                ephemeral=True
            )
            return

        lines = [f"`#{i + 1:02d}` <@{r['user_id']}> — **{_format_time_short(r['total_sec'])}**" for i, r in enumerate(rows)]

        embed = discord.Embed(
            title="Ranking Czasu na Głosowym (All-Time)",
            description="\n".join(lines),
            color=PANEL_COLOR
        )
        embed.set_footer(text="Team77 • Aktywność Głosowa")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="top_wiadomosci",
        description="Ranking TOP 10 najbardziej aktywnych na czacie tekstowym (all-time)."
    )
    async def top_wiadomosci(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            """
            SELECT user_id, SUM(count) as total_msg FROM message_activity
            WHERE guild_id = ?
            GROUP BY user_id
            HAVING total_msg > 0
            ORDER BY total_msg DESC
            LIMIT 10
            """,
            (interaction.guild_id,)
        )

        if not rows:
            await interaction.response.send_message(
                embed=info_embed("Ranking Wiadomości", "Brak zarejestrowanych wiadomości."),
                ephemeral=True
            )
            return

        lines = [f"`#{i + 1:02d}` <@{r['user_id']}> — **{format_number(r['total_msg'])}** wiadomości" for i, r in enumerate(rows)]

        embed = discord.Embed(
            title="Ranking Wiadomości na Czacie (All-Time)",
            description="\n".join(lines),
            color=PANEL_COLOR
        )
        embed.set_footer(text="Team77 • Aktywność Tekstowa")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="drabinka_rang",
        description="Przegląd drabinki rang organizacji z uwzględnieniem roli bazowej Saper."
    )
    async def drabinka_rang(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        embed = await self.build_ladder_embed(interaction.guild, highlight_member=member)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberPanelCog(bot))