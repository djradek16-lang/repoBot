"""
Moduł audycji oraz Radia Project FM (ProjectRPG).

Zawiera:
- Pobieranie ramówki na żywo z API (ramowka.projectrpg.pl)
- Wyświetlanie karty audycji (/audycja) z automatycznym odświeżaniem i auto-odtworzeniem
- Rolę powiadomień (@Powiadomienia Audycje) oraz pingi, gdy prezenter Team77 wchodzi na antenę
- Odtwarzanie oficjalnego streamu audio Radia Project FM na kanałach głosowych (/radio, /projectfm)
  poprzez natywny klient Discord (FFmpeg + PCMVolumeTransformer) z panelem sterowania (RadioControlView)
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

import json
import os
import shutil
import aiohttp
import cloudscraper
import discord
from discord.ext import commands, tasks
from discord import app_commands

from utils.embeds import success_embed, error_embed, info_embed, create_embed
import config

logger = logging.getLogger("AegisBot.Cogs.Audycja")

RAMOWKA_API_BASE = "https://ramowka.projectrpg.pl/api/program"

# Karta /audycja ma DWA niezależne interwały:
# - AUDYCJA_FETCH_INTERVAL_SECONDS: jak często faktycznie odpytujemy stronę
#   ramowka.projectrpg.pl (żeby nie zarzucać jej zapytaniami).
# - AUDYCJA_EDIT_INTERVAL_SECONDS: jak często odświeżamy samą wiadomość na
#   Discordzie - między fetchami korzystamy z ostatnio pobranych danych.
AUDYCJA_FETCH_INTERVAL_SECONDS = 120       # 2 minuty (szybkie wykrywanie wejścia DJ-a na antenę)
AUDYCJA_EDIT_INTERVAL_SECONDS = 60        # 1 minuta
AUDYCJA_FAIL_THRESHOLD = 3                # ile kolejnych nieudanych edycji z rzędu = wiadomość uznana za "martwą"

# Zapamiętuje, w którym kanale ostatnio wywołano /audycja (per serwer), żeby po
# restarcie bota móc usunąć starą wiadomość i wkleić nową w tym samym miejscu.
AUDYCJA_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prpg_audycja_state.json")

_ERROR_MESSAGE_MAX_CHARS = 240
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _short_error(exc: BaseException) -> str:
    """Zamienia komunikat wyjątku na krótki, czytelny tekst do embeda."""
    text = str(exc)
    text = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    if len(text) > _ERROR_MESSAGE_MAX_CHARS:
        text = text[:_ERROR_MESSAGE_MAX_CHARS].rstrip() + "…"
    return text or exc.__class__.__name__


def _load_audycja_state() -> dict:
    try:
        with open(AUDYCJA_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"Nie udało się wczytać stanu audycji ProjectRPG: {e}")
        return {}


def _save_audycja_state(state: dict) -> None:
    try:
        with open(AUDYCJA_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning(f"Nie udało się zapisać stanu audycji ProjectRPG: {e}")


async def fetch_radio_stream_title(stream_url: Optional[str] = None) -> Optional[str]:
    """Odczytuje na żywo metadane ICY (StreamTitle) z bezpośredniego strumienia audio Radia Project FM."""
    url = stream_url or getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3")
    headers = {"Icy-MetaData": "1", "User-Agent": "AegisBot/2.0 RadioClient"}
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                metaint_hdr = resp.headers.get("icy-metaint")
                if not metaint_hdr:
                    return None
                metaint = int(metaint_hdr)
                await resp.content.readexactly(metaint)
                len_b = await resp.content.readexactly(1)
                meta_len = ord(len_b) * 16
                if meta_len <= 0:
                    return None
                data = await resp.content.readexactly(meta_len)
                text = data.decode("utf-8", errors="ignore")
                m = re.search(r"StreamTitle='([^']*)'", text)
                if m:
                    val = m.group(1).strip()
                    return val if val else None
    except Exception as e:
        logger.debug(f"Błąd odczytu ICY metadata ze strumienia radia: {e}")
    return None


def build_radio_embed(
    current_title: Optional[str] = None,
    current_show: Optional[dict] = None,
    requester: Optional[discord.abc.User] = None,
    volume: int = 100,
) -> discord.Embed:
    """Tworzy profesjonalny embed transmisji na żywo Radia Project FM."""
    title_str = current_title or "Transmisja Radia Project FM"
    presenter_str = "🎧 **Autopilot Radia Project FM** (Muzyka 24/7)"
    if current_show:
        user_info = current_show.get("user") or {}
        p_name = user_info.get("username") or "Prezenter"
        s_name = current_show.get("name") or "Audycja na żywo"
        presenter_str = f"🎙️ **{s_name}** — prowadzi **{p_name}**"

    fields = [
        {
            "name": "🎵 Aktualnie na antenie",
            "value": f"**{title_str}**",
            "inline": False,
        },
        {
            "name": "📻 Prowadzący & Audycja",
            "value": presenter_str,
            "inline": False,
        },
        {
            "name": "⚡ Status",
            "value": f"🔴 **NA ŻYWO** • Głośność: `{volume}%`",
            "inline": True,
        },
        {
            "name": "🌐 Oficjalne Odnośniki",
            "value": f"[Strona Główna]({getattr(config, 'RADIO_PROJECTFM_WEBSITE', 'https://projectrpg.pl/forum/114-radio-projectfm/')}) • [Ramówka](https://ramowka.projectrpg.pl/schedule)",
            "inline": True,
        },
    ]

    if requester:
        fields.append({
            "name": "🙋 Uruchomiono przez",
            "value": requester.mention,
            "inline": False,
        })

    embed = create_embed(
        title="📻 Radio Project FM",
        url=getattr(config, "RADIO_PROJECTFM_WEBSITE", "https://projectrpg.pl/forum/114-radio-projectfm/"),
        color=0xE02444,
        fields=fields,
        thumbnail_url=getattr(config, "RADIO_PROJECTFM_LOGO", "https://ramowka.projectrpg.pl/logo.png"),
    )
    embed.set_footer(
        text=f"{config.BOT_NAME} • Radio Project FM • Team77",
        icon_url=getattr(config, "RADIO_PROJECTFM_LOGO", "https://ramowka.projectrpg.pl/logo.png"),
    )
    return embed


class AudycjaControlView(discord.ui.View):
    """Trwały widok (persistent View) z przyciskiem do włączania/wyłączania powiadomień o audycjach oraz słuchania radia."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Powiadomienia o audycjach",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="audycja:toggle_notif",
    )
    async def toggle_notifications(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild = interaction.guild
        if not guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Ta opcja jest dostępna tylko na serwerze Discord.", ephemeral=True
            )
            return

        member: discord.Member = interaction.user
        role_name = "Powiadomienia Audycje"
        role = discord.utils.get(guild.roles, name=role_name)

        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name,
                    mentionable=True,
                    reason="Automatycznie utworzona rola do powiadomień o audycjach prezenterów organizacji",
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot nie posiada uprawnień do zarządzania rolami (`Zarządzanie rolami`), aby utworzyć rolę powiadomień.",
                    ephemeral=True,
                )
                return
            except Exception as e:
                logger.error(f"Nie udało się utworzyć roli powiadomień audycji na serwerze {guild.id}: {e}")
                await interaction.response.send_message(
                    "❌ Wystąpił błąd podczas tworzenia roli powiadomień.",
                    ephemeral=True,
                )
                return

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Użytkownik wyłączył powiadomienia o audycjach")
                await interaction.response.send_message(
                    "🔕 **Wyłączono powiadomienia o audycjach naszych prezenterów.**",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot nie ma uprawnień do odebrania roli (sprawdź hierarchię ról bota).",
                    ephemeral=True,
                )
        else:
            try:
                await member.add_roles(role, reason="Użytkownik włączył powiadomienia o audycjach")
                await interaction.response.send_message(
                    "🔔 **Włączono powiadomienia o audycjach naszych prezenterów!**\n"
                    "Otrzymasz wzmiankę, gdy prezenter z naszej organizacji wejdzie na żywo na antenę Radia Project FM.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot nie ma uprawnień do nadania roli (sprawdź hierarchię ról bota).",
                    ephemeral=True,
                )

    @discord.ui.button(
        label="Słuchaj Radia na VC",
        style=discord.ButtonStyle.success,
        emoji="📻",
        custom_id="audycja:listen_radio",
        row=0,
    )
    async def listen_radio_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        cog = (
            interaction.client.get_cog("AudycjaCog")
            or interaction.client.get_cog("MusicCog")
        )
        if not cog or not hasattr(cog, "start_radio_for_interaction"):
            await interaction.response.send_message("❌ Moduł audycji nie jest dostępny.", ephemeral=True)
            return
        await cog.start_radio_for_interaction(interaction)


class RadioControlView(discord.ui.View):
    """Trwały widok (persistent View) sterowania transmisją na żywo Radia Project FM."""

    def __init__(self, cog: Optional["Audycja"] = None):
        super().__init__(timeout=None)
        self.cog = cog

        # Linki oficjalne radia w rzędzie 1
        self.add_item(
            discord.ui.Button(
                label="Strona Radia",
                url=getattr(config, "RADIO_PROJECTFM_WEBSITE", "https://radio.projectrpg.pl"),
                emoji="🌐",
                row=1,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Ramówka Audycji",
                url="https://ramowka.projectrpg.pl",
                emoji="📅",
                row=1,
            )
        )

    def _get_cog(self, interaction: discord.Interaction) -> Optional["Audycja"]:
        return (
            self.cog
            or interaction.client.get_cog("AudycjaCog")
            or interaction.client.get_cog("MusicCog")
        )

    @discord.ui.button(
        label="Rozłącz",
        emoji="⏹️",
        style=discord.ButtonStyle.danger,
        custom_id="radio:stop",
        row=0,
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_connected():
            await interaction.response.send_message("Bot nie jest połączony z żadnym kanałem głosowym.", ephemeral=True)
            return

        user_ch = interaction.user.voice.channel if interaction.user.voice else None
        if user_ch != vc.channel:
            await interaction.response.send_message(
                f"Musisz być na kanale {vc.channel.mention}, aby zatrzymać radio.",
                ephemeral=True,
            )
            return

        cog = self._get_cog(interaction)
        if cog and hasattr(cog, "_cancel_radio_updater"):
            cog._cancel_radio_updater(interaction.guild_id)
        if cog and hasattr(cog, "_radio_messages"):
            old_radio = cog._radio_messages.pop(interaction.guild_id, None)
            if old_radio:
                try:
                    await old_radio.delete()
                except Exception:
                    pass

        if vc.is_playing():
            vc.stop()

        await interaction.response.send_message("⏹️ **Radio Project FM zostało wyłączone.**", ephemeral=True)
        await vc.disconnect()

    @discord.ui.button(
        label="Odśwież Tytuł",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="radio:refresh_title",
        row=0,
    )
    async def refresh_title_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        title = await fetch_radio_stream_title()
        cog = self._get_cog(interaction)
        cur = None
        if cog and hasattr(cog, "_get_schedule_shows"):
            _, cur, _, _ = cog._get_schedule_shows()

        vc = interaction.guild.voice_client if interaction.guild else None
        vol = 100
        if vc and hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
            vol = int(round(vc.source.volume * 100))

        new_embed = build_radio_embed(current_title=title, current_show=cur, requester=None, volume=vol)
        try:
            await interaction.message.edit(embed=new_embed)
            await interaction.followup.send(f"🔄 Zaktualizowano tytuł z anteny: **{title or 'Brak danych'}**", ephemeral=True)
        except Exception:
            await interaction.followup.send("Nie udało się zaktualizować wiadomości.", ephemeral=True)

    @discord.ui.button(
        label="Głośność",
        emoji="🔊",
        style=discord.ButtonStyle.primary,
        custom_id="radio:volume_cycle",
        row=0,
    )
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_connected():
            await interaction.response.send_message("Radio nie gra na żadnym kanale.", ephemeral=True)
            return

        if vc and hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
            curr_vol = int(round(vc.source.volume * 100))
            vols = [50, 75, 100, 125]
            next_vol = vols[(vols.index(curr_vol) + 1) % len(vols)] if curr_vol in vols else 100
            vc.source.volume = next_vol / 100.0
            await interaction.response.send_message(f"🔊 Głośność radia ustawiona na: **{next_vol}%**", ephemeral=True)
        else:
            await interaction.response.send_message("Głośność można regulować bezpośrednio na Discordzie (PPM na bota -> Głośność).", ephemeral=True)


class Audycja(commands.Cog, name="AudycjaCog"):
    """Moduł audycji oraz Radia Project FM (ProjectRPG)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ramówka ProjectRPG stoi za Cloudflare (strona "Just a moment..." / JS challenge)
        # cloudscraper rozwiązuje wyzwanie Cloudflare synchronicznie w osobnym wątku.
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        self._audycja_messages: dict[int, discord.Message] = {}
        self._audycja_fail_counts: dict[int, int] = {}
        self._audycja_restored = False
        self._cached_schedule_ok: bool = False
        self._cached_schedule: list[dict] = []
        self._last_audycja_fetch: Optional[datetime] = None
        self._last_announced_shows: dict[int, str] = {}
        self._radio_updater_tasks: dict[int, asyncio.Task] = {}
        self._radio_messages: dict[int, discord.Message] = {}
        logger.info("Moduł audycji i Radia Project FM załadowany.")

    async def cog_load(self) -> None:
        self.bot.add_view(AudycjaControlView())
        self.bot.add_view(RadioControlView(self))
        if self.bot.is_ready() and not self._audycja_restored:
            self._audycja_restored = True
            asyncio.create_task(self._restore_audycja_messages())

    async def cog_unload(self) -> None:
        self.scraper.close()
        self._refresh_audycja.cancel()
        for t in list(self._radio_updater_tasks.values()):
            if not t.done():
                t.cancel()
        self._radio_updater_tasks.clear()
        self._radio_messages.clear()

    def _find_audycja_channel(self, guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
        """Znajduje dedykowany kanał audycji:
        1. Zapisany w stanie /audycja (prpg_audycja_state.json),
        2. Lub kanał tekstowy o nazwie zawierającej 'audycj' albo 'radio',
        3. Lub zdefiniowany w config/env (opcjonalnie CHANNEL_AUDYCJA_ID)."""
        if not guild:
            return None

        state = _load_audycja_state()
        guild_state = state.get(str(guild.id))
        if guild_state and "channel_id" in guild_state:
            ch = guild.get_channel(int(guild_state["channel_id"]))
            if isinstance(ch, discord.TextChannel):
                return ch

        env_ch_id = getattr(config, "CHANNEL_AUDYCJA_ID", 0) or getattr(config, "AUDYCJA_CHANNEL_ID", 0)
        if env_ch_id:
            ch = guild.get_channel(int(env_ch_id))
            if isinstance(ch, discord.TextChannel):
                return ch

        for ch in guild.text_channels:
            name_lower = ch.name.lower()
            if "audycj" in name_lower or "radio" in name_lower:
                return ch

        return None

    def _cancel_radio_updater(self, guild_id: int) -> None:
        task = self._radio_updater_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _start_radio_updater(self, guild_id: int, message: discord.Message) -> None:
        self._cancel_radio_updater(guild_id)

        async def _updater():
            try:
                radio_url = getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3")
                last_title = None
                while True:
                    await asyncio.sleep(20)
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        break
                    vc = guild.voice_client
                    if not vc or not vc.is_connected() or not vc.is_playing():
                        break

                    vol = 100
                    if hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
                        vol = int(round(vc.source.volume * 100))

                    current_title = await fetch_radio_stream_title(radio_url)
                    _, cur_show, _, _ = self._get_schedule_shows()

                    if current_title != last_title:
                        last_title = current_title
                        embed = build_radio_embed(
                            current_title=current_title,
                            current_show=cur_show,
                            volume=vol
                        )
                        try:
                            await message.edit(embed=embed)
                        except discord.NotFound:
                            break
                        except Exception as e:
                            logger.debug(f"Błąd aktualizacji embeda radia: {e}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Nieoczekiwany błąd w pętli aktualizacji radia: {e}")

        self._radio_updater_tasks[guild_id] = asyncio.create_task(_updater())

    async def _fetch_schedule(self) -> Tuple[bool, list[dict]]:
        """Pobiera pełną listę audycji z API ramówki ProjectRPG.
        Zwraca (ok, lista) — `ok=False` oznacza błąd sieci/Cloudflare."""
        try:
            resp = await asyncio.to_thread(
                self.scraper.get, f"{RAMOWKA_API_BASE}/", timeout=15
            )
            if resp.status_code != 200:
                logger.error(f"Ramówka API zwróciło status {resp.status_code} dla /: {resp.text[:200]!r}")
                return False, []
            data = resp.json()
            if isinstance(data, list):
                return True, data
            return False, []
        except Exception as e:
            logger.error(f"Nie udało się pobrać ramówki z API: {e}")
            return False, []

    @staticmethod
    def _is_guild_presenter(username: str, guild: Optional[discord.Guild]) -> bool:
        """Sprawdza, czy podany nick prowadzącego z ramówki odpowiada członkowi serwera
        posiadającemu rangę 'Prezenter'."""
        if not guild or not username:
            return False
        target = username.strip().lower()
        for member in guild.members:
            if any(r.name.lower() == "prezenter" for r in member.roles):
                names_to_match = [member.name.lower(), member.display_name.lower()]
                if member.nick:
                    names_to_match.append(member.nick.lower())
                if target in names_to_match:
                    return True
        return False

    def _format_program_value(
        self, show: dict, *, live: bool, guild: Optional[discord.Guild] = None
    ) -> str:
        name = show.get("name") or "Bez nazwy"
        host = (show.get("user") or {}).get("username") or "Nieznany"
        is_host_presenter = self._is_guild_presenter(host, guild)
        host_badge = " ⭐ *(Prezenter Team77)*" if is_host_presenter else ""

        lines = [f"🎵 **{name}**", f"🎙️ Prowadzi: **{host}**{host_badge}"]

        guests = show.get("guests") or []
        if guests:
            guest_strs = []
            for g in guests:
                gu = g.get("username")
                if not gu:
                    continue
                g_badge = " ⭐ *(Prezenter Team77)*" if self._is_guild_presenter(gu, guild) else ""
                guest_strs.append(f"**{gu}**{g_badge}")
            if guest_strs:
                lines.append(f"👥 Goście: {', '.join(guest_strs)}")

        try:
            start_dt = datetime.fromisoformat(show["dateStart"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(show["dateEnd"].replace("Z", "+00:00"))
            start_ts, end_ts = int(start_dt.timestamp()), int(end_dt.timestamp())
            time_line = f"🕒 <t:{start_ts}:t> – <t:{end_ts}:t>"
            if live:
                time_line += f" (kończy się <t:{end_ts}:R>)"
            else:
                time_line += f" (zacznie się <t:{start_ts}:R>)"
            lines.append(time_line)
        except (KeyError, ValueError, AttributeError):
            pass

        return "\n".join(lines)

    def _get_schedule_shows(self) -> Tuple[bool, Optional[dict], Optional[dict], Optional[dict]]:
        """Zwraca (ok, current, next_show, upcoming_2) na podstawie bieżącego czasu UTC i pamięci podręcznej."""
        if not self._cached_schedule:
            return self._cached_schedule_ok, None, None, None

        now = datetime.now(timezone.utc)
        valid = []
        for s in self._cached_schedule:
            try:
                end_dt = datetime.fromisoformat(s["dateEnd"].replace("Z", "+00:00"))
                if end_dt > now:
                    valid.append(s)
            except Exception:
                continue

        valid.sort(key=lambda x: x.get("dateStart", ""))
        if not valid:
            return self._cached_schedule_ok, None, None, None

        first = valid[0]
        try:
            start_dt = datetime.fromisoformat(first["dateStart"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(first["dateEnd"].replace("Z", "+00:00"))
            if start_dt <= now < end_dt:
                current = first
                nxt = valid[1] if len(valid) > 1 else None
                up2 = valid[2] if len(valid) > 2 else None
            else:
                current = None
                nxt = first
                up2 = valid[1] if len(valid) > 1 else None
            return True, current, nxt, up2
        except Exception:
            return True, None, valid[0], valid[1] if len(valid) > 1 else None

    def _build_audycja_embed(
        self,
        schedule_ok: bool,
        current: Optional[dict],
        next_show: Optional[dict],
        upcoming_2: Optional[dict],
        guild: Optional[discord.Guild] = None,
    ) -> discord.Embed:
        """Buduje nowoczesny embed ramówki z 3 audycjami (teraz, następna, w dalszej kolejności)."""
        embed = discord.Embed(
            title="📻 Radio Project FM — Ramówka Audycji",
            url="https://ramowka.projectrpg.pl",
            color=0xE02444 if current else 0x2B2D31,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url="https://ramowka.projectrpg.pl/logo.png")
        embed.set_footer(
            text=f"{config.BOT_NAME} • Team77 • Aktualizowane na bieżąco",
            icon_url=config.FOOTER_ICON_URL or None,
        )

        # 1. Teraz na antenie
        if current:
            embed.add_field(
                name="🔴 TERAZ NA ANTENIE",
                value=self._format_program_value(current, live=True, guild=guild),
                inline=False,
            )
        elif schedule_ok:
            embed.add_field(
                name="⚪ TERAZ NA ANTENIE",
                value="*Obecnie nic nie jest nadawane (autopilot).* 🎧",
                inline=False,
            )
        else:
            embed.add_field(
                name="⚠️ TERAZ NA ANTENIE",
                value="*Nie udało się pobrać danych z API.*",
                inline=False,
            )

        # 2. Następna audycja
        if next_show:
            embed.add_field(
                name="⏭️ NASTĘPNA AUDYCJA",
                value=self._format_program_value(next_show, live=False, guild=guild),
                inline=False,
            )
        elif schedule_ok:
            embed.add_field(
                name="⏭️ NASTĘPNA AUDYCJA",
                value="*Brak kolejnych zaplanowanych audycji na ten moment.*",
                inline=False,
            )
        else:
            embed.add_field(
                name="⏭️ NASTĘPNA AUDYCJA",
                value="*Nie udało się pobrać danych.*",
                inline=False,
            )

        # 3. W dalszej kolejności (druga nadchodząca audycja)
        if upcoming_2:
            embed.add_field(
                name="🗓️ W DALSZEJ KOLEJNOŚCI",
                value=self._format_program_value(upcoming_2, live=False, guild=guild),
                inline=False,
            )
        embed.add_field(
            name="\u200b",
            value="⭐ Nasi prezenterzy: **Heda**, **Rafix*_Official** ",
            inline=False,
        )
        # Sekcja informacyjna
        embed.add_field(
            name="\u200b",
            value="🔔 *Chcesz wiedzieć, kiedy nasi prezenterzy wchodzą na antenę? Kliknij przycisk poniżej!*",
            inline=False,
        )

        return embed

    def _remember_audycja_location(self, guild_id: int, channel_id: int, message_id: int) -> None:
        """Zapisuje na dysku, gdzie stoi wiadomość audycji danego serwera (przetrwa restart bota)."""
        state = _load_audycja_state()
        state[str(guild_id)] = {"channel_id": channel_id, "message_id": message_id}
        _save_audycja_state(state)

    async def _get_current_audycja_embed(self, guild: Optional[discord.Guild] = None) -> discord.Embed:
        """Pobiera lub odświeża z pamięci podręcznej pełną ramówkę i zwraca gotowy embed."""
        now = datetime.now(timezone.utc)
        need_fetch = (
            self._last_audycja_fetch is None
            or not self._cached_schedule
            or (now - self._last_audycja_fetch).total_seconds() >= AUDYCJA_FETCH_INTERVAL_SECONDS
        )

        if need_fetch:
            ok, data = await self._fetch_schedule()
            if ok:
                self._cached_schedule_ok = True
                self._cached_schedule = data
                self._last_audycja_fetch = now
            elif not self._cached_schedule:
                self._cached_schedule_ok = False

        ok, cur, nxt, up2 = self._get_schedule_shows()
        return self._build_audycja_embed(ok, cur, nxt, up2, guild=guild)

    async def _check_presenter_announcement(self, guild_id: int, msg: discord.Message) -> None:
        """Wysyła ping do roli powiadomień, gdy członek organizacji z rangą 'Prezenter' wejdzie na żywo na antenę."""
        guild = msg.guild or self.bot.get_guild(guild_id)
        if not guild:
            return

        ok, cur, _, _ = self._get_schedule_shows()
        if not cur:
            return

        show_id = f"{cur.get('dateStart')}_{cur.get('name')}_{(cur.get('user') or {}).get('username')}"
        if self._last_announced_shows.get(guild_id) == show_id:
            return

        host = (cur.get("user") or {}).get("username") or ""
        is_org_presenter = self._is_guild_presenter(host, guild)
        presenter_name = host

        if not is_org_presenter:
            for g in (cur.get("guests") or []):
                gu = g.get("username", "")
                if self._is_guild_presenter(gu, guild):
                    is_org_presenter = True
                    presenter_name = gu
                    break

        self._last_announced_shows[guild_id] = show_id

        if not is_org_presenter:
            return

        role = discord.utils.get(guild.roles, name="Powiadomienia Audycje")
        mention_str = role.mention if role else "**@Powiadomienia Audycje**"
        show_name = cur.get("name") or "Audycja"

        announcement = (
            f"🎙️ {mention_str} **Nasz prezenter {presenter_name} właśnie wszedł na antenę Radia Project FM!**\n"
            f"📻 **{show_name}** — zapraszamy do słuchania radia w grze albo na kanale głosowym!"
        )

        try:
            await msg.channel.send(announcement)
            logger.info(f"Wysłano powiadomienie o audycji prezentera {presenter_name} na serwerze {guild_id}.")
        except Exception as e:
            logger.warning(f"Nie udało się wysłać wzmianki o audycji: {e}")

    async def _recreate_audycja_message(self, guild_id: int, channel: discord.abc.Messageable) -> Optional[discord.Message]:
        """Tworzy nowy embed audycji na wskazanym kanale, gdy stary zniknął / został usunięty."""
        try:
            guild = getattr(channel, "guild", None) or self.bot.get_guild(guild_id)
            embed = await self._get_current_audycja_embed(guild=guild)
            new_msg = await channel.send(embed=embed, view=AudycjaControlView())
            self._audycja_messages[guild_id] = new_msg
            self._audycja_fail_counts[guild_id] = 0
            if hasattr(channel, "id"):
                self._remember_audycja_location(guild_id, channel.id, new_msg.id)
            if not self._refresh_audycja.is_running():
                self._refresh_audycja.start()
            logger.info(f"Odtworzono kartę audycji dla serwera {guild_id} (msg: {new_msg.id}).")
            return new_msg
        except Exception as e:
            logger.error(f"Nie udało się odtworzyć karty audycji dla serwera {guild_id}: {e}")
            return None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Po (ponownym) uruchomieniu bota: przywraca wiadomości audycji w tych samych kanałach."""
        if self._audycja_restored:
            return
        self._audycja_restored = True
        await self._restore_audycja_messages()

    async def _restore_audycja_messages(self) -> None:
        state = _load_audycja_state()
        new_state = {}

        if state:
            for gid_str, loc in state.items():
                try:
                    guild_id = int(gid_str)
                    channel_id = int(loc["channel_id"])
                except (KeyError, ValueError, TypeError):
                    continue

                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except Exception as e:
                        logger.warning(f"Nie znaleziono kanału audycji ({channel_id}) dla serwera {gid_str}: {e}")
                        continue

                guild = getattr(channel, "guild", None) or self.bot.get_guild(guild_id)
                embed = await self._get_current_audycja_embed(guild=guild)

                old_message_id = loc.get("message_id")
                if old_message_id:
                    try:
                        old_msg = await channel.fetch_message(int(old_message_id))
                        await old_msg.delete()
                    except Exception:
                        pass  # stara wiadomość mogła już nie istnieć — nic się nie stanie

                try:
                    new_msg = await channel.send(embed=embed, view=AudycjaControlView())
                except Exception as e:
                    logger.error(f"Nie udało się przywrócić wiadomości audycji dla serwera {gid_str}: {e}")
                    continue

                self._audycja_messages[guild_id] = new_msg
                new_state[gid_str] = {"channel_id": channel_id, "message_id": new_msg.id}
                logger.info(f"Przywrócono kartę audycji po restarcie bota (serwer {gid_str}).")
        else:
            # Gdy stan był pusty (np. świeża instalacja bota), znajdź kanał audycji automatycznie
            for guild in self.bot.guilds:
                ch = self._find_audycja_channel(guild)
                if ch:
                    new_msg = await self._recreate_audycja_message(guild.id, ch)
                    if new_msg:
                        new_state[str(guild.id)] = {"channel_id": ch.id, "message_id": new_msg.id}

        if new_state:
            _save_audycja_state(new_state)
        if self._audycja_messages and not self._refresh_audycja.is_running():
            self._refresh_audycja.start()

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """Reaguje natychmiast, gdy embed audycji zostanie usunięty z Discorda."""
        for gid, msg in list(self._audycja_messages.items()):
            if msg.id == payload.message_id:
                logger.warning(f"Wiadomość audycji ({gid}) została usunięta z Discorda — natychmiast tworzę nową.")
                channel = msg.channel
                if channel is None or not hasattr(channel, "send"):
                    channel = self.bot.get_channel(payload.channel_id)
                    if channel is None:
                        try:
                            channel = await self.bot.fetch_channel(payload.channel_id)
                        except Exception:
                            return
                await self._recreate_audycja_message(gid, channel)
                break

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        """Reaguje, gdy embed audycji zniknie podczas masowego usuwania wiadomości (purge)."""
        for gid, msg in list(self._audycja_messages.items()):
            if msg.id in payload.message_ids:
                logger.warning(f"Wiadomość audycji ({gid}) zniknęła w wyniku purge — tworzę nową.")
                channel = msg.channel
                if channel is None or not hasattr(channel, "send"):
                    channel = self.bot.get_channel(payload.channel_id)
                    if channel is None:
                        try:
                            channel = await self.bot.fetch_channel(payload.channel_id)
                        except Exception:
                            return
                await self._recreate_audycja_message(gid, channel)

    @tasks.loop(seconds=AUDYCJA_EDIT_INTERVAL_SECONDS)
    async def _refresh_audycja(self) -> None:
        """Cyklicznie edytuje wiadomości audycji. Jeśli zniknęły lub wystąpił błąd — tworzy nowe."""
        if not self._audycja_messages:
            return

        dead = []
        for gid, msg in list(self._audycja_messages.items()):
            guild = msg.guild or self.bot.get_guild(gid)
            try:
                embed = await self._get_current_audycja_embed(guild=guild)
                await msg.edit(embed=embed, view=AudycjaControlView())
                self._audycja_fail_counts[gid] = 0

                await self._check_presenter_announcement(gid, msg)
                continue
            except discord.NotFound:
                logger.warning(f"Karta audycji (serwer {gid}) zniknęła (NotFound), tworzę nową.")
            except Exception as e:
                fails = self._audycja_fail_counts.get(gid, 0) + 1
                self._audycja_fail_counts[gid] = fails
                logger.debug(f"Nieudana edycja audycji ({gid}), próba {fails}/{AUDYCJA_FAIL_THRESHOLD}: {e}")
                if fails < AUDYCJA_FAIL_THRESHOLD:
                    continue

            # Zniknęła lub przekroczono limit prób
            self._audycja_fail_counts[gid] = 0
            channel = msg.channel
            try:
                await msg.delete()
            except Exception:
                pass

            new_msg = await self._recreate_audycja_message(gid, channel)
            if not new_msg:
                dead.append(gid)

        for gid in dead:
            self._audycja_messages.pop(gid, None)
            self._audycja_fail_counts.pop(gid, None)

    @_refresh_audycja.error
    async def _refresh_audycja_error(self, error: Exception) -> None:
        logger.error(f"Pętla odświeżania audycji napotkała błąd: {error}", exc_info=error)
        if not self._refresh_audycja.is_running():
            self._refresh_audycja.restart()

    @_refresh_audycja.before_loop
    async def _before_refresh_audycja(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="audycja", description="Pokazuje aktualną i nadchodzące audycje Radia ProjectRPG.")
    async def audycja(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            embed = await self._get_current_audycja_embed(guild=interaction.guild)
        except Exception as e:
            logger.error(f"Nie udało się pobrać embeda audycji: {e}")
            await interaction.followup.send(
                embed=error_embed("Błąd", "Nie udało się połączyć z ramówką ProjectRPG. Spróbuj ponownie za chwilę."),
                ephemeral=True,
            )
            return

        # Jeśli dla tego serwera istniała już wcześniej aktywna karta audycji,
        # usuwamy starą, żeby nie dublować kart na serwerze.
        old_msg = self._audycja_messages.get(interaction.guild_id)
        if old_msg:
            try:
                await old_msg.delete()
            except Exception:
                pass

        try:
            message = await interaction.channel.send(embed=embed, view=AudycjaControlView())
        except discord.HTTPException as e:
            logger.error(f"Nie udało się wysłać karty ramówki: {e}")
            await interaction.followup.send(
                embed=error_embed("Błąd", "Nie udało się wysłać karty ramówki na ten kanał."),
                ephemeral=True,
            )
            return

        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

        self._audycja_messages[interaction.guild_id] = message
        self._audycja_fail_counts[interaction.guild_id] = 0
        self._remember_audycja_location(interaction.guild_id, interaction.channel_id, message.id)
        if not self._refresh_audycja.is_running():
            self._refresh_audycja.start()

    async def start_radio_for_interaction(self, interaction: discord.Interaction) -> None:
        """Uruchamia oficjalny stream Radia Project FM na kanale głosowym użytkownika."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            msg = "Musisz być połączony z kanałem głosowym, aby włączyć Radio Project FM!"
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed("Błąd", msg), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Błąd", msg), ephemeral=True)
            return

        channel = interaction.user.voice.channel
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        radio_url = getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3")

        vc = interaction.guild.voice_client
        if vc is not None and vc.is_playing():
            vc.stop()

        # Połącz z kanałem głosowym jako natywny VoiceClient
        if vc is None or not vc.is_connected():
            try:
                vc = await channel.connect(self_deaf=True)
            except Exception as e:
                await interaction.followup.send(
                    embed=error_embed("Błąd Połączenia", f"Nie udało się połączyć z kanałem głosowym:\n`{_short_error(e)}`"),
                    ephemeral=True
                )
                return
        elif vc.channel.id != channel.id:
            try:
                await vc.move_to(channel)
            except Exception as e:
                await interaction.followup.send(
                    embed=error_embed("Błąd Połączenia", f"Nie udało się przenieść bota: `{e}`"),
                    ephemeral=True
                )
                return

        if vc.is_playing():
            vc.stop()

        # Natywny odtwarzacz FFmpeg z regulacją głośności PCMVolumeTransformer
        ffmpeg_bin = shutil.which("ffmpeg") or ("C:\\FFMPEG\\bin\\ffmpeg.exe" if os.path.exists("C:\\FFMPEG\\bin\\ffmpeg.exe") else "ffmpeg")
        raw_source = discord.FFmpegPCMAudio(
            radio_url,
            executable=ffmpeg_bin,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn"
        )
        source = discord.PCMVolumeTransformer(raw_source, volume=1.0)
        vc.play(source)

        current_title = await fetch_radio_stream_title(radio_url)
        _, cur_show, _, _ = self._get_schedule_shows()

        # Embed radia bez wskazywania kto uruchomił komendę (requester=None)
        embed = build_radio_embed(
            current_title=current_title,
            current_show=cur_show,
            requester=None,
            volume=100
        )

        # Karta radia wysyłana jest TYLKO na kanał audycji
        target_channel = self._find_audycja_channel(interaction.guild) or interaction.channel

        # Usuń poprzednią kartę radia na tym kanale, jeśli istniała
        old_msg = self._radio_messages.pop(interaction.guild_id, None)
        if old_msg:
            try:
                await old_msg.delete()
            except Exception:
                pass

        try:
            radio_msg = await target_channel.send(embed=embed, view=RadioControlView(self))
            self._radio_messages[interaction.guild_id] = radio_msg
            self._start_radio_updater(interaction.guild_id, radio_msg)
        except Exception as e:
            logger.error(f"Nie udało się wysłać karty radia na kanał {target_channel}: {e}")

        # Prywatne potwierdzenie dla użytkownika — nikt na kanale nie widzi wywołania komendy
        loc_str = f" na kanale {target_channel.mention}" if target_channel != interaction.channel else ""
        await interaction.followup.send(
            embed=success_embed(
                "Radio Project FM 📻",
                f"Uruchomiono oficjalny stream Radia Project FM na Twoim kanale głosowym **{channel.name}**!\n"
                f"Karta radia i panel sterowania są aktywne{loc_str}."
            ),
            ephemeral=True
        )

    @app_commands.command(name="radio", description="Włącza oficjalny stream Radia Project FM na żywo na Twoim kanale głosowym.")
    async def radio(self, interaction: discord.Interaction) -> None:
        """Uruchamia transmisję Radia Project FM."""
        await self.start_radio_for_interaction(interaction)

    @app_commands.command(name="projectfm", description="Odtwarza Radio Project FM na żywo (alias dla /radio).")
    async def projectfm(self, interaction: discord.Interaction) -> None:
        """Alias dla /radio."""
        await self.start_radio_for_interaction(interaction)


# Alias dla kompatybilności wstecznej
Music = Audycja


async def setup(bot: commands.Bot) -> None:
    cog = Audycja(bot)
    await bot.add_cog(cog)
    bot.add_view(AudycjaControlView())
    bot.add_view(RadioControlView(cog))