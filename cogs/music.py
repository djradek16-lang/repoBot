"""
Moduł muzyczny dla bota Discord (Music Cog) oparty o serwer Lavalink.

Cały ciężar pobierania/streamowania audio (YouTube/SoundCloud/itd.) leży po
stronie węzła Lavalink (przez bibliotekę wavelink) - bot Discord wysyła tylko
komendy i odbiera gotowy strumień audio.

=== WARSTWA WIZUALNA ===
Styl (kolory embedów, pasek postępu, przyciski sterujące, żywo odświeżana
karta "Teraz Odtwarzane") przeniesiony z rozbudowanej wersji opartej o yt-dlp.
Kluczowe uproszczenie względem tamtej wersji: Lavalink SAM śledzi pozycję
odtwarzania (`player.position`, w ms, aktualizowane przez węzeł), więc CAŁA
ręczna księgowość czasu (`time.monotonic()`, `paused_since`, `paused_accum`)
jest zbędna i została pominięta - progress bar czyta pozycję bezpośrednio
z odtwarzacza.

WYMAGANIA:
- Skonfigurowany i działający węzeł Lavalink (host, port, hasło) w .env
  (patrz config.py: LAVALINK_HOST / LAVALINK_PORT / LAVALINK_PASSWORD / LAVALINK_SECURE).
- Biblioteka `wavelink` w requirements.txt: wavelink>=3.4,<4
"""

import asyncio
import difflib
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Literal, Tuple

import json
import os
import shutil
import aiohttp
import cloudscraper
import discord
from discord.ext import commands, tasks
from discord import app_commands
import wavelink

from utils.embeds import success_embed, error_embed, info_embed, create_embed
from core.checks import is_owner
import config

logger = logging.getLogger("AegisBot.Cogs.Music")

RAMOWKA_API_BASE = "https://ramowka.projectrpg.pl/api/program"

INACTIVITY_TIMEOUT_SECONDS = 180

# Karta /audycja ma DWA niezależne interwały:
# - AUDYCJA_FETCH_INTERVAL_SECONDS: jak często faktycznie odpytujemy stronę
#   ramowka.projectrpg.pl (żeby nie zarzucać jej zapytaniami).
# - AUDYCJA_EDIT_INTERVAL_SECONDS: jak często odświeżamy samą wiadomość na
#   Discordzie - między fetchami korzystamy z ostatnio pobranych danych
#   (patrz _local_promote), więc edycja NIE oznacza nowego zapytania do API.
AUDYCJA_FETCH_INTERVAL_SECONDS = 120       # 2 minuty (szybkie wykrywanie wejścia DJ-a na antenę)
AUDYCJA_EDIT_INTERVAL_SECONDS = 60        # 1 minuta
AUDYCJA_FAIL_THRESHOLD = 3                # ile kolejnych nieudanych edycji z rzędu = wiadomość uznana za "martwą"

# Zapamiętuje, w którym kanale ostatnio wywołano /audycja (per serwer), żeby po
# restarcie bota móc usunąć starą wiadomość i wkleić nową w tym samym miejscu (wzorzec statusu serwera).
AUDYCJA_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prpg_audycja_state.json")


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

# --- Kolory embedów (spójny język wizualny w całym module) ---
COLOR_NOW_PLAYING = 0x1DB954  # zielony, styl "na żywo"
COLOR_QUEUED = 0x5865F2       # blurple Discorda
COLOR_PAUSED = 0xF1C40F       # żółty
COLOR_IDLE = 0x2B2D31         # ciemnoszary, "nic się nie dzieje"

PROGRESS_BAR_LENGTH = 18
PROGRESS_UPDATE_INTERVAL = 15  # co ile sekund odświeżać pasek postępu (rate-limit friendly)
PROGRESS_FILLED = "▰"
PROGRESS_EMPTY = "▱"
PROGRESS_KNOB = "🔘"

# Próg "podejrzanie długiego" pojedynczego utworu w kolejce (patrz _sum_duration) -
# ponad 3h dla utworu dodanego przez /play niemal zawsze oznacza błędnie zgłoszony
# czas trwania (transmisja/radio/mix), a nie realną, celowo zakolejkowaną długość.
SUSPICIOUS_TRACK_SECONDS = 3 * 60 * 60

# Ile pierwszych wyników wyszukiwania Lavalink bierzemy pod uwagę przy wyborze
# "najlepszego" dopasowania (patrz _pick_best_search_result). Standardowy Lavalink
# NIE zwraca liczby wyświetleń/popularności utworu, więc nie da się sortować
# dosłownie "po popularności" - to podejście przybliża ten efekt przez dopasowanie
# tytułu do zapytania, żeby unikać sytuacji, w której YouTube stawia wyżej
# mashup/remix/cover zamiast oryginału tylko dlatego, że fraza gdzieś w tytule pasuje.
SEARCH_CANDIDATE_POOL = 8

# Słowa w tytule, których NIE było w zapytaniu, a które sugerują, że to NIE jest
# "zwykła", oryginalna wersja utworu (mashup, przeróbka, cover, itp.) - obniżają ocenę
# kandydata, żeby taki wynik nie wygrywał tylko dlatego, że fraza zapytania gdzieś
# w tytule pasuje.
_SEARCH_NOISE_WORDS = frozenset({
    "mashup", "cover", "remix", "sped", "slowed", "reverb", "nightcore",
    "8d", "tiktok", "edit", "mix", "extended", "instrumental", "karaoke",
    "reaction", "type", "beat", "freestyle", "parody", "amv", "meme",
})

# Litery i cyfry są osobnymi grupami tokenów - dzięki temu "1AM" (bez spacji
# w zapytaniu) i "1 AM" (ze spacją w oficjalnym tytule) tokenizują się identycznie
# jako {"1", "am"}. Wcześniej jeden wzorzec [a-z0-9]+ łączył cyfry i litery w jeden
# token, więc "1am" (bez spacji, tytuł nieoficjalnego uploadu) pasował idealnie do
# zapytania "1AM", a "1 AM" (poprawny, oficjalny tytuł) - wcale, bo "1" i "am" to
# w tamtej wersji dwa OSOBNE tokeny nie do dopasowania z jednym tokenem "1am".
_SEARCH_TOKEN_RE = re.compile(r"[a-ząćęłńóśźż]+|[0-9]+", re.IGNORECASE)

# Do dopasowania "znakowego" (patrz _normalize) - usuwa WSZYSTKO poza literami
# i cyframi, łącznie ze spacjami. Dzięki temu zapytanie "whitewidow drip" (artysta
# sklejony bez spacji) i tytuł "White Widow - Drip" (artysta rozdzielony spacją)
# stają się identycznymi ciągami "whitewidowdrip" - mimo że jako ODDZIELNE TOKENY
# ("whitewidow" vs "white"+"widow") w ogóle by się nie pokryły. Samo porównanie
# tokenów zawodzi w obie strony (rozdzielone/sklejone słowa), więc jest tu tylko
# jednym z sygnałów, nie jedynym.
_NORMALIZE_RE = re.compile(r"[^a-ząćęłńóśźż0-9]+", re.IGNORECASE)


def _normalize(s: str) -> str:
    return _NORMALIZE_RE.sub("", s.lower())


def _score_search_candidate(query: str, track: wavelink.Playable) -> tuple:
    """Ocenia, jak dobrze wynik wyszukiwania odpowiada zapytaniu - próba wybrania
    oryginalnego/oficjalnego utworu zamiast losowego mashupu, coveru czy remiksu,
    które potrafią wygrać w wynikach YouTube tylko dlatego, że fraza zapytania
    gdzieś w ich (dłuższym, bardziej "szumowym") tytule też pasuje.

    Zwraca KROTKĘ, nie pojedynczą liczbę. Powód: przy jednym połączonym wyniku
    zdarzały się przypadkowe remisy/prawie-remisy (np. dwa kandydaci różniące
    się tylko drobną karą za "dodatkowe słowa"), gdzie o zwycięstwie decydowała
    de facto kolejność wyników z Lavalink, a nie faktyczna jakość dopasowania.
    Krotka porównywana jest element po elemencie, więc kolejność pól = kolejność
    ważności sygnałów: (1) łączny wynik ważony, (2) PEŁNE pokrycie tokenów
    zapytania (całe zapytanie odnalezione w tytule+wykonawcy - silny,
    binarny sygnał), (3) oficjalny/topic kanał, (4) mniej "obcych" słów
    w tytule. Dzięki temu remisy w polu (1) rozstrzygane są w oparciu
    o twardsze przesłanki zamiast po cichu, przypadkowo."""
    q_tokens = set(t.lower() for t in _SEARCH_TOKEN_RE.findall(query))
    if not q_tokens:
        return (0.0, False, False, 0)

    title = track.title or ""
    title_lower = title.lower()
    t_token_set = set(t.lower() for t in _SEARCH_TOKEN_RE.findall(title))

    author = getattr(track, "author", "") or ""
    author_lower = author.lower()
    a_token_set = set(t.lower() for t in _SEARCH_TOKEN_RE.findall(author))

    # Łączymy tokeny tytułu i wykonawcy - zapytania typu "1AM CIV" zawierają
    # zwykle też nazwę artysty/kanału, która nie występuje w samym tytule
    # utworu, tylko w polu `author`. Bez tego "civ" nigdy nie miałby szans się
    # dopasować, mimo że to poprawna nazwa wykonawcy. Porównanie na poziomie
    # SŁÓW (a nie tylko znaków, patrz niżej) jest niezbędne dla przypadków,
    # gdzie MIĘDZY nazwą artysty a tytułem wciśnięte jest dodatkowe słowo
    # (np. "White Widow (Official) - Drip") - takie "wtrącenie" psuje
    # dopasowanie znakowe/podciągowe, ale nie psuje dopasowania po tokenach,
    # bo kolejność/sąsiedztwo słów nie ma tu znaczenia.
    combined_title_author = t_token_set | a_token_set
    coverage = len(q_tokens & combined_title_author) / len(q_tokens)
    full_coverage = coverage >= 0.999
    score = coverage * 10.0

    # Tytuł (lub tytuł+wykonawca) zaczynający się od frazy zapytania to
    # najsilniejszy sygnał, że to "ten" utwór, a nie przeróbka, w której fraza
    # jest gdzieś w środku/nawiasach.
    combined_start = f"{title_lower} {author_lower}".strip()
    if combined_start.startswith(query.strip().lower()):
        score += 3.0

    # Dopasowanie znakowe (bez spacji/myślników) - łapie przypadki, których
    # porównanie "słowo po słowie" NIE widzi, bo ktoś napisał artystę/tytuł
    # sklejony (np. "whitewidow") tam, gdzie w prawdziwym tytule/wykonawcy jest
    # spacja lub myślnik ("White Widow - Drip"), albo odwrotnie. To osobny,
    # niezależny sygnał od `coverage` powyżej (który zawodzi właśnie w tym
    # przypadku, bo "whitewidow" jako CAŁY token nie jest równy ani "white",
    # ani "widow") - oba sygnały razem pokrywają wzajemnie swoje słabości.
    norm_query = _normalize(query)
    norm_title_author = _normalize(f"{title} {author}")
    if norm_query and norm_title_author:
        if norm_query in norm_title_author:
            score += 8.0
        else:
            ratio = difflib.SequenceMatcher(None, norm_query, norm_title_author).ratio()
            score += ratio * 5.0

    # Słowo z tytułu spoza zapytania, które sugeruje przeróbkę/mashup/cover,
    # jest silniejszym negatywnym sygnałem niż same "dodatkowe słowa" (patrz
    # extra_words niżej) - stąd wyraźnie większa kara.
    noise_hits = len((t_token_set - q_tokens) & _SEARCH_NOISE_WORDS)
    score -= noise_hits * 4.0

    is_official = any(marker in author_lower for marker in ("official", "- topic", "vevo"))
    if is_official:
        score += 3.0

    # Krótszy, bardziej "czysty" tytuł (mniej dodatkowych słów spoza zapytania)
    # dostaje mały bonus - mashupy/przeróbki mają zwykle długie, złożone tytuły.
    # Celowo bardzo mały (0.15/słowo, max 6 słów) - to tylko delikatny
    # tie-breaker w polu (1), a nie główny sygnał (tym są coverage i
    # dopasowanie znakowe powyżej); zbyt duża waga karałaby poprawne wyniki
    # z dłuższym, ale wciąż trafnym tytułem (np. "(Official video)").
    extra_words = len(t_token_set - q_tokens)
    score -= min(extra_words, 6) * 0.15

    return (round(score, 4), full_coverage, is_official, -extra_words)


def _pick_best_search_result(query: str, results) -> wavelink.Playable:
    """Wybiera najlepiej dopasowany utwór spośród pierwszych SEARCH_CANDIDATE_POOL
    wyników, zamiast bezrefleksyjnie brać wynik #1 zwrócony przez węzeł Lavalink."""
    candidates = list(results[:SEARCH_CANDIDATE_POOL])
    if len(candidates) <= 1:
        return candidates[0]
    return max(candidates, key=lambda t: _score_search_candidate(query, t))


# Margines bezpieczeństwa dla treści wstawianej do opisu embeda błędu -
# niektóre wyjątki (np. wavelink.LavalinkLoadException, gdy WSZYSCY klienci
# YouTube zawiodą) potrafią zawierać pełny, wieloliniowy stos wywołań Javy w
# samej treści komunikatu (`str(exc)`), co bez obcięcia potrafiło samo
# przekroczyć limit 4096 znaków na opis embeda Discorda - powodując DRUGI,
# ukryty crash przy próbie wysłania SAMEGO komunikatu o błędzie.
_ERROR_MESSAGE_MAX_CHARS = 500

# Linie stosu wywołań Javy (np. z wavelink.LavalinkLoadException) mają postać
# "\n        at pakiet.Klasa.metoda(Plik.java:123)" - obcinamy komunikat
# TUŻ PRZED pierwszą taką linią, żeby użytkownik widział czytelne
# podsumowanie błędu ("All clients failed to load the item.") zamiast
# nieczytelnego zrzutu stosu wywołań.
_JAVA_STACK_FRAME_RE = re.compile(r"\n\s*at\s")
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _short_error(exc: BaseException) -> str:
    """Zamienia komunikat wyjątku na krótki, czytelny tekst do embeda:
    obcina ewentualny doklejony stos wywołań Javy (patrz _JAVA_STACK_FRAME_RE),
    zwija wielokrotne białe znaki do pojedynczych spacji, a na końcu i tak
    ucina do bezpiecznej długości (patrz _ERROR_MESSAGE_MAX_CHARS)."""
    text = str(exc)

    match = _JAVA_STACK_FRAME_RE.search(text)
    if match:
        text = text[:match.start()]

    text = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    if len(text) > _ERROR_MESSAGE_MAX_CHARS:
        text = text[:_ERROR_MESSAGE_MAX_CHARS].rstrip() + "…"
    return text or exc.__class__.__name__


def _format_duration(total_seconds: int) -> str:
    """Formatuje sekundy jako M:SS lub H:MM:SS."""
    if total_seconds <= 0:
        return "0:00"
    m, s = divmod(int(total_seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _progress_bar(elapsed: float, duration: float, length: int = PROGRESS_BAR_LENGTH) -> str:
    """Buduje wizualny pasek postępu, np. ▰▰▰▰▰🔘▱▱▱▱▱▱▱▱  1:12 / 3:45."""
    if duration <= 0:
        return "🔴 `NA ŻYWO`"
    ratio = max(0.0, min(1.0, elapsed / duration))
    knob_pos = min(length - 1, int(ratio * length))
    bar = PROGRESS_FILLED * knob_pos + PROGRESS_KNOB + PROGRESS_EMPTY * (length - knob_pos - 1)
    return f"`{_format_duration(elapsed)}` {bar} `{_format_duration(duration)}`"


def _sum_duration(tracks, *, mark_uncertain: bool = False) -> str:
    """Sumuje czas trwania listy utworów wavelink (pomija utwory na żywo).

    Pojedynczy utwór z absurdalnie długim czasem trwania (> SUSPICIOUS_TRACK_SECONDS)
    prawie zawsze oznacza transmisję/radio/mix z błędnie zgłoszonym `length` przez
    węzeł Lavalink (część źródeł nie ustawia przy tym poprawnie flagi `is_stream`).
    Taki wpis jest pomijany w sumie, żeby nie zawyżał estymacji do kilku godzin przy
    resztek kolejki złożonej z kilkuminutowych piosenek. Gdy `mark_uncertain=True`
    i coś zostało pominięte, do wyniku doklejane jest "+", sygnalizując że to dolna
    granica, a nie dokładna wartość."""
    total_ms = 0
    skipped_long = False
    for t in tracks:
        if getattr(t, "is_stream", False):
            continue
        length = getattr(t, "length", 0) or 0
        if length // 1000 > SUSPICIOUS_TRACK_SECONDS:
            skipped_long = True
            continue
        total_ms += length
    formatted = _format_duration(total_ms // 1000)
    if mark_uncertain and skipped_long:
        return formatted + "+"
    return formatted


def get_requester(track: wavelink.Playable) -> str:
    """Odczytuje osobę, która zleciła utwór (przechowywane jako dynamiczny atrybut na obiekcie track)."""
    return getattr(track, "requested_by", "Nieznany")


def build_track_embed(
    track: wavelink.Playable,
    *,
    elapsed: Optional[float] = None,
    status_label: Optional[str] = None,
    title_prefix: str = "🎶  ",
) -> discord.Embed:
    """Tworzy estetyczny embed z informacjami o utworze wavelink.

    `elapsed` (opcjonalnie, w sekundach): jeśli podane, dorysowuje żywy pasek
    postępu zamiast samego czasu trwania. `status_label` nadpisuje domyślny
    tytuł-eyebrow (np. "▶️ Teraz Odtwarzane", "⏸️ Wstrzymano")."""
    is_stream = getattr(track, "is_stream", False)
    duration_seconds = 0 if is_stream else track.length // 1000
    color = COLOR_NOW_PLAYING if elapsed is not None else COLOR_QUEUED

    if elapsed is not None:
        time_line = _progress_bar(elapsed, duration_seconds)
    elif is_stream:
        time_line = "🔴 `NA ŻYWO`"
    else:
        time_line = f"`{_format_duration(duration_seconds)}`"

    description = (
        f"{status_label}\n\n" if status_label else ""
    ) + (
        f"👤 **Kanał:** `{track.author}`\n"
        f"⏱️ **Czas:** {time_line}\n"
        f"🙋 **Zlecono przez:** {get_requester(track)}"
    )

    embed = create_embed(
        title=title_prefix + track.title,
        description=description,
        url=track.uri,
        thumbnail_url=getattr(track, "artwork", None),
        color=color,
    )
    return embed


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
            "name": "⚡Status",
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


class AegisPlayer(wavelink.Player):
    """Rozszerzony odtwarzacz wavelink przechowujący stan i UI specyficzne dla serwera."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text_channel: Optional[discord.TextChannel] = None
        self.loop_mode: Literal["off", "track", "queue"] = "off"
        self.inactivity_task: Optional[asyncio.Task] = None
        self.progress_task: Optional[asyncio.Task] = None
        self.now_playing_message: Optional[discord.Message] = None
        # Gdy /play startuje utwór natychmiast, sam wysyła bogatą kartę jako
        # odpowiedź na interakcję - ustawia tę flagę, żeby on_wavelink_track_start
        # nie wysłał DRUGIEJ, zdublowanej karty na kanał. Podobnie przy powtórce
        # utworu w trybie pętli "track" (nie ma sensu spamować tym samym embedem
        # co kilka sekund/minut).
        self.suppress_next_announcement: bool = False

    def cancel_inactivity(self) -> None:
        if self.inactivity_task and not self.inactivity_task.done():
            self.inactivity_task.cancel()
        self.inactivity_task = None

    def cancel_progress_updater(self) -> None:
        if self.progress_task and not self.progress_task.done():
            self.progress_task.cancel()
        self.progress_task = None


class MusicControlView(discord.ui.View):
    """Panel przycisków sterowania odtwarzaczem, dołączany pod embedem 'teraz gra'.

    Persistent (timeout=None, stałe custom_id) — działa nawet po restarcie bota,
    o ile zostanie zarejestrowany przez bot.add_view() w setup()."""

    def __init__(self):
        super().__init__(timeout=None)

    def _get_cog(self, interaction: discord.Interaction) -> Optional["Music"]:
        return interaction.client.get_cog("MusicCog")

    async def _require_same_channel(self, interaction: discord.Interaction) -> Optional[AegisPlayer]:
        """Wspólna walidacja: bot musi grać, a klikający musi siedzieć na tym samym kanale."""
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or not player.connected:
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Bot nie jest obecnie połączony z żadnym kanałem głosowym."),
                ephemeral=True
            )
            return None
        user_channel = interaction.user.voice.channel if interaction.user.voice else None
        if user_channel != player.channel:
            await interaction.response.send_message(
                embed=error_embed("Błąd", f"Musisz być na kanale {player.channel.mention}, żeby sterować odtwarzaczem."),
                ephemeral=True
            )
            return None
        return player

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary, custom_id="music_toggle_pause")
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = await self._require_same_channel(interaction)
        if not player:
            return
        if player.playing and not player.paused:
            await player.pause(True)
            await interaction.response.send_message(embed=success_embed("Wstrzymano ⏸️", "Muzyka została wstrzymana."), ephemeral=True)
        elif player.paused:
            await player.pause(False)
            await interaction.response.send_message(embed=success_embed("Wznowiono ▶️", "Wznowiono odtwarzanie."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", "Nic teraz nie gra."), ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music_skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = await self._require_same_channel(interaction)
        if not player:
            return
        if player.playing:
            title = player.current.title if player.current else "Utwór"
            if player.loop_mode == "track":
                player.loop_mode = "off"
            await player.skip(force=True)
            await interaction.response.send_message(embed=success_embed("Pominięto ⏭️", f"Pominięto **{title}**."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", "Żaden utwór nie jest obecnie odtwarzany."), ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music_stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = await self._require_same_channel(interaction)
        if not player:
            return
        player.cancel_inactivity()
        player.cancel_progress_updater()
        player.queue.clear()
        # Odpowiedz PRZED disconnect() - wolne rozłączenie (np. przy problemach
        # sieciowych węzła) mogłoby unieważnić token interakcji.
        await interaction.response.send_message(embed=success_embed("Zatrzymano ⏹️", "Kolejka wyczyszczona, bot opuścił kanał."), ephemeral=True)
        await player.disconnect()

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music_shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = await self._require_same_channel(interaction)
        if not player:
            return
        if len(player.queue) < 2:
            await interaction.response.send_message(embed=error_embed("Błąd", "W kolejce musi być przynajmniej 2 utwory."), ephemeral=True)
            return
        player.queue.shuffle()
        await interaction.response.send_message(embed=success_embed("Przetasowano 🔀", f"Wymieszano **{len(player.queue)}** utworów."), ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music_loop")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = await self._require_same_channel(interaction)
        if not player:
            return
        order: list[Literal["off", "track", "queue"]] = ["off", "track", "queue"]
        player.loop_mode = order[(order.index(player.loop_mode) + 1) % len(order)]
        labels = {"off": "Wyłączona ❌", "track": "Jeden Utwór 🔂", "queue": "Cała Kolejka 🔁"}
        await interaction.response.send_message(embed=success_embed("Tryb Pętli 🔁", f"Ustawiono na: **{labels[player.loop_mode]}**."), ephemeral=True)

    @discord.ui.button(label="Kolejka", emoji="📜", style=discord.ButtonStyle.primary, custom_id="music_show_queue")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = self._get_cog(interaction)
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        await interaction.response.send_message(embed=cog.build_queue_embed(player, interaction.guild.name), ephemeral=True)

    @discord.ui.button(label="Edytuj", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="music_edit_queue")
    async def edit_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = await self._require_same_channel(interaction)
        if not player:
            return
        view = QueueEditView(player)
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
        )


class QueueEditView(discord.ui.View):
    """Panel do zarządzania kolejką: wybór utworu z listy rozwijanej + akcje
    (zagraj teraz / usuń). Widok efemeryczny (widoczny tylko dla klikającego),
    więc nie musi być rejestrowany jako persistent - żyje tylko przez czas
    trwania interakcji (timeout 180s).

    Discord ogranicza pojedynczą listę rozwijaną do 25 pozycji, więc przy
    dłuższych kolejkach panel dzieli utwory na strony po 25 i pozwala
    przechodzić między nimi przyciskami ◀️/▶️ - dzięki temu widoczne (i
    wybieralne) są WSZYSTKIE utwory w kolejce, nie tylko pierwsze 25."""

    PAGE_SIZE = 25

    def __init__(self, player: AegisPlayer, page: int = 0):
        super().__init__(timeout=180)
        self.player = player
        self.page = page
        self.selected_index: Optional[int] = None
        self._refresh()

    def _tracks(self) -> list:
        return list(self.player.queue)

    def _max_page(self, total: int) -> int:
        return max(0, (total - 1) // self.PAGE_SIZE) if total else 0

    def _refresh(self) -> None:
        """Przebudowuje opcje listy rozwijanej i stan przycisków nawigacji
        pod obecną stronę. WAŻNE: wartość każdej opcji to jej ABSOLUTNY
        indeks w pełnej kolejce (start + i), a nie indeks w obrębie strony -
        inaczej wybór na stronie 2+ trafiałby w zły utwór."""
        tracks = self._tracks()
        total = len(tracks)
        self.page = max(0, min(self.page, self._max_page(total)))
        start = self.page * self.PAGE_SIZE
        page_tracks = tracks[start:start + self.PAGE_SIZE]

        if page_tracks:
            options = [
                discord.SelectOption(
                    label=f"{start + i + 1}. {t.title}"[:100],
                    description=(t.author or "")[:100] or None,
                    value=str(start + i),
                )
                for i, t in enumerate(page_tracks)
            ]
            self.select.disabled = False
            self.select.placeholder = "Wybierz utwór z kolejki..."
        else:
            options = [discord.SelectOption(label="Kolejka jest pusta", value="-1")]
            self.select.disabled = True
            self.select.placeholder = "Brak utworów w kolejce"
        self.select.options = options

        max_page = self._max_page(total)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= max_page

    def build_embed(self, note: str = "") -> discord.Embed:
        total = len(self._tracks())
        pages = self._max_page(total) + 1
        desc = "Wybierz utwór z listy poniżej, a następnie kliknij **Zagraj teraz** albo **Usuń**."
        if pages > 1:
            desc += f"\n-# Strona {self.page + 1}/{pages} • {total} utworów w kolejce."
        embed = info_embed("✏️ Edytuj Kolejkę", desc)
        if note:
            embed.add_field(name="\u200b", value=note, inline=False)
        return embed

    async def _resolve_selected(self, interaction: discord.Interaction) -> Optional[tuple]:
        if self.selected_index is None:
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Najpierw wybierz utwór z listy rozwijanej."), ephemeral=True
            )
            return None
        tracks = self._tracks()
        if self.selected_index >= len(tracks):
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Kolejka zmieniła się w międzyczasie - otwórz panel edycji ponownie."),
                ephemeral=True,
            )
            return None
        return tracks, self.selected_index

    @discord.ui.select(placeholder="Wybierz utwór z kolejki...", row=0)
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.selected_index = int(select.values[0]) if select.values[0] != "-1" else None
        await interaction.response.defer()

    @discord.ui.button(label="Poprzednia", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        self.selected_index = None
        self._refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Następna", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        self.selected_index = None
        self._refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Zagraj teraz", emoji="▶️", style=discord.ButtonStyle.success, row=2)
    async def play_now_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        resolved = await self._resolve_selected(interaction)
        if not resolved:
            return
        tracks, idx = resolved
        target = tracks.pop(idx)
        await Music._rebuild_queue(self.player, [target] + tracks)
        if self.player.playing:
            await self.player.skip(force=True)
        self.selected_index = None
        self._refresh()
        await interaction.response.edit_message(
            embed=self.build_embed(f"✅ Zaraz zagra: **{target.title}**"), view=self
        )

    @discord.ui.button(label="Usuń", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        resolved = await self._resolve_selected(interaction)
        if not resolved:
            return
        tracks, idx = resolved
        removed = tracks.pop(idx)
        await Music._rebuild_queue(self.player, tracks)
        self.selected_index = None
        self._refresh()
        await interaction.response.edit_message(
            embed=self.build_embed(f"🗑️ Usunięto: **{removed.title}**"), view=self
        )


class AudycjaControlView(discord.ui.View):
    """Trwały widok (persistent View) z przyciskiem do włączania/wyłączania powiadomień o audycjach."""

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
        music_cog = interaction.client.get_cog("MusicCog")
        if not music_cog:
            await interaction.response.send_message("❌ Moduł muzyczny nie jest dostępny.", ephemeral=True)
            return
        await music_cog.start_radio_for_interaction(interaction)


class RadioControlView(discord.ui.View):
    """Trwały widok (persistent View) sterowania transmisją na żywo Radia Project FM."""

    def __init__(self, cog: Optional["Music"] = None):
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

    def _get_cog(self, interaction: discord.Interaction) -> Optional["Music"]:
        return self.cog or interaction.client.get_cog("MusicCog")

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

        if isinstance(vc, AegisPlayer):
            vc.cancel_inactivity()
            vc.cancel_progress_updater()
            vc.queue.clear()
        elif vc.is_playing():
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
        if isinstance(vc, AegisPlayer):
            vol = int(vc.volume)
        elif vc and hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
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

        if isinstance(vc, AegisPlayer):
            curr_vol = int(vc.volume)
            vols = [50, 75, 100, 125]
            next_vol = vols[(vols.index(curr_vol) + 1) % len(vols)] if curr_vol in vols else 100
            await vc.set_volume(next_vol)
            await interaction.response.send_message(f"🔊 Głośność radia ustawiona na: **{next_vol}%**", ephemeral=True)
        elif vc and hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
            curr_vol = int(round(vc.source.volume * 100))
            vols = [50, 75, 100, 125]
            next_vol = vols[(vols.index(curr_vol) + 1) % len(vols)] if curr_vol in vols else 100
            vc.source.volume = next_vol / 100.0
            await interaction.response.send_message(f"🔊 Głośność radia ustawiona na: **{next_vol}%**", ephemeral=True)
        else:
            await interaction.response.send_message("Głośność można regulować bezpośrednio na Discordzie (PPM na bota -> Głośność).", ephemeral=True)


class Music(commands.Cog, name="MusicCog"):
    """Główny Cog muzyczny oparty o węzeł Lavalink (wavelink)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ramówka ProjectRPG stoi za Cloudflare (strona "Just a moment..." /
        # JS challenge) - zwykły aiohttp dostaje 403, więc do TEGO JEDNEGO
        # endpointu używamy cloudscraper (biblioteka sync na bazie `requests`,
        # naśladująca przeglądarkę i rozwiązująca wyzwanie Cloudflare).
        # Sync -> odpalane w wątku przez asyncio.to_thread, żeby nie blokować
        # pętli zdarzeń bota.
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
        logger.info("Moduł muzyczny (Lavalink/wavelink) załadowany. Użyj /musicdiag, żeby sprawdzić stan węzła.")

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
                    if isinstance(vc, AegisPlayer):
                        vol = int(vc.volume)
                    elif hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
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
        # usuwamy starą, żeby nie dublować kart na serwerze (dokładnie tak jak ze statusem serwera).
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

    def build_queue_embed(self, player: Optional[AegisPlayer], guild_name: str) -> discord.Embed:
        """Buduje spójny embed kolejki — używany zarówno przez /queue, jak i przycisk 📜."""
        if not player or (not player.current and player.queue.is_empty):
            return info_embed("📜 Kolejka", "Kolejka jest obecnie pusta.\nUżyj `/play`, aby coś dodać!")

        lines = []
        if player.current:
            current = player.current
            duration_seconds = 0 if getattr(current, "is_stream", False) else current.length // 1000
            elapsed_seconds = player.position // 1000
            lines.append("**▶️ Teraz gra**")
            lines.append(f"[{current.title}]({current.uri})")
            lines.append(_progress_bar(elapsed_seconds, duration_seconds))
            lines.append("")

        upcoming = list(player.queue)
        if upcoming:
            lines.append(f"**📋 Następne ({len(upcoming)})**")
            for i, t in enumerate(upcoming[:10], start=1):
                duration = "Na żywo" if getattr(t, "is_stream", False) else _format_duration(t.length // 1000)
                lines.append(f"`{i:>2}.` [{t.title}]({t.uri}) • `{duration}` • {get_requester(t)}")
            if len(upcoming) > 10:
                lines.append(f"-# ...i **{len(upcoming) - 10}** więcej.")
        else:
            lines.append("*Brak kolejnych utworów w kolejce.*")

        embed = info_embed(f"📜 Kolejka — {guild_name}", "\n".join(lines))
        if player.current and getattr(player.current, "artwork", None):
            embed.set_thumbnail(url=player.current.artwork)
        loop_labels = {"off": "Wyłączona", "track": "Jeden utwór", "queue": "Cała kolejka"}
        embed.set_footer(
            text=(
                f"🎵 {len(upcoming)} w kolejce  •  ⏱️ {_sum_duration(upcoming)} łącznie  •  "
                f"🔁 Pętla: {loop_labels[player.loop_mode]}  •  🔊 {int(player.volume)}%"
            )
        )
        return embed

    @staticmethod
    async def _rebuild_queue(player: AegisPlayer, tracks: list) -> None:
        """Czyści kolejkę i wypełnia ją ponownie podaną listą utworów, zachowując
        ich kolejność i wszystkie dynamiczne atrybuty (np. `requested_by`).

        Utwory dodawane są PO JEDNYM (a nie całą listą naraz do `put_wait`) -
        `put_wait` jest udokumentowane i sprawdzone w tym module tylko dla
        pojedynczego `Playable` (patrz `/play`); przekazanie mu gotowej listy
        nie gwarantuje zachowania kolejności 1:1, co właśnie powodowało, że
        `/playnow`, `/queueremove` i `/queuemove` trafiały w zły utwór względem
        pozycji pokazywanej w `/queue`."""
        player.queue.clear()
        for track in tracks:
            await player.queue.put_wait(track)

    async def remove_from_queue(self, player: AegisPlayer, position: int) -> wavelink.Playable:
        """Usuwa utwór z kolejki na podanej (1-indeksowanej) pozycji i zwraca go."""
        tracks = list(player.queue)
        if position < 1 or position > len(tracks):
            raise IndexError(f"Pozycja {position} poza zakresem kolejki (1-{len(tracks)}).")
        removed = tracks.pop(position - 1)
        await self._rebuild_queue(player, tracks)
        return removed

    async def move_in_queue(self, player: AegisPlayer, frm: int, to: int) -> wavelink.Playable:
        """Przenosi utwór z pozycji `frm` na pozycję `to` w kolejce i zwraca go."""
        tracks = list(player.queue)
        if frm < 1 or frm > len(tracks) or to < 1 or to > len(tracks):
            raise IndexError(f"Pozycja poza zakresem kolejki (1-{len(tracks)}).")
        track = tracks.pop(frm - 1)
        tracks.insert(to - 1, track)
        await self._rebuild_queue(player, tracks)
        return track

    async def play_now(self, player: AegisPlayer, position: int) -> wavelink.Playable:
        """Przenosi utwór z podanej pozycji na sam początek kolejki i pomija
        obecnie grający utwór, żeby wybrany utwór zaczął grać natychmiast."""
        tracks = list(player.queue)
        if position < 1 or position > len(tracks):
            raise IndexError(f"Pozycja {position} poza zakresem kolejki (1-{len(tracks)}).")
        target = tracks.pop(position - 1)
        await self._rebuild_queue(player, [target] + tracks)
        if player.playing:
            await player.skip(force=True)
        return target

    async def update_now_playing_message(
        self,
        player: AegisPlayer,
        embed: discord.Embed,
        *,
        view: Optional[discord.ui.View] = None,
    ) -> Optional[discord.Message]:
        """Wysyła kartę 'Teraz Odtwarzane' dla NOWEGO utworu jako najnowszą
        wiadomość na kanale (usuwając poprzednią), zamiast edytować starą
        w miejscu. Dzięki temu po zmianie utworu karta zawsze ląduje na dole
        kanału i jest widoczna bez przewijania, nawet jeśli w międzyczasie
        ktoś napisał w czacie i stara wiadomość "utonęła" wyżej.

        (Sam pasek postępu w obrębie TEGO SAMEGO utworu nadal jest po prostu
        edytowany co PROGRESS_UPDATE_INTERVAL sekund w _start_progress_updater -
        tu chodzi wyłącznie o start nowego utworu.)"""
        if view is None:
            view = MusicControlView()

        if player.now_playing_message:
            try:
                await player.now_playing_message.delete()
            except discord.NotFound:
                pass  # już usunięta - nic się nie dzieje
            except discord.HTTPException as e:
                logger.error(f"Nie udało się usunąć poprzedniej wiadomości 'Teraz Odtwarzane': {e}")
            player.now_playing_message = None

        if not player.text_channel:
            return None
        try:
            player.now_playing_message = await player.text_channel.send(embed=embed, view=view)
            return player.now_playing_message
        except discord.HTTPException as e:
            logger.error(f"Nie udało się wysłać wiadomości 'Teraz Odtwarzane': {e}")
            return None

    def schedule_disconnect(self, player: AegisPlayer, timeout: int = INACTIVITY_TIMEOUT_SECONDS) -> None:
        player.cancel_inactivity()

        async def _disconnect():
            try:
                await asyncio.sleep(timeout)
                if player.connected and not player.playing and not player.paused:
                    text_channel = player.text_channel
                    await player.disconnect()
                    if text_channel:
                        try:
                            await text_channel.send(embed=info_embed("Auto-Rozłączenie 💤", "Bot opuścił kanał z powodu bezczynności."))
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass

        player.inactivity_task = asyncio.create_task(_disconnect())

    def _start_progress_updater(self, player: AegisPlayer) -> None:
        """Uruchamia zadanie, które co PROGRESS_UPDATE_INTERVAL sekund edytuje wiadomość
        'Teraz Odtwarzane', żeby pasek postępu faktycznie się przesuwał zamiast stać na 0:00.

        W przeciwieństwie do wersji FFmpeg (ręczne liczenie czasu przez time.monotonic()),
        tutaj po prostu odczytujemy player.position - Lavalink sam śledzi realną pozycję
        odtwarzania, więc nie trzeba nic liczyć ręcznie."""
        player.cancel_progress_updater()

        async def _updater():
            try:
                while player.connected and (player.playing or player.paused):
                    await asyncio.sleep(PROGRESS_UPDATE_INTERVAL)
                    if not (player.playing or player.paused):
                        break
                    if player.now_playing_message and player.current:
                        radio_url = getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3")
                        is_radio = (
                            getattr(player.current, "is_stream", False)
                            or "panelradiowy.projectrpg.pl" in getattr(player.current, "uri", "")
                            or player.current.uri == radio_url
                        )
                        if is_radio:
                            # Dynamiczne odświeżanie utworu z anteny Radia Project FM
                            title = await fetch_radio_stream_title(radio_url)
                            _, cur_show, _, _ = self._get_schedule_shows()
                            embed = build_radio_embed(
                                current_title=title,
                                current_show=cur_show,
                                volume=int(player.volume)
                            )
                        else:
                            status_label = "⏸️ **Wstrzymano**" if player.paused else "▶️ **Teraz Odtwarzane**"
                            # player.position bywa chwilowo None tuż po (re)synchronizacji
                            # z węzłem Lavalink (np. krótka zrywka sieci przy długich utworach) -
                            # traktujemy to jako 0, zamiast wywalać całe zadanie na TypeError.
                            position_ms = player.position or 0
                            embed = build_track_embed(
                                player.current,
                                elapsed=position_ms // 1000,
                                status_label=status_label,
                            )
                        try:
                            await player.now_playing_message.edit(embed=embed)
                        except discord.NotFound:
                            break  # wiadomość usunięta przez kogoś — przestań próbować
                        except discord.HTTPException:
                            pass  # chwilowy błąd/rate limit — spróbuj przy kolejnym tyknięciu
                        except Exception as e:
                            # Nieoczekiwany błąd nie może po cichu ubić zadania na resztę
                            # utworu (wcześniej właśnie tak pasek/licznik "przestawały działać").
                            logger.error(f"Błąd aktualizacji paska postępu: {e}", exc_info=True)
            except asyncio.CancelledError:
                pass

        player.progress_task = asyncio.create_task(_updater())

    # ==========================================================================
    # Zdarzenia węzła Lavalink
    # ==========================================================================

    @commands.Cog.listener()
    async def on_wavelink_node_disconnected(self, payload: wavelink.NodeDisconnectedEventPayload) -> None:
        """Wywoływane gdy węzeł Lavalink się rozłączy (np. restart węzła, błąd sieci)."""
        logger.error(
            f"Węzeł Lavalink '{payload.node.identifier}' rozłączył się! "
            f"Kod: {getattr(payload, 'code', '?')} | Powód: {getattr(payload, 'reason', 'brak')}"
        )

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: AegisPlayer) -> None:
        """Wywoływane przez Lavalink 4.x gdy odtwarzacz jest nieaktywny przez inactiveThreshold
        (ustawiane w application.yml węzła). Bez tego handlera wavelink automatycznie
        rozłącza bota — to właśnie powoduje 'łączy się i od razu odłącza' gdy Lavalink
        nie może załadować utworu (np. YouTube bez pluginu youtube-source)."""
        logger.warning(
            f"Lavalink zgłosił nieaktywny odtwarzacz na serwerze {player.guild.id if player.guild else '?'}. "
            f"Odtwarzający: {player.playing} | Wstrzymany: {player.paused} | Kolejka: {len(player.queue)}"
        )
        # Jeśli w kolejce jest cokolwiek - spróbuj zagrać
        if not player.queue.is_empty:
            next_track = player.queue.get()
            try:
                await player.play(next_track)
                return
            except Exception as e:
                logger.error(f"on_wavelink_inactive_player: nie udało się zagrać kolejnego: {e}")
        # Nic do grania - rozłącz z komunikatem
        if player.connected:
            text_channel = getattr(player, 'text_channel', None)
            await player.disconnect()
            if text_channel:
                try:
                    await text_channel.send(
                        embed=error_embed(
                            "Błąd Odtwarzania",
                            "Lavalink nie mógł załadować żadnego utworu (węzeł zgłosił nieaktywność). "
                            "Sprawdź czy węzeł Lavalink ma zainstalowane pluginy dla YouTube/Spotify "
                            "(`youtube-source` / `LavaSrc`). Użyj `/musicdiag` do diagnostyki."
                        )
                    )
                except Exception:
                    pass


    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        """Wysyła bogatą kartę 'Teraz Odtwarzane' (embed + przyciski) i startuje
        odświeżanie paska postępu - dla KAŻDEGO startu utworu, poza przypadkami
        jawnie wyciszonymi (patrz suppress_next_announcement)."""
        player: Optional[AegisPlayer] = payload.player  # type: ignore[assignment]
        if player is None:
            return

        if player.suppress_next_announcement:
            player.suppress_next_announcement = False
            return

        if not player.text_channel:
            return

        embed = build_track_embed(payload.track, elapsed=0, status_label="▶️ **Teraz Odtwarzane**")
        await self.update_now_playing_message(player, embed)
        self._start_progress_updater(player)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player: Optional[AegisPlayer] = payload.player  # type: ignore[assignment]
        if player is None:
            return

        player.cancel_progress_updater()
        track = payload.track

        # Tryb pętli: pojedynczy utwór (bez ponownego ogłaszania - to ten sam utwór).
        # Ogłoszenie jest wyciszone, ale pasek postępu MUSI wystartować ręcznie tutaj -
        # inaczej po pierwszym powtórzeniu zamraża się na starej wartości na zawsze
        # (on_wavelink_track_start też pomija start aktualizatora, bo ogłoszenie stłumione).
        if player.loop_mode == "track" and track is not None:
            try:
                player.suppress_next_announcement = True
                await player.play(track)
                self._start_progress_updater(player)
            except Exception as e:
                logger.error(f"Nie udało się powtórzyć utworu '{track.title}': {e}", exc_info=True)
            return

        # Tryb pętli: cała kolejka - odłożenie zakończonego utworu na koniec
        if player.loop_mode == "queue" and track is not None:
            await player.queue.put_wait(track)

        if not player.queue.is_empty:
            next_track = player.queue.get()
            try:
                await player.play(next_track)
            except Exception as e:
                logger.error(f"Nie udało się odtworzyć kolejnego utworu '{next_track.title}': {e}", exc_info=True)
        else:
            self.schedule_disconnect(player)

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        player: Optional[AegisPlayer] = payload.player  # type: ignore[assignment]
        logger.error(f"Błąd odtwarzania na węźle Lavalink: {payload.exception}")
        if player and player.text_channel:
            try:
                message = payload.exception.get("message") if isinstance(payload.exception, dict) else str(payload.exception)
                safe_message = _short_error(Exception(message)) if message else "Nieznany błąd"
                await player.text_channel.send(
                    embed=error_embed("Błąd Odtwarzania", f"Wystąpił błąd podczas odtwarzania utworu:\n`{safe_message}`")
                )
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        player: Optional[AegisPlayer] = payload.player  # type: ignore[assignment]
        logger.warning(f"Utwór 'utknął' na węźle Lavalink (threshold {payload.threshold}ms) - pomijam.")
        if player:
            await player.skip(force=True)

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

        # Jeśli na serwerze był aktywny odtwarzacz Lavalink (AegisPlayer), rozłącz go
        vc = interaction.guild.voice_client
        if vc is not None:
            if isinstance(vc, AegisPlayer):
                vc.cancel_inactivity()
                vc.cancel_progress_updater()
                vc.queue.clear()
                await vc.disconnect()
                await asyncio.sleep(0.3)
                vc = None
            elif vc.is_playing():
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

    @app_commands.command(name="play", description="Odtwarza utwór z podanego linku lub wyszukuje po tytule.")
    @app_commands.describe(query="Tytuł utworu lub link do YouTube/SoundCloud/MP3")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        query_clean = query.strip().lower()
        radio_url = getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3")
        if (
            query_clean in ("radio", "projectfm", "project fm", "radio projectfm")
            or "panelradiowy.projectrpg.pl" in query
            or query.strip() == radio_url
        ):
            await self.start_radio_for_interaction(interaction)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Musisz być połączony z kanałem głosowym!"),
                ephemeral=True
            )
            return

        if not wavelink.Pool.nodes:
            await interaction.response.send_message(
                embed=error_embed("Błąd Konfiguracji", "Bot nie jest połączony z żadnym węzłem Lavalink. Sprawdź `/musicdiag`."),
                ephemeral=True
            )
            return

        channel = interaction.user.voice.channel
        await interaction.response.defer()

        # Jeśli na serwerze grało radio (natywny VoiceClient, a nie AegisPlayer), rozłącz je
        existing_vc = interaction.guild.voice_client
        if existing_vc is not None and not isinstance(existing_vc, AegisPlayer):
            self._cancel_radio_updater(interaction.guild_id)
            if existing_vc.is_playing():
                existing_vc.stop()
            await existing_vc.disconnect()
            await asyncio.sleep(0.3)

        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if player is None:
            try:
                player = await channel.connect(cls=AegisPlayer, self_deaf=True)
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Błąd Połączenia", f"Nie udało się połączyć z kanałem:\n`{_short_error(e)}`"))
                return
        elif player.channel.id != channel.id:
            try:
                await player.move_to(channel)
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Błąd Połączenia", f"Nie udało się przenieść na Twój kanał:\n`{_short_error(e)}`"))
                return

        player.text_channel = interaction.channel
        player.cancel_inactivity()

        try:
            results: wavelink.Search = await wavelink.Playable.search(query)
        except wavelink.LavalinkLoadException as e:
            logger.error(f"Lavalink odmówił załadowania '{query}': {e}", exc_info=True)
            is_url = query.startswith(("http://", "https://"))
            hint = ""
            if "youtube" in query.lower() or "youtu.be" in query.lower():
                hint = "\n\nDla linków **YouTube** węzeł Lavalink wymaga pluginu `youtube-source`. Sprawdź konfigurację węzła."
            elif "spotify" in query.lower() or "open.spotify.com" in query.lower():
                hint = "\n\nDla linków **Spotify** wymagany jest plugin `LavaSrc`. Sprawdź konfigurację węzła."
            if player and not player.playing and player.queue.is_empty:
                await player.disconnect()
            await interaction.followup.send(embed=error_embed(
                "Błąd Ładowania",
                f"Węzeł Lavalink nie mógł załadować tego zasobu:\n`{_short_error(e)}`{hint}"
            ))
            return
        except Exception as e:
            logger.error(f"Błąd wyszukiwania Lavalink dla '{query}': {e}", exc_info=True)
            if player and not player.playing and player.queue.is_empty:
                await player.disconnect()
            await interaction.followup.send(embed=error_embed("Błąd Wyszukiwania", f"Nie udało się odnaleźć utworu:\n`{_short_error(e)}`"))
            return

        if not results:
            is_url = query.startswith(("http://", "https://"))
            hint = ""
            if "youtube" in query.lower() or "youtu.be" in query.lower():
                hint = "\n\nMożliwe, że węzeł Lavalink nie ma pluginu `youtube-source` wymaganego do obsługi YouTube."
            elif "spotify.com" in query.lower():
                hint = "\n\nObsługa **Spotify** wymaga pluginu `LavaSrc` na węźle Lavalink."
            logger.warning(f"Lavalink zwrócił puste wyniki dla: '{query}' (URL: {is_url})")
            if player and not player.playing and player.queue.is_empty:
                await player.disconnect()
            await interaction.followup.send(embed=error_embed(
                "Nie Znaleziono",
                f"Nie znaleziono wyników dla: `{query}`{hint}"
            ))
            return

        was_idle = not player.playing

        if isinstance(results, wavelink.Playlist):
            for track in results.tracks:
                track.requested_by = interaction.user.mention
            added_count = await player.queue.put_wait(results)
            embed = info_embed(
                "📃 Dodano Playlistę",
                f"**{results.name}**\nDodano **{added_count}** utworów do kolejki.",
            )
            embed.color = discord.Color(COLOR_QUEUED)
            await interaction.followup.send(embed=embed)
        else:
            # Zamiast bezrefleksyjnie brać wynik #1 z Lavalink (co dawało czasem
            # mashup/przeróbkę zamiast oryginału - patrz _pick_best_search_result),
            # wybieramy najlepiej dopasowanego kandydata spośród kilku pierwszych.
            # Dla bezpośrednich linków to i tak zwykle jeden wynik, więc nic się
            # tu nie zmienia.
            track = _pick_best_search_result(query, results)
            track.requested_by = interaction.user.mention
            await player.queue.put_wait(track)

            if not was_idle:
                # Bogatszy styl (przeniesiony z wersji yt-dlp): status_label zamiast
                # modyfikacji tytułu, plus szacowany czas do odtworzenia obok pozycji.
                upcoming_ahead = list(player.queue)[:-1]
                wait_time = _sum_duration(upcoming_ahead, mark_uncertain=True) if upcoming_ahead else "0:00"
                embed = build_track_embed(track, status_label="🎶 **Dodano do Kolejki**")
                embed.add_field(name="📍 Pozycja", value=f"#{len(player.queue)}", inline=True)
                embed.add_field(name="⏳ Zagra za (ok.)", value=f"`{wait_time}`", inline=True)
                await interaction.followup.send(embed=embed)

        if was_idle and not player.queue.is_empty:
            next_track = player.queue.get()
            try:
                # Wyciszamy on_wavelink_track_start dla TEGO startu, bo sami zaraz
                # wyślemy bogatą kartę jako odpowiedź na interakcję - unikamy dubla.
                player.suppress_next_announcement = True
                await player.play(next_track)
            except Exception as e:
                player.suppress_next_announcement = False
                await interaction.followup.send(embed=error_embed("Błąd Odtwarzania", f"Nie udało się rozpocząć odtwarzania:\n`{_short_error(e)}`"))
                return

            embed = build_track_embed(next_track, elapsed=0, status_label="▶️ **Teraz Odtwarzane**")
            msg = await interaction.followup.send(embed=embed, view=MusicControlView())
            try:
                player.now_playing_message = await msg.fetch() if not isinstance(msg, discord.Message) else msg
            except Exception:
                player.now_playing_message = None
            self._start_progress_updater(player)

    @app_commands.command(name="skip", description="Pomija aktualnie odtwarzany utwór.")
    async def skip(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if player and player.playing:
            title = player.current.title if player.current else "Utwór"
            if player.loop_mode == "track":
                player.loop_mode = "off"
            await player.skip(force=True)
            await interaction.response.send_message(embed=success_embed("Pominięto ⏭️", f"Pominięto **{title}**."))
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", "Żaden utwór nie jest obecnie odtwarzany."), ephemeral=True)

    @app_commands.command(name="pause", description="Wstrzymuje odtwarzanie utworu.")
    async def pause(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if player and player.playing and not player.paused:
            await player.pause(True)
            await interaction.response.send_message(embed=success_embed("Wstrzymano ⏸️", "Muzyka została wstrzymana."))
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", "Brak aktywnego odtwarzania do wstrzymania."), ephemeral=True)

    @app_commands.command(name="resume", description="Wznawia wstrzymane odtwarzanie utworu.")
    async def resume(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if player and player.paused:
            await player.pause(False)
            await interaction.response.send_message(embed=success_embed("Wznowiono ▶️", "Wznowiono odtwarzanie muzyki."))
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", "Muzyka nie jest wstrzymana."), ephemeral=True)

    @app_commands.command(name="stop", description="Zatrzymuje muzykę/radio, czyści kolejkę i rozłącza bota.")
    async def stop(self, interaction: discord.Interaction) -> None:
        self._cancel_radio_updater(interaction.guild_id)
        old_radio = self._radio_messages.pop(interaction.guild_id, None)
        if old_radio:
            try:
                await old_radio.delete()
            except Exception:
                pass

        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_connected():
            if isinstance(vc, AegisPlayer):
                vc.cancel_inactivity()
                vc.cancel_progress_updater()
                vc.queue.clear()
            elif vc.is_playing():
                vc.stop()
            await interaction.response.send_message(embed=success_embed("Zatrzymano ⏹️", "Odtwarzanie zatrzymane, bot opuścił kanał."))
            await vc.disconnect()
        else:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)

    @app_commands.command(name="queue", description="Wyświetla listę utworów w kolejce.")
    async def queue(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        await interaction.response.send_message(embed=self.build_queue_embed(player, interaction.guild.name))

    @app_commands.command(name="nowplaying", description="Informacje o obecnie odtwarzanym utworze.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or not isinstance(player, AegisPlayer) or not player.current:
            vc = interaction.guild.voice_client
            if vc and vc.is_playing():
                radio_url = getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3")
                current_title = await fetch_radio_stream_title(radio_url)
                _, cur_show, _, _ = self._get_schedule_shows()
                vol = int(round(vc.source.volume * 100)) if hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer) else 100
                embed = build_radio_embed(current_title=current_title, current_show=cur_show, volume=vol)
                await interaction.response.send_message(embed=embed, view=RadioControlView(self))
                return
            await interaction.response.send_message(embed=info_embed("Teraz Gra", "Żaden utwór nie jest obecnie odtwarzany."), ephemeral=True)
            return

        status_label = "⏸️ **Wstrzymano**" if player.paused else "▶️ **Teraz Odtwarzane**"
        embed = build_track_embed(player.current, elapsed=player.position // 1000, status_label=status_label)
        await interaction.response.send_message(embed=embed, view=MusicControlView())

    @app_commands.command(name="volume", description="Ustawia głośność odtwarzacza (1 - 150%).")
    @app_commands.describe(volume="Poziom głośności od 1 do 150")
    async def volume(self, interaction: discord.Interaction, volume: int) -> None:
        if volume < 1 or volume > 150:
            await interaction.response.send_message(embed=error_embed("Błąd", "Wybierz poziom głośności od 1 do 150%."), ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)
            return

        if isinstance(vc, AegisPlayer):
            await vc.set_volume(volume)
        elif hasattr(vc, "source") and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = volume / 100.0

        await interaction.response.send_message(embed=success_embed("Głośność 🔊", f"Ustawiono głośność na **{volume}%**."))

    @app_commands.command(name="clearqueue", description="Czyści listę utworów oczekujących w kolejce.")
    async def clearqueue(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        count = len(player.queue) if player else 0
        if player:
            player.queue.clear()
        await interaction.response.send_message(embed=success_embed("Wyczyszczono Kolejkę 🧹", f"Usunięto **{count}** utworów z kolejki."))

    @app_commands.command(name="shuffle", description="Przetasowuje utwory w kolejce.")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or len(player.queue) < 2:
            await interaction.response.send_message(embed=error_embed("Błąd", "W kolejce musi być przynajmniej 2 utwory."), ephemeral=True)
            return

        player.queue.shuffle()
        await interaction.response.send_message(embed=success_embed("Przetasowano 🔀", f"Wymieszano **{len(player.queue)}** utworów w kolejce."))

    @app_commands.command(name="playnow", description="Odtwarza natychmiast wybrany utwór z kolejki.")
    @app_commands.describe(position="Pozycja utworu w kolejce (sprawdź w /queue)")
    async def playnow(self, interaction: discord.Interaction, position: int) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or not player.connected:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)
            return
        try:
            track = await self.play_now(player, position)
        except IndexError:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nieprawidłowa pozycja. Kolejka ma **{len(player.queue)}** utworów."), ephemeral=True)
            return
        await interaction.response.send_message(embed=success_embed("Zmieniono utwór ⏭️", f"Zaraz zagra: **{track.title}**."))

    @app_commands.command(name="queueremove", description="Usuwa utwór z kolejki na podanej pozycji.")
    @app_commands.describe(position="Pozycja utworu w kolejce (sprawdź w /queue)")
    async def queueremove(self, interaction: discord.Interaction, position: int) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or not player.connected:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)
            return
        try:
            track = await self.remove_from_queue(player, position)
        except IndexError:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nieprawidłowa pozycja. Kolejka ma **{len(player.queue)}** utworów."), ephemeral=True)
            return
        await interaction.response.send_message(embed=success_embed("Usunięto z Kolejki 🗑️", f"Usunięto **{track.title}**."))

    @app_commands.command(name="queuemove", description="Przenosi utwór w kolejce z jednej pozycji na inną.")
    @app_commands.describe(from_position="Aktualna pozycja utworu", to_position="Docelowa pozycja utworu")
    async def queuemove(self, interaction: discord.Interaction, from_position: int, to_position: int) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or not player.connected:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)
            return
        try:
            track = await self.move_in_queue(player, from_position, to_position)
        except IndexError:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nieprawidłowa pozycja. Kolejka ma **{len(player.queue)}** utworów."), ephemeral=True)
            return
        await interaction.response.send_message(embed=success_embed("Przeniesiono 🔀", f"Przeniesiono **{track.title}** na pozycję #{to_position}."))

    @app_commands.command(name="queueedit", description="Otwiera interaktywny panel edycji kolejki.")
    async def queueedit(self, interaction: discord.Interaction) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player or not player.connected:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)
            return
        view = QueueEditView(player)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name="loop", description="Ustawia tryb pętli (wyłączona, jeden utwór, cała kolejka).")
    @app_commands.describe(mode="Tryb pętli")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Wyłączona (off)", value="off"),
        app_commands.Choice(name="Bieżący Utwór (track)", value="track"),
        app_commands.Choice(name="Cała Kolejka (queue)", value="queue")
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        if not player:
            await interaction.response.send_message(embed=error_embed("Błąd", "Bot nie jest połączony z kanałem głosowym."), ephemeral=True)
            return

        player.loop_mode = mode.value
        labels = {"off": "Wyłączona ❌", "track": "Jeden Utwór 🔂", "queue": "Cała Kolejka 🔁"}
        await interaction.response.send_message(embed=success_embed("Tryb Pętli 🔁", f"Ustawiono tryb pętli na: **{labels[mode.value]}**."))

    @app_commands.command(name="musicdiag", description="[Właściciel] Diagnostyka połączenia z węzłem Lavalink na żywo.")
    @is_owner()
    async def musicdiag(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        nodes = list(wavelink.Pool.nodes.values())
        if not nodes:
            embed = create_embed(
                title="🩺 Diagnostyka Modułu Muzycznego",
                description="**Brak skonfigurowanych węzłów Lavalink.** Sprawdź `LAVALINK_HOST` / `LAVALINK_PASSWORD` w `.env`.",
                color=COLOR_IDLE,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        lines = []
        for node in nodes:
            status = str(node.status).split(".")[-1]
            status_icon = "✅" if node.status == wavelink.NodeStatus.CONNECTED else "❌"
            lines.append(f"{status_icon} **{node.identifier}** — `{node.uri}`\n　Status: `{status}` • Graczy: `{len(node.players)}`")

        player: Optional[AegisPlayer] = interaction.guild.voice_client  # type: ignore[assignment]
        player_line = ""
        if player and player.connected:
            player_line = f"\n\n**Ten serwer:** połączony z `{player.node.identifier}`, ping `{player.ping}ms`"

        embed = create_embed(
            title="🩺 Diagnostyka Modułu Muzycznego",
            description="\n\n".join(lines) + player_line,
            color=COLOR_IDLE,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    # Persistent View (timeout=None + stałe custom_id): rejestrujemy raz, żeby
    # przyciski pod starymi wiadomościami "teraz gra" działały nawet po restarcie bota.
    bot.add_view(MusicControlView())