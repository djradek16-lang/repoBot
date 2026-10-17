"""
Dedykowany moduł serwera MTA: ProjectRPG (/prpg).
Zawiera status serwera live (ASE), wyszukiwarkę graczy online, listę graczy z paginacją, zaawansowany kalkulator zarobków z oficjalnymi stawkami z forum (zaktualizowany pod 2026 rok), cennik tuningu aut, taryfikator mandatów SAPD, wzory apelacji na forum oraz informacje o frakcjach.
"""

import socket
import asyncio
import logging
import time
import math
import re
import html as html_lib
import urllib.parse
import difflib
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Any
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# cloudscraper (opcjonalny) — potrafi automatycznie rozwiązać część "klasycznych" wyzwań JS
# Cloudflare (strony "Just a moment..."), które blokują zwykłe zapytania aiohttp kodem 403.
# Biblioteka jest w pełni opcjonalna: jeśli nie jest zainstalowana (pip install cloudscraper),
# ta metoda jest po prostu pomijana i bot działa jak dotychczas (API -> scraping -> DuckDuckGo).
# UWAGA: jeśli forum używa nowszego Cloudflare Turnstile (wymagającego realnej interakcji
# w przeglądarce), cloudscraper NIE pomoże — jedynym pewnym rozwiązaniem pozostaje wtedy
# oficjalny klucz REST API forum (patrz FORUM_API_KEY / config.IPS_API_KEY).
try:
    import cloudscraper
except ImportError:
    cloudscraper = None

from utils.embeds import success_embed, error_embed, info_embed, create_embed
from utils.helpers import format_number
import config

logger = logging.getLogger("AegisBot.Cogs.ProjectRPG")


def _strip_tags(fragment: str) -> str:
    """Usuwa znaczniki HTML z fragmentu tekstu i dekoduje encje (zamiennik BeautifulSoup.get_text())."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_anchors(html_text: str):
    """Generuje (href, tekst_linku) dla każdego <a href="...">...</a> w podanym HTML-u
    (zamiennik BeautifulSoup(html).find_all("a", href=True))."""
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL):
        yield m.group(1), _strip_tags(m.group(2))


def _extract_anchors_with_title(html_text: str):
    """Jak _extract_anchors, ale generuje też atrybut title="..." tagu <a> (albo None,
    jeśli go brak). Motyw forum (Invision Community) konsekwentnie ustawia
    title="Przejdź do profilu {nick}" na linkach do profilu — czysty, bez odznak rangi/gwiazdek
    (★), które w SAMYM tekście linku potrafią psuć dokładne porównanie do nicku
    (patrz _parse_profile_link_from_html)."""
    anchor_re = re.compile(r'<a\b([^>]*)href=["\']([^"\']+)["\']([^>]*)>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    title_re = re.compile(r'title=["\']([^"\']*)["\']', re.IGNORECASE)
    for m in anchor_re.finditer(html_text):
        attrs_before, href, attrs_after, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        title_m = title_re.search(attrs_before) or title_re.search(attrs_after)
        title = html_lib.unescape(title_m.group(1)).strip() if title_m else None
        yield href, _strip_tags(inner), title

SERVER_IP = "s.projectrpg.pl"
SERVER_PORT = 22003
SERVER_ASE_IP = "s.projectrpg.pl"
SERVER_ASE_PORT = 22126
FORUM_URL = "https://projectrpg.pl"
try:
    POLAND_TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    from datetime import timezone, timedelta
    POLAND_TZ = timezone(timedelta(hours=1))

# --- Cennik Tuningu Wizualnego (dane z oficjalnego poradnika forum ProjectRPG) ---
# Źródło: https://projectrpg.pl/topic/71613-tuning-wizualny-wyglad-czesci-ceny-id/
# Format: (ID, cena_w_PLN_lub_None_jesli_nieznana, [lista_kompatybilnych_pojazdow])
VISUAL_TUNING_PARTS: dict[str, list[tuple[int, Optional[int], list[str]]]] = {
    "Spojlery": [
        (1000, 7000, ["Perrenial", "Sentinel", "Manana", "Washington", "Rancher", "Greenwood", "Nebula", "Primo", "Emperor", "Club"]),
        (1001, 9000, ["Bravura", "Sentinel", "Manana", "Cheetah", "Premier", "Previon", "Stallion", "Blista Compact", "Buccaneer", "Vincent", "Tampa", "Sunrise", "Phoenix", "Stafford"]),
        (1002, 6000, ["Perrenial", "Moonbeam", "Rancher", "Blista Compact", "Nebula", "Majestic", "Merit", "Emperor", "Manana"]),
        (1003, 10000, ["Manana", "Bravura", "Cheetah", "Premier", "Previon", "Stallion", "Virgo", "Blista Compact", "Majestic", "Buccaneer", "Primo", "Tampa", "Sunrise", "Merit", "Emperor"]),
        (1014, 5400, ["Sentinel", "Manana", "Washington", "Virgo", "Cadrona", "Clover", "Emperor"]),
        (1015, 4000, ["Nebula", "Cadrona", "Clover", "Manana", "Emperor"]),
        (1016, 4000, ["Perrenial", "Moonbeam", "Manana", "Washington", "Rancher", "Greenwood", "Nebula", "Majestic", "Primo", "Merit", "Emperor", "Club"]),
        (1023, 8500, ["Sentinel", "Manana", "Cheetah", "Washington", "Stallion", "Virgo", "Blista Compact", "Majestic", "Buccaneer", "Tampa", "Sunrise", "Vincent", "Stafford", "Emperor", "Phoenix"]),
        (1050, 3200, ["Flash"]),
        (1058, 3800, ["Stratum"]),
        (1060, 5500, ["Stratum"]),
        (1138, 8700, ["Sultan", "Manana"]),
        (1139, 9500, ["Sultan", "Manana"]),
        (1146, 7800, ["Elegy", "Bravura", "Manana"]),
        (1147, 7500, ["Bravura", "Elegy", "Manana"]),
        (1158, 6000, ["Manana", "Bravura", "Jester"]),
        (1162, 5600, ["Manana", "Jester", "Bravura"]),
        (1163, 11500, ["Manana", "Bravura", "Uranus"]),
    ],
    "Progi": [
        (1007, 2100, ["Bravura", "Manana", "Perrenial", "Cheetah", "Bobcat", "Previon", "Stallion", "ZR-350", "Virgo", "Blista Compact", "Nebula", "Cadrona", "Vincent", "Tampa", "Stafford", "Emperor", "Club", "Picador", "Phoenix"]),
        (1026, 3500, ["Sultan", "Stratum"]),
        (1036, 4000, ["Elegy"]),
        (1039, 3600, ["Elegy"]),
        (1047, 3000, ["Flash"]),
        (1048, 2800, ["Flash"]),
        (1056, 3400, ["Stratum"]),
        (1057, 2800, ["Stratum"]),
        (1069, 3400, ["Jester"]),
        (1090, 2700, ["Uranus"]),
        (1093, 2400, ["Uranus"]),
        (1101, 3200, ["Remington"]),
        (1102, 2300, ["Savanna"]),
        (1106, 2500, ["Remington"]),
        (1107, 2100, ["Blade"]),
        (1118, 2800, ["Slamvan"]),
        (1119, 3300, ["Slamvan"]),
        (1134, 1500, ["Tornado"]),
    ],
    "Wydechy": [
        (1018, 2400, ["Landstalker", "Vincent", "Sentinel", "Club", "Cheetah", "Washington", "ZR-350", "Rancher", "Virgo", "Nebula", "Buccaneer", "Cadrona", "Clover", "Primo", "Tampa", "Sunrise", "Merit", "Emperor", "Picador", "Phoenix"]),
        (1019, 1800, ["Landstalker", "Bravura", "Perrenial", "Manana", "Sentinel", "Cheetah", "Washington", "Bobcat", "Premier", "Previon", "ZR-350", "Rancher", "Virgo", "Blista Compact", "Mesa", "Nebula", "Majestic", "Vincent", "Clover", "Primo", "Tampa", "Sunrise", "Merit", "Emperor", "Phoenix"]),
        (1020, 1600, ["Phoenix", "Landstalker", "Bravura", "Perrenial", "Sentinel", "Manana", "Moonbeam", "Washington", "Bobcat", "Previon", "ZR-350", "Walton", "Rancher", "Virgo", "Blista Compact", "Mesa", "Nebula", "Majestic", "Buccaneer", "Cadrona", "Vincent", "Clover", "Primo", "Tampa", "Sunrise", "Merit"]),
        (1021, 1400, ["Landstalker", "Perrenial", "Sentinel", "Manana", "Moonbeam", "Washington", "Bobcat", "Premier", "Previon", "ZR-350", "Walton", "Virgo", "Mesa", "Nebula", "Cadrona", "Clover", "Primo", "Merit", "Emperor"]),
        (1028, 2400, ["Sultan"]),
        (1034, 2200, ["Elegy"]),
        (1037, 3600, ["Elegy"]),
        (1045, 2800, ["Flash"]),
        (1046, 2100, ["Flash"]),
        (1059, 2700, ["Stratum"]),
        (1064, 1900, ["Stratum"]),
        (1065, 2000, ["Jester"]),
        (1066, 2800, ["Jester"]),
        (1089, 2600, ["Uranus"]),
        (1092, 1800, ["Uranus"]),
        (1104, 2800, ["Blade"]),
        (1105, 3200, ["Blade"]),
        (1113, 2300, ["Slamvan"]),
        (1114, 4100, ["Slamvan"]),
        (1126, 2300, ["Remington"]),
        (1127, 2500, ["Remington"]),
        (1129, 2800, ["Savanna"]),
        (1132, 3400, ["Savanna"]),
        (1135, 2000, ["Tornado"]),
        (1136, 2500, ["Tornado"]),
    ],
    "Zderzaki przednie": [
        (1117, 2800, ["Slamvan"]),
        (1152, 6500, ["Flash"]),
        (1153, 6000, ["Flash"]),
        (1155, 7000, ["Stratum"]),
        (1157, 6500, ["Stratum"]),
        (1160, 7500, ["Jester"]),
        (1165, 5000, ["Uranus"]),
        (1166, 4700, ["Uranus"]),
        (1169, 8500, ["Sultan"]),
        (1170, 7000, ["Sultan"]),
        (1171, 8000, ["Elegy"]),
        (1172, 6500, ["Elegy"]),
        (1173, 5500, ["Jester"]),
        (1179, 3800, ["Remington"]),
        (1181, 3100, ["Blade"]),
        (1182, 3300, ["Blade"]),
        (1188, 3800, ["Savanna"]),
        (1189, 4100, ["Savanna"]),
        (1190, 1800, ["Tornado"]),
        (1191, 2000, ["Tornado"]),
    ],
    "Zderzaki tylne": [
        (1140, 5500, ["Sultan"]),
        (1141, 6000, ["Sultan"]),
        (1148, 5000, ["Elegy"]),
        (1149, 5500, ["Elegy"]),
        (1151, 4000, ["Flash"]),
        (1154, 5000, ["Stratum"]),
        (1156, 4750, ["Stratum"]),
        (1159, 5250, ["Jester"]),
        (1161, 4500, ["Jester"]),
        (1167, 4250, ["Uranus"]),
        (1168, 4000, ["Uranus"]),
        (1178, 2800, ["Remington"]),
        (1180, 3800, ["Remington"]),
        (1183, 3300, ["Blade"]),
        (1184, 3100, ["Blade"]),
        (1186, 4400, ["Savanna"]),
        (1187, 4000, ["Savanna"]),
        (1193, 2600, ["Tornado"]),
    ],
    "Dachy": [
        (1006, 900, ["Bravura", "Moonbeam", "Premier", "Previon", "ZR-350", "Rancher", "Greenwood", "Blista Compact", "Buccaneer", "Vincent", "Sunrise", "Merit", "Emperor", "Picador", "Phoenix"]),
        (1032, 1800, ["Sultan"]),
        (1033, 2600, ["Sultan"]),
        (1035, 2300, ["Elegy"]),
        (1053, 1800, ["Flash"]),
        (1054, 1100, ["Flash"]),
        (1055, 1200, ["Stratum"]),
        (1061, 1500, ["Stratum"]),
        (1067, 1400, ["Jester"]),
        (1068, 2200, ["Jester"]),
        (1088, 1300, ["Uranus"]),
        (1091, 1900, ["Uranus"]),
        (1128, 7000, ["Blade"]),
        (1131, None, ["Savanna"]),
    ],
    "Maski": [
        (1011, 3900, ["Blista Compact", "Tampa"]),
        (1012, 4200, ["Walton", "Tampa"]),
        (1142, 3500, ["Bravura", "Stallion", "Virgo", "Blista Compact", "Buccaneer", "Vincent", "Primo", "Tampa", "Sunrise", "Emperor", "Phoenix"]),
        (1144, 3200, ["Bravura", "Stallion", "Virgo", "Buccaneer", "Vincent", "Tampa", "Sunrise", "Emperor", "Phoenix", "Club", "Clover"]),
    ],
    "Tylne lampy": [
        (1109, 1800, ["Slamvan"]),
        (1110, 1500, ["Slamvan"]),
    ],
    "Przednie lampy": [
        (1115, 2800, ["Slamvan"]),
        (1116, 3500, ["Slamvan"]),
    ],
    "Reflektory": [
        (1013, 500, ["Landstalker", "Bravura", "Perrenial", "Manana", "Bobcat", "Previon", "Stallion", "Walton", "Rancher", "Mesa", "Buccaneer", "Emperor", "Club", "Picador"]),
        (1024, 500, ["Phoenix", "Club", "Vincent", "Mesa", "Rancher", "Walton", "Manana", "Landstalker"]),
    ],
    "Wentyle": [
        (1004, 4200, ["Bravura", "Premier", "Walton", "Rancher", "Greenwood", "Nebula", "Vincent", "Sunrise", "Club", "Picador"]),
        (1005, 3900, ["Picador", "Club", "Merit", "Sunrise", "Buccaneer", "Greenwood", "Rancher", "Walton", "Premier", "Bravura"]),
    ],
}

# Zbiór wszystkich unikalnych nazw pojazdów, dla których istnieją dane tuningu wizualnego
# (używane w autouzupełnianiu komendy /prpg tuning-wizualny).
ALL_TUNABLE_VEHICLES: list[str] = sorted({
    vehicle
    for parts in VISUAL_TUNING_PARTS.values()
    for _id, _cena, vehicles in parts
    for vehicle in vehicles
})


# Nagłówki "przeglądarkowe" dla żądań do forum — bez nich część zapytań (np. /search/)
# bywa odrzucana z kodem 403 przez firewall/CDN forum, który blokuje domyślny,
# łatwo rozpoznawalny User-Agent biblioteki aiohttp ("Python/3.x aiohttp/x.y").
BROWSER_LIKE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# --- Konfiguracja integracji /prpg szukaj (dane gracza: ID, UID, organizacja, służba, reputacja, ping) ---
# Forum (Invision Community): domyślnie profil gracza jest wyszukiwany przez PUBLICZNĄ
# wyszukiwarkę forum (/search/) — nie wymaga żadnych uprawnień administracyjnych ani klucza API.
# Jeśli w przyszłości pojawi się dostęp do ACP -> System -> API, wystarczy ustawić w config.py:
# IPS_API_KEY = "twoj_klucz", a bot automatycznie zacznie korzystać z szybszego, oficjalnego REST API.
FORUM_API_BASE = f"{FORUM_URL}/api"
FORUM_API_KEY = getattr(config, "IPS_API_KEY", None)

# Serwer gry (własny endpoint Lua/fetchRemote) — zwraca ID gracza i ping per-gracz, np.:
# GET http://s.projectrpg.pl:PORT/api/player?nick=NICK -> {"id": 123, "ping": 45}
# Jeśli endpoint zwraca też pole organizacji (np. "organizacja"/"organization"/"org"),
# zostanie ono automatycznie odczytane i użyte przez /prpg organizacja.
# Ustaw w config.py: PRPG_SERVER_API_URL = "http://..." (i opcjonalnie PRPG_SERVER_API_KEY)
SERVER_API_URL = getattr(config, "PRPG_SERVER_API_URL", None)
SERVER_API_KEY = getattr(config, "PRPG_SERVER_API_KEY", None)

# Opcjonalny endpoint ZBIORCZY (jeden request zwraca dane WSZYSTKICH graczy online naraz) —
# znacznie wydajniejszy dla /prpg organizacja niż odpytywanie serwera osobno dla każdego
# gracza. Oczekiwany format JSON: lista obiektów, każdy z polem nicku (np. "nick"/"name")
# i polem organizacji (np. "organizacja"/"organization"/"org"), np.:
# GET http://s.projectrpg.pl:PORT/api/players -> [{"nick": "Gracz1", "organizacja": "Team77", ...}, ...]
# Jeśli nie ustawiony, /prpg organizacja odpytuje SERVER_API_URL osobno dla każdego gracza online.
# Ustaw w config.py: PRPG_SERVER_PLAYERS_API_URL = "http://..."
SERVER_PLAYERS_API_URL = getattr(config, "PRPG_SERVER_PLAYERS_API_URL", None)

# Współdzielony cache wyników zapytań ASE (używany przez /prpg gracze, /prpg szukaj
# oraz autouzupełnianie nicków), żeby nie zasypywać serwera powtarzającymi się zapytaniami UDP.
_ASE_CACHE: dict = {"data": None, "ts": 0.0}
ASE_CACHE_TTL = 8.0  # sekundy

# Cache liczby aktywnych (online) graczy danej organizacji (np. "Team77") — /prpg organizacja.
# Dane pochodzą WYŁĄCZNIE z własnego serwera (patrz SERVER_API_URL / SERVER_PLAYERS_API_URL
# niżej), a nie z forum, więc jest to głównie zabezpieczenie przed zbędnym powtarzaniem
# zapytań HTTP do własnego serwera przy kilku wywołaniach komendy pod rząd.
_ORG_COUNT_CACHE: dict = {}
ORG_COUNT_CACHE_TTL = 15.0  # sekundy
# Ile zapytań do WŁASNEGO serwera (endpoint per-gracz) wysyłamy jednocześnie, gdy nie ma
# skonfigurowanego endpointu zbiorczego (SERVER_PLAYERS_API_URL). To nasz serwer, więc może
# to być wyższe niż przy scrapowaniu forum — nie ma tu ryzyka Cloudflare/rate-limitów.
ORG_COUNT_CONCURRENCY = 10

# Cache przypisania gracza do organizacji i rangi z forum: nick.lower() -> {"org": str, "rank": str, "ts": float}
_PLAYER_ORG_CACHE: dict = {}
PLAYER_ORG_CACHE_TTL = 1800.0  # 30 minut

# Cache pojedynczej instancji cloudscraper: rozwiązanie wyzwania JS Cloudflare trwa
# realnie ok. 1-3 sekundy, więc tworzenie NOWEGO scrapera (i rozwiązywanie wyzwania od zera)
# przy KAŻDYM pojedynczym zapytaniu — jak działało to poprzednio — niepotrzebnie spowalniało
# każde wyszukiwanie i zwiększało ryzyko dodatkowych blokad. Zamiast tego trzymamy jedną
# instancję razem z jej ciasteczkami "cf_clearance" i odświeżamy ją dopiero po
# CLOUDSCRAPER_TTL sekundach (albo natychmiast, jeśli poprzednie żądanie się nie powiodło).
_CLOUDSCRAPER_CACHE: dict = {"scraper": None, "ts": 0.0}
CLOUDSCRAPER_TTL = 900.0  # 15 minut


def _get_cloudscraper():
    """Zwraca współdzieloną, cachowaną instancję cloudscraper (albo None, jeśli biblioteka
    nie jest zainstalowana). Wołające funkcje mogą wymusić odświeżenie (nowe ciasteczka),
    wołając _invalidate_cloudscraper() po nieudanym żądaniu."""
    if cloudscraper is None:
        return None
    now = time.monotonic()
    if _CLOUDSCRAPER_CACHE["scraper"] is None or (now - _CLOUDSCRAPER_CACHE["ts"]) > CLOUDSCRAPER_TTL:
        _CLOUDSCRAPER_CACHE["scraper"] = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        _CLOUDSCRAPER_CACHE["ts"] = now
    return _CLOUDSCRAPER_CACHE["scraper"]


def _invalidate_cloudscraper() -> None:
    """Wymusza utworzenie świeżej instancji cloudscraper przy następnym użyciu (np. gdy
    ostatnie żądanie dostało 403/inny błąd — stare ciasteczka mogły wygasnąć lub zostać
    zablokowane)."""
    _CLOUDSCRAPER_CACHE["ts"] = 0.0


# Rozwiązanie wyzwania Cloudflare przez cloudscraper NIE zawsze wychodzi za pierwszym razem —
# w praktyce bywa to trochę "loteria" (czasem 1. próba wystarczy, czasem trzeba spróbować
# kilka razy ze świeżą sesją, zanim się uda). Dlatego zamiast jednego ponowienia próbujemy
# do CLOUDSCRAPER_MAX_ATTEMPTS razy z rosnącym krótkim odstępem, zawsze na nowej instancji
# scrapera (nowe ciasteczka „cf_clearance” za każdym razem).
CLOUDSCRAPER_MAX_ATTEMPTS = 4
CLOUDSCRAPER_RETRY_DELAY = 1.5  # sekundy, mnożone przez numer próby (1.5s, 3s, 4.5s, ...)

# Znaczniki treści strony wyzwania Cloudflare — czasem taka strona wraca z kodem 200 (nie 403),
# więc samo sprawdzanie status_code nie wystarczy; trzeba też zajrzeć w treść odpowiedzi.
_CLOUDFLARE_CHALLENGE_MARKERS = ("Just a moment", "cf-browser-verification", "Checking your browser", "cf_chl_")


def _looks_like_cloudflare_challenge(html: Optional[str]) -> bool:
    if not html:
        return False
    return any(marker in html for marker in _CLOUDFLARE_CHALLENGE_MARKERS)


def _cloudscraper_get(
    url: str, params: dict | None, headers: dict | None, timeout: int = 20, retry: bool = True
) -> Optional[str]:
    """Synchroniczny helper (uruchamiany w executorze) do pobierania stron przez cloudscraper,
    z automatycznym ponawianiem (do CLOUDSCRAPER_MAX_ATTEMPTS razy, zawsze na świeżej sesji),
    jeśli poprzednia próba się nie powiedzie — czy to przez status != 200, czy przez treść
    strony wyzwania Cloudflare zwróconą mimo statusu 200.

    To odtwarza dokładnie to, co pomagało wcześniej ręcznie: pierwsza próba czasem trafia na
    "zimne" wyzwanie Cloudflare i się nie udaje, ale kolejna (z nowymi ciasteczkami) zwykle
    przechodzi — więc bot próbuje kilka razy sam, automatycznie, zamiast wymagać od
    użytkownika ręcznego ponowienia.
    """
    attempts = CLOUDSCRAPER_MAX_ATTEMPTS if retry else 1
    for attempt in range(1, attempts + 1):
        scraper = _get_cloudscraper()
        if scraper is None:
            return None
        try:
            resp = scraper.get(url, params=params, headers=headers or {}, timeout=timeout)
            if resp.status_code == 200 and not _looks_like_cloudflare_challenge(resp.text):
                return resp.text
            if resp.status_code == 200:
                logger.info(f"cloudscraper: próba {attempt}/{attempts} dla {url} dostała status 200, ale to strona wyzwania Cloudflare.")
            else:
                logger.info(f"cloudscraper: próba {attempt}/{attempts} dla {url} — status {resp.status_code}.")
        except Exception as e:
            logger.info(f"cloudscraper: próba {attempt}/{attempts} dla {url} — błąd: {e}")

        _invalidate_cloudscraper()
        if attempt < attempts:
            time.sleep(CLOUDSCRAPER_RETRY_DELAY * attempt)

    return None


def _build_manual_search_url(query: str) -> str:
    """Buduje bezpośredni link do publicznej wyszukiwarki forum dla danego zapytania
    (nick/login/UID). W przeciwieństwie do pozostałych funkcji w tym pliku, ta funkcja
    NIE wykonuje żadnego żądania sieciowego — po prostu składa URL, więc zawsze się
    powiedzie (nie blokuje jej Cloudflare, bo to tylko link dla użytkownika do kliknięcia,
    a nie automatyczny scraping w imieniu bota).

    Używana jako ostatnia, GWARANTOWANA deska ratunku w _find_forum_profile_url, gdy żadna
    z metod scrapujących (API, /search/ przez aiohttp/cloudscraper, DuckDuckGo, Bing) nie
    znajdzie dokładnego profilu — dzięki temu sztab/moderacja zawsze dostaje przynajmniej
    klikalny link do ręcznego sprawdzenia na forum, zamiast suchego "nie znaleziono" bez
    żadnego linku.
    """
    return f"{FORUM_URL}/search/?q={urllib.parse.quote(query, safe='')}&type=core_members&search_and_or=and"

# Zapamiętuje, w którym kanale ostatnio wywołano /prpg status (per serwer), żeby po
# restarcie bota móc usunąć starą wiadomość i wkleić nową w tym samym miejscu.
STATUS_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prpg_status_state.json")


def _load_status_state() -> dict:
    try:
        with open(STATUS_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"Nie udało się wczytać stanu statusu ProjectRPG: {e}")
        return {}


def _save_status_state(state: dict) -> None:
    try:
        with open(STATUS_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning(f"Nie udało się zapisać stanu statusu ProjectRPG: {e}")



async def _get_ase_data(force_refresh: bool = False) -> dict:
    """Zwraca dane ASE z cache'a (jeśli świeże) lub odpytuje serwer i aktualizuje cache."""
    now = time.monotonic()
    if not force_refresh and _ASE_CACHE["data"] is not None and (now - _ASE_CACHE["ts"]) < ASE_CACHE_TTL:
        return _ASE_CACHE["data"]

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _query_ase_full)
    _ASE_CACHE["data"] = info
    _ASE_CACHE["ts"] = now
    return info


def _normalize_org_name(name: str) -> str:
    """Normalizuje nazwę organizacji do porównań (bez wielkości liter/białych znaków na brzegach)."""
    return re.sub(r"\s+", " ", (name or "")).strip().lower()


def _extract_org_field(payload: dict) -> Optional[str]:
    """Wyciąga nazwę organizacji z odpowiedzi JSON serwera, akceptując kilka wariantów
    nazwy pola (zależnie od tego, jak zwraca to skrypt Lua serwera)."""
    for key in ("organizacja", "organization", "org", "faction_org", "gang"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _extract_nick_field(payload: dict) -> Optional[str]:
    for key in ("nick", "name", "nickname", "player", "username"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


async def _fetch_server_players_bulk() -> Optional[list[dict]]:
    """Pobiera dane WSZYSTKICH graczy online JEDNYM zapytaniem z własnego serwera
    (config.PRPG_SERVER_PLAYERS_API_URL). Zwraca None, jeśli endpoint nie jest skonfigurowany
    albo zapytanie się nie powiodło."""
    if not SERVER_PLAYERS_API_URL:
        return None
    headers = {"Authorization": f"Bearer {SERVER_API_KEY}"} if SERVER_API_KEY else {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SERVER_PLAYERS_API_URL, headers=headers,
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"SERVER_PLAYERS_API_URL zwrócił status {resp.status}.")
                    return None
                data = await resp.json()
                return data if isinstance(data, list) else None
    except Exception as e:
        logger.debug(f"Błąd pobierania zbiorczej listy graczy z serwera: {e}")
        return None


async def _count_online_players_in_organization(
    players: list[str],
    org_name: str,
    guild: Optional[discord.Guild] = None,
    db: Any = None
) -> dict:
    """Sprawdza, ilu spośród PODANYCH (aktualnie online) graczy należy do danej organizacji.

    Priorytety źródeł:
    1. Endpoint zbiorczy serwera (config.PRPG_SERVER_PLAYERS_API_URL), jeśli skonfigurowany.
    2. Endpoint per-gracz serwera (config.PRPG_SERVER_API_URL), jeśli skonfigurowany.
    3. Automatyczna synchronizacja członków organizacji na podstawie Discorda, bazy danych
       (aplikacje / rekrutacja / rangi) oraz weryfikacji profilu forum z inteligentnym cache'em.
    """
    target = _normalize_org_name(org_name)
    matched: list[str] = []
    matched_details: dict[str, str] = {}

    bulk_data = await _fetch_server_players_bulk()
    if bulk_data is not None:
        checked = 0
        online_set = {p.lower() for p in players}
        for entry in bulk_data:
            if not isinstance(entry, dict):
                continue
            nick = _extract_nick_field(entry)
            if not nick:
                continue
            if online_set and nick.lower() not in online_set:
                continue
            checked += 1
            org = _extract_org_field(entry)
            if org and target and target in _normalize_org_name(org):
                matched.append(nick)
                matched_details[nick] = ""
        return {
            "matched": sorted(matched, key=str.lower),
            "matched_details": matched_details,
            "checked": checked,
            "errors": 0,
            "total": len(players),
            "source": "bulk",
        }

    if SERVER_API_URL:
        semaphore = asyncio.Semaphore(ORG_COUNT_CONCURRENCY)
        checked = 0
        errors = 0

        async def _check(nick: str) -> None:
            nonlocal checked, errors
            async with semaphore:
                try:
                    payload = await _fetch_server_player_extra(nick)
                    if payload is None:
                        errors += 1
                        return
                    checked += 1
                    org = _extract_org_field(payload)
                    if org and target and target in _normalize_org_name(org):
                        matched.append(nick)
                        matched_details[nick] = ""
                except Exception as e:
                    errors += 1
                    logger.debug(f"[/prpg organizacja] Błąd zapytania do serwera dla '{nick}': {e}")

        await asyncio.gather(*(_check(p) for p in players))
        return {
            "matched": sorted(matched, key=str.lower),
            "matched_details": matched_details,
            "checked": checked,
            "errors": errors,
            "total": len(players),
            "source": "per_player",
        }

    # --- Tryb autonomiczny: baza danych bota + Discord + forum ProjectRPG ---
    is_team77 = "77" in target or "team77" in target or target in ("team77", "team 77", "77", "")
    known_roster: dict[str, str] = {}  # nick_lower -> source_desc

    if is_team77:
        if db is not None:
            try:
                apps = await db.fetchall("SELECT nick, login FROM applications WHERE status = 'accept'")
                for a in apps:
                    if a.get("nick"):
                        known_roster[a["nick"].strip().lower()] = "rekrutacja"
                    if a.get("login"):
                        known_roster[a["login"].strip().lower()] = "rekrutacja"
            except Exception as e:
                logger.debug(f"Błąd odczytu applications dla /prpg organizacja: {e}")

            try:
                recruits = await db.fetchall("SELECT nick, login FROM recruit_entries")
                for r in recruits:
                    if r.get("nick"):
                        known_roster[r["nick"].strip().lower()] = "rekrutacja"
                    if r.get("login"):
                        known_roster[r["login"].strip().lower()] = "rekrutacja"
            except Exception as e:
                logger.debug(f"Błąd odczytu recruit_entries dla /prpg organizacja: {e}")

            try:
                members = await db.fetchall("SELECT display_name FROM member_names")
                for m in members:
                    if m.get("display_name"):
                        known_roster[m["display_name"].strip().lower()] = "discord"
            except Exception as e:
                logger.debug(f"Błąd odczytu member_names dla /prpg organizacja: {e}")

            try:
                points = await db.fetchall("SELECT nick FROM rank_points WHERE nick IS NOT NULL")
                for p_row in points:
                    if p_row.get("nick"):
                        known_roster[p_row["nick"].strip().lower()] = "staz"
            except Exception:
                pass

        if guild is not None:
            org_role_ids = set()
            if db is not None:
                try:
                    cfg = await db.fetchone(
                        "SELECT applications_accept_role_id, member_count_role_id FROM guild_config WHERE guild_id = ?",
                        (str(guild.id),)
                    )
                    if cfg:
                        if cfg.get("applications_accept_role_id"):
                            org_role_ids.add(int(cfg["applications_accept_role_id"]))
                        if cfg.get("member_count_role_id"):
                            org_role_ids.add(int(cfg["member_count_role_id"]))
                    acc_roles = await db.fetchall(
                        "SELECT role_id FROM application_accept_roles WHERE guild_id = ?",
                        (str(guild.id),)
                    )
                    for ar in acc_roles:
                        if ar.get("role_id"):
                            org_role_ids.add(int(ar["role_id"]))
                    r_thresholds = await db.fetchall(
                        "SELECT role_id FROM rank_thresholds WHERE guild_id = ?",
                        (str(guild.id),)
                    )
                    for rt in r_thresholds:
                        if rt.get("role_id"):
                            org_role_ids.add(int(rt["role_id"]))
                except Exception:
                    pass

            for member in guild.members:
                if member.bot:
                    continue
                has_role = bool(org_role_ids and any(r.id in org_role_ids for r in member.roles))
                if has_role or not org_role_ids:
                    for name_variant in (member.nick, member.display_name, member.name, getattr(member, "global_name", None)):
                        if name_variant:
                            clean_name = re.sub(r"^[\[\(][^\]\)]+[\]\)]\s*", "", name_variant).strip()
                            clean_name = re.sub(r"\s*[\[\(][^\]\)]+[\]\)]$", "", clean_name).strip()
                            known_roster[name_variant.lower()] = "discord"
                            if clean_name:
                                known_roster[clean_name.lower()] = "discord"

    candidates: list[str] = []
    for p in players:
        pl = p.lower()
        if is_team77:
            if (
                pl in known_roster
                or pl.endswith("77")
                or re.search(r"(?i)(?:^|[\W_])(?:team)?77\b|77$", p)
                or "[77]" in pl or "[team77]" in pl or "_77" in pl or "-77" in pl
            ):
                candidates.append(p)
        else:
            tag_pat = f"[{target}]"
            if tag_pat in pl or f"({target})" in pl or pl.startswith(f"{target}_") or pl.endswith(f"_{target}") or target in pl:
                candidates.append(p)
            elif pl in _PLAYER_ORG_CACHE and target in _normalize_org_name(_PLAYER_ORG_CACHE[pl].get("org", "")):
                candidates.append(p)

    checked = 0
    errors = 0
    semaphore = asyncio.Semaphore(5)
    now = time.monotonic()

    async def _verify_candidate(nick: str) -> None:
        nonlocal checked, errors
        nick_key = nick.lower()
        cached = _PLAYER_ORG_CACHE.get(nick_key)
        org = ""
        rank = ""

        if cached is not None and (now - cached.get("ts", 0)) < PLAYER_ORG_CACHE_TTL:
            org = cached.get("org", "")
            rank = cached.get("rank", "")
            checked += 1
        else:
            async with semaphore:
                try:
                    prof = await _find_forum_profile_url(nick, guarantee_link=False, cloudscraper_retry=False)
                    if prof and prof.get("profile_url"):
                        g_info = await _scrape_forum_game_info(prof["profile_url"])
                        if g_info:
                            org = (g_info.get("organizacja") or "").strip()
                            rank = (g_info.get("ranga_organizacji") or g_info.get("ranga_frakcji") or "").strip()
                    _PLAYER_ORG_CACHE[nick_key] = {"org": org, "rank": rank, "ts": now}
                    checked += 1
                except Exception as e:
                    errors += 1
                    logger.debug(f"[/prpg organizacja] Błąd weryfikacji forum dla '{nick}': {e}")

        norm_org = _normalize_org_name(org)
        if is_team77:
            if "team77" in norm_org or "77" in norm_org:
                matched.append(nick)
                matched_details[nick] = rank or "Członek"
            elif not org and nick_key in known_roster:
                matched.append(nick)
                matched_details[nick] = rank or "Członek"
        else:
            if target and target in norm_org:
                matched.append(nick)
                matched_details[nick] = rank or "Członek"

    if candidates:
        await asyncio.gather(*(_verify_candidate(c) for c in candidates))

    return {
        "matched": sorted(matched, key=str.lower),
        "matched_details": matched_details,
        "checked": checked,
        "candidates": len(candidates),
        "errors": errors,
        "total": len(players),
        "source": "roster",
    }


async def _get_organization_count(
    players: list[str],
    org_name: str,
    guild: Optional[discord.Guild] = None,
    db: Any = None,
    force_refresh: bool = False
) -> dict:
    """Zwraca liczbę aktywnych graczy danej organizacji z cache'a (ORG_COUNT_CACHE_TTL) albo
    świeżo policzoną (i cache'owaną) przez _count_online_players_in_organization."""
    key = _normalize_org_name(org_name)
    now = time.monotonic()
    cached = _ORG_COUNT_CACHE.get(key)
    if not force_refresh and cached is not None and (now - cached["ts"]) < ORG_COUNT_CACHE_TTL:
        return cached["data"]

    data = await _count_online_players_in_organization(players, org_name, guild=guild, db=db)
    _ORG_COUNT_CACHE[key] = {"data": data, "ts": now}
    return data


def _rank_matches(players: list[str], query: str, limit: int = 25) -> list[tuple[str, str]]:
    """Sortuje graczy wg trafności dopasowania do zapytania.

    Zwraca listę krotek (nick, poziom_dopasowania) posortowaną: dokładne dopasowanie
    ➔ zaczyna się od zapytania ➔ zawiera zapytanie, wszystko bez rozróżniania wielkości liter.
    """
    q = query.lower().strip()
    exact, starts, contains = [], [], []
    for p in players:
        pl = p.lower()
        if pl == q:
            exact.append(p)
        elif pl.startswith(q):
            starts.append(p)
        elif q in pl:
            contains.append(p)
    ranked = [(p, "exact") for p in exact] + [(p, "starts") for p in starts] + [(p, "contains") for p in contains]
    return ranked[:limit]


def _highlight_match(nick: str, query: str) -> str:
    """Pogrubia fragment nicku odpowiadający zapytaniu wyszukiwania."""
    if not query:
        return f"**{nick}**"
    idx = nick.lower().find(query.lower())
    if idx == -1:
        return f"**{nick}**"
    before, matched, after = nick[:idx], nick[idx:idx + len(query)], nick[idx + len(query):]
    return f"{before}**{matched}**{after}" if (before or after) else f"**{matched}**"


async def _find_forum_profile_url_via_api(nick: str) -> Optional[dict]:
    """Znajduje profil forum (Invision Community) po nicku przez oficjalne REST API.

    Wymaga config.IPS_API_KEY (ACP -> System -> API). Zwraca
    {'id': ..., 'name': ..., 'profile_url': ...} albo None.
    """
    if not FORUM_API_KEY:
        return None
    url = f"{FORUM_API_BASE}/core/members"
    params = {"filter[name]": nick, "perPage": 1}
    auth = aiohttp.BasicAuth(FORUM_API_KEY, "")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, auth=auth, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status != 200:
                    logger.warning(f"IPS API zwróciło status {resp.status} dla nicku '{nick}'.")
                    return None
                data = await resp.json()
        results = data.get("results", [])
        if not results:
            return None
        member = results[0]
        return {
            "id": member.get("id"),
            "name": member.get("name"),
            "profile_url": member.get("profileUrl") or f"{FORUM_URL}/profile/{member.get('id')}-{nick.lower()}/",
        }
    except Exception as e:
        logger.debug(f"Błąd wyszukiwania profilu forum (REST API) dla '{nick}': {e}")
        return None


def _parse_profile_link_from_html(html: str, nick: str) -> Optional[dict]:
    """Wspólny parser: znajduje w podanym HTML-u najlepiej pasujący link do profilu
    w formacie /profile/<id>-<seo-nick>/ dla danego nicku. Używany zarówno przez
    zwykłe zapytanie do wyszukiwarki forum, jak i przez wariant korzystający z cloudscraper.

    Priorytet dopasowania (sprawdzone na realnym HTML-u wyników /search/?type=core_members):
    1) atrybut title="Przejdź do profilu {nick}" na <a> — motyw forum (Invision Community)
       ustawia go konsekwentnie i BEZ żadnych odznak/gwiazdek rangi (★), w przeciwieństwie do
       widocznego tekstu linku, który dla graczy z rangą/premium wygląda np. "★ leiz77" —
       przez co dawne porównanie `link_text.lower() == nick.lower()` nigdy nie trafiało
       dla takich kont i zawsze spadało do słabszego dopasowania "zawiera".
    2) dokładne dopasowanie widocznego tekstu linku do nicku (dla stron/motywów bez title),
    3) tekst linku zawierający nick jako podciąg (najsłabszy, ostatnia deska ratunku).
    """
    if not html:
        return None

    # Odpowiedź wyszukiwarki Invision Community z nagłówkiem XMLHttpRequest przychodzi jako JSON
    # postaci {"filters": "...", "content": "<ul ...>...</ul>"}. Wyciągamy zawartość HTML:
    trimmed = html.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            data = json.loads(trimmed)
            if isinstance(data, dict) and "content" in data:
                html = data["content"]
        except Exception:
            pass

    profile_link_re = re.compile(r"/profile/(\d+)-([^/\"?#]+)/?")
    title_re = re.compile(r"^przejd[zź] do profilu\s+(.+)$", re.IGNORECASE)

    best = None  # (match, name, priorytet) — niższy priorytet = lepsze dopasowanie
    best_priority = 99
    for href, link_text, title in _extract_anchors_with_title(html):
        m = profile_link_re.search(href)
        if not m:
            continue

        if title:
            title_m = title_re.match(title.strip())
            if title_m and title_m.group(1).strip().lower() == nick.lower():
                # Najsilniejszy możliwy sygnał — kończymy od razu, nic go nie przebije.
                return {"id": int(m.group(1)), "name": title_m.group(1).strip(), "profile_url": _absolute_forum_url(m.group(0))}

        if not link_text:
            continue
        if link_text.lower() == nick.lower() and best_priority > 2:
            best, best_priority = (m, link_text), 2
        elif nick.lower() in link_text.lower() and best_priority > 3:
            best, best_priority = (m, link_text), 3

    if best is None:
        return None

    m, name = best
    profile_url = _absolute_forum_url(m.group(0))
    return {"id": int(m.group(1)), "name": name, "profile_url": profile_url}


def _absolute_forum_url(path_or_url: str) -> str:
    """Dokleja FORUM_URL do względnej ścieżki, jeśli podany link nie jest już pełnym adresem."""
    if path_or_url.startswith("http"):
        return path_or_url
    return f"{FORUM_URL}{path_or_url}"


async def _find_forum_profile_url_via_search(nick: str) -> Optional[dict]:
    """Znajduje profil forum po nicku BEZ klucza API — przez publiczną wyszukiwarkę forum.

    Odpytuje stronę wyszukiwania (/search/?type=core_members&q=nick), którą może otworzyć
    każdy odwiedzający forum (bez logowania i bez uprawnień administracyjnych), i wyciąga
    z HTML pierwszy pasujący link do profilu w formacie /profile/<id>-<seo-nick>/.

    Zwraca {'id': ..., 'name': ..., 'profile_url': ...} albo None, jeśli nic nie znaleziono.
    UWAGA: jeśli motyw/wersja forum zmieni strukturę wyników wyszukiwania, może być potrzebne
    dostosowanie selektora niżej — w takim wypadku prześlij surowy HTML strony wyszukiwania.
    """
    search_url = f"{FORUM_URL}/search/"
    params = {"q": nick, "type": "core_members", "search_and_or": "and"}
    # WAŻNE: strona /search/ tego forum (Invision Community) renderuje wyniki wyszukiwania
    # graczy przez AJAX — zwykłe zapytanie GET (bez poniższego nagłówka) zwraca pusty formularz
    # "Szukanie zaawansowane" zamiast listy wyników, więc scraper nigdy nic by nie znalazł.
    # Nagłówek X-Requested-With daje serwerowi znać, że to żądanie AJAX (IPS::Request::isAjax()),
    # dzięki czemu powinien zwrócić właściwy fragment HTML z wynikami.
    ajax_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Referer": f"{FORUM_URL}/search/",
        **BROWSER_LIKE_HEADERS,
    }
    try:
        async with aiohttp.ClientSession() as session:
            # "Rozgrzewka" sesji: część firewalli/CDN (np. Cloudflare) blokuje (403) pierwsze
            # żądanie z danego IP, jeśli nie ma jeszcze ustawionych ciasteczek sesyjnych —
            # odwiedzenie najpierw strony głównej w tej samej sesji pozwala je zebrać, zanim
            # wyślemy właściwe zapytanie do /search/.
            try:
                async with session.get(
                    FORUM_URL, headers=BROWSER_LIKE_HEADERS, timeout=aiohttp.ClientTimeout(total=8)
                ) as warm_resp:
                    await warm_resp.read()
            except Exception as e:
                logger.debug(f"Błąd podczas rozgrzewki sesji forum przed wyszukiwaniem '{nick}': {e}")

            async with session.get(
                search_url, params=params, headers=ajax_headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    body_snippet = (await resp.text())[:200].replace("\n", " ")
                    logger.warning(
                        f"Wyszukiwarka forum zwróciła status {resp.status} dla nicku '{nick}'. "
                        f"Fragment odpowiedzi: {body_snippet!r}"
                    )
                    return None
                html = await resp.text()
    except Exception as e:
        logger.debug(f"Błąd wyszukiwania profilu forum (publiczna wyszukiwarka) dla '{nick}': {e}")
        return None

    if "core_members" not in html and "/profile/" not in html:
        # Nawet z nagłówkiem AJAX serwer mógł zwrócić pełną stronę formularza zamiast wyników
        # (np. jeśli motyw forum wymaga dodatkowo tokenu CSRF albo żądania POST) —
        # w takim wypadku jawnie to logujemy, żeby było widać w logach bota, że trzeba
        # skonfigurować IPS_API_KEY (patrz _find_forum_profile_url_via_api) zamiast polegać na scrapingu.
        logger.debug(f"Wyszukiwarka forum nie zwróciła fragmentu z wynikami dla '{nick}' (być może wymaga API key).")

    return _parse_profile_link_from_html(html, nick)


async def _find_forum_profile_url_via_cloudscraper(nick: str, retry: bool = True) -> Optional[dict]:
    """Wariant wyszukiwarki forum korzystający z biblioteki cloudscraper zamiast aiohttp.

    Używany tylko wtedy, gdy zwykłe zapytanie (_find_forum_profile_url_via_search) dostało 403
    ze stroną wyzwania Cloudflare ("Just a moment..."). cloudscraper potrafi automatycznie
    rozwiązać część klasycznych, statycznych wyzwań JS Cloudflare — ale NIE poradzi sobie
    z nowszym Cloudflare Turnstile, który wymaga realnej interakcji w przeglądarce. Jeśli
    biblioteka nie jest zainstalowana (brak w requirements.txt / brak pip install), funkcja
    od razu zwraca None i bot przechodzi do kolejnej metody (DuckDuckGo).

    `retry=False` wymusza JEDNĄ próbę zamiast CLOUDSCRAPER_MAX_ATTEMPTS — używane przy
    masowym skanowaniu wielu graczy naraz (/prpg organizacja), żeby nie multiplikować
    obciążenia forum (i ryzyka 429) przez wielokrotne ponawianie dla KAŻDEGO gracza.
    """
    if cloudscraper is None:
        return None

    def _sync_request() -> Optional[str]:
        search_url = f"{FORUM_URL}/search/"
        params = {"q": nick, "type": "core_members", "search_and_or": "and"}
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html, */*; q=0.01"}
        return _cloudscraper_get(search_url, params, headers, timeout=20, retry=retry)

    try:
        loop = asyncio.get_running_loop()
        html = await loop.run_in_executor(None, _sync_request)
    except Exception as e:
        logger.info(f"Błąd wyszukiwania profilu forum (cloudscraper) dla '{nick}': {e}")
        return None

    if not html:
        return None

    return _parse_profile_link_from_html(html, nick)


async def _find_forum_profile_url_via_duckduckgo(nick: str) -> Optional[dict]:
    """Ostatnia deska ratunku: szuka profilu forum przez DuckDuckGo (wersja HTML, bez JS),
    gdy nie ma klucza API i publiczna wyszukiwarka forum (/search/) nie zwróciła wyników
    (na tym forum wymaga ona JavaScriptu, więc zwykle zawodzi — patrz _find_forum_profile_url_via_search).

    UWAGA — w przeciwieństwie do REST API forum, to metoda best-effort bez gwarancji trafienia:
    DuckDuckGo może nie mieć zaindeksowanego danego profilu (zwłaszcza kont mało aktywnych/nowych),
    a nawet gdy go ma, może zwrócić wątek WSPOMINAJĄCY dany nick zamiast samej strony profilu —
    dlatego niżej i tak filtrujemy wyniki do linków pasujących dokładnie do /profile/<id>-<nick>/.
    Jeśli to zawiedzie, jedynym w pełni niezawodnym rozwiązaniem pozostaje IPS_API_KEY.
    """
    domain = FORUM_URL.replace("https://", "").replace("http://", "")
    query = f"site:{domain}/profile {nick}"
    ddg_url = "https://html.duckduckgo.com/html/"
    headers = {**BROWSER_LIKE_HEADERS}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ddg_url, params={"q": query}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                # DuckDuckGo (wersja HTML-only, bez JS) potrafi zwrócić 202 Accepted jako
                # normalną, poprawną odpowiedź z wynikami (nie tylko 200) — traktujemy oba
                # kody jako sukces, żeby nie odrzucać poprawnych wyników.
                if resp.status not in (200, 202):
                    logger.warning(f"DuckDuckGo zwróciło status {resp.status} dla nicku '{nick}'.")
                    return None
                html = await resp.text()
    except Exception as e:
        logger.debug(f"Błąd wyszukiwania DuckDuckGo dla '{nick}': {e}")
        return None

    profile_link_re = re.compile(r"/profile/(\d+)-([^/\"?#]+)/?")

    best = None
    for href, _link_text in _extract_anchors(html):
        # DuckDuckGo owija linki wynikowe w przekierowanie postaci
        # //duckduckgo.com/l/?uddg=<zakodowany-url>&... — trzeba je zdekodować, żeby
        # dostać się do prawdziwego adresu profilu na forum.
        real_url = href
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" in qs:
                real_url = urllib.parse.unquote(qs["uddg"][0])

        m = profile_link_re.search(real_url)
        if not m:
            continue
        slug_nick = m.group(2)
        if slug_nick.lower() == nick.lower():
            best = (m, real_url)
            break
        if best is None and nick.lower() in slug_nick.lower():
            best = (m, real_url)

    if best is None:
        return None

    m, real_url = best
    profile_url = real_url if real_url.startswith("http") else f"{FORUM_URL}{real_url}"
    return {"id": int(m.group(1)), "name": nick, "profile_url": profile_url}


async def _find_forum_profile_url_via_bing(nick: str) -> Optional[dict]:
    """Kolejna best-effort deska ratunku: szuka profilu forum przez Bing (bez klucza API,
    HTML wersja wyników), na wypadek gdyby DuckDuckGo nie miał danej strony zaindeksowanej
    (DuckDuckGo bywa słabo zaindeksowany dla mniejszych, niszowych for jak to). Ma dokładnie
    te same zastrzeżenia co wariant DuckDuckGo — to nie jest niezawodna metoda, tylko dodatkowa
    szansa trafienia, gdy oficjalne API i scraping /search/ zawiodą.
    """
    domain = FORUM_URL.replace("https://", "").replace("http://", "")
    query = f"site:{domain}/profile {nick}"
    bing_url = "https://www.bing.com/search"
    headers = {**BROWSER_LIKE_HEADERS}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                bing_url, params={"q": query}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Bing zwróciło status {resp.status} dla zapytania '{query}'.")
                    return None
                html = await resp.text()
    except Exception as e:
        logger.debug(f"Błąd wyszukiwania Bing dla '{nick}': {e}")
        return None

    profile_link_re = re.compile(r"/profile/(\d+)-([^/\"?#]+)/?")

    best = None
    for href, _link_text in _extract_anchors(html):
        # Bing zwykle podaje bezpośrednie URL-e wyników (bez przekierowania jak DuckDuckGo),
        # ale czasem owija je we własny link śledzący (bing.com/ck/a?...) — próbujemy dopasować
        # wzorzec profilu wprost do href; jeśli Bing owinął link, to i tak nic nie znajdziemy
        # (to best-effort, patrz opis funkcji), więc nie ma potrzeby dekodowania takich linków.
        m = profile_link_re.search(href)
        if not m:
            continue
        slug_nick = m.group(2)
        if slug_nick.lower() == nick.lower():
            best = (m, href)
            break
        if best is None and nick.lower() in slug_nick.lower():
            best = (m, href)

    if best is None:
        return None

    m, real_url = best
    profile_url = real_url if real_url.startswith("http") else f"{FORUM_URL}{real_url}"
    return {"id": int(m.group(1)), "name": nick, "profile_url": profile_url}


async def _find_forum_profile_url(nick: str, guarantee_link: bool = True, cloudscraper_retry: bool = True) -> Optional[dict]:
    """Znajduje URL profilu forum po nicku gracza, próbując po kolei coraz mniej pewne metody:

    1. Oficjalne REST API forum (config.IPS_API_KEY) — w pełni niezawodne, ale wymaga
       klucza wygenerowanego w ACP (System -> API), do czego nie każdy ma dostęp.
    2. Ta sama wyszukiwarka, ale przez cloudscraper (jeśli zainstalowany) — potrafi
       automatycznie rozwiązać część klasycznych wyzwań JS Cloudflare, którymi forum chroni
       /search/. Wypróbowywana PRZED zwykłym aiohttp (patrz punkt 3), bo w praktyce zwykłe
       zapytanie prawie zawsze i tak dostaje 403 na tym forum — próbowanie go najpierw tylko
       marnowało czas na każde pojedyncze wyszukiwanie. Best-effort: nie poradzi sobie z
       nowszym Cloudflare Turnstile, który wymaga realnej interakcji w przeglądarce.
    3. Publiczna wyszukiwarka forum (/search/) przez zwykłe aiohttp — szybki fallback na
       wypadek, gdyby akurat Cloudflare nie blokował (albo cloudscraper nie był zainstalowany).
    4. DuckDuckGo, a potem Bing (best-effort, patrz zastrzeżenia w odpowiednich funkcjach).
       WAŻNE: wszystkie metody 1-4 szukają po NAZWIE KONTA na forum — jeśli gracz poda w
       formularzu nick z gry różniący się od swojej nazwy konta forum (co się zdarza — patrz
       pole "Nick" w bloku "Informacje z gry" na profilu, które bywa inne niż login forum),
       żadna z tych metod go nie znajdzie, nawet gdyby Cloudflare wcale nie blokował /search/.
    5. GWARANTOWANA deska ratunku (jeśli guarantee_link=True, domyślnie): gdy nic powyżej nie
       da dokładnego dopasowania, zwracamy link do publicznej wyszukiwarki forum dla podanego
       zapytania (patrz _build_manual_search_url) — to nigdy nie zawodzi, bo nie wymaga żadnego
       żądania sieciowego. Taki wynik ma "exact": False, żeby wołający kod mógł jasno oznaczyć
       go jako niepotwierdzone dopasowanie zamiast pewnego profilu.

    cloudscraper_retry: przekazywane wprost do _find_forum_profile_url_via_cloudscraper.
       Domyślnie True (CLOUDSCRAPER_MAX_ATTEMPTS=4 próby) — ale przy WIELU wywołaniach tej
       funkcji pod rząd w krótkim czasie (np. weryfikacja jednego zgłoszenia po kolei dla
       Nick/Login/UID) te 4 próby na KAŻDE wywołanie potrafią się zsumować do dziesiątek
       żądań do forum w kilkanaście sekund — co w praktyce samo wywołuje blokadę/rate-limit
       (potwierdzone w logach: status 429 od cloudscraper po serii takich wywołań). W takich
       przypadkach wołający powinien przekazać cloudscraper_retry=False (JEDNA próba na
       wywołanie), żeby nie eskalować własnym ruchem blokady, którą i tak trzeba obejść inaczej
       (dłuższy odstęp między zgłoszeniami, IPS_API_KEY zamiast scrapingu).
    """
    if FORUM_API_KEY:
        result = await _find_forum_profile_url_via_api(nick)
        if result:
            result["exact"] = True
            return result
    result = await _find_forum_profile_url_via_cloudscraper(nick, retry=cloudscraper_retry)
    if result:
        result["exact"] = True
        return result
    result = await _find_forum_profile_url_via_search(nick)
    if result:
        result["exact"] = True
        return result
    result = await _find_forum_profile_url_via_duckduckgo(nick)
    if result:
        result["exact"] = True
        return result
    result = await _find_forum_profile_url_via_bing(nick)
    if result:
        result["exact"] = True
        return result

    logger.info(
        f"Nie znaleziono dokładnego profilu forum dla '{nick}' żadną z metod scrapingu "
        f"(API/cloudscraper/aiohttp/DuckDuckGo/Bing)."
        + (" Zwracam link do wyszukiwarki forum jako pewną deskę ratunku." if guarantee_link else "")
    )
    if not guarantee_link:
        return None
    return {
        "id": None,
        "name": nick,
        "profile_url": _build_manual_search_url(nick),
        "exact": False,
    }


async def _fetch_html_bypassing_cloudflare(
    url: str,
    params: dict | None = None,
    extra_headers: dict | None = None,
) -> Optional[str]:
    """Pobiera HTML spod danego adresu forum, próbując najpierw zwykłego, szybkiego aiohttp,
    a jeśli to się nie uda (np. 403 ze stroną wyzwania Cloudflare "Just a moment..."),
    ponawia próbę przez cloudscraper (jeśli zainstalowany — patrz import na górze pliku).

    Zwraca treść HTML albo None, jeśli obie metody zawiodą (albo cloudscraper nie jest
    zainstalowany, a aiohttp też nie przeszedł).
    """
    headers = {**BROWSER_LIKE_HEADERS, **(extra_headers or {})}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                logger.debug(f"aiohttp zwrócił status {resp.status} dla {url} — próbuję cloudscraper.")
    except Exception as e:
        logger.debug(f"Błąd aiohttp podczas pobierania {url}: {e}")

    if cloudscraper is None:
        return None

    def _sync_request() -> Optional[str]:
        return _cloudscraper_get(url, params, extra_headers, timeout=20, retry=True)

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_request)
    except Exception as e:
        logger.info(f"Błąd cloudscraper podczas pobierania {url}: {e}")
        return None


async def _scrape_forum_game_info(profile_url: str) -> Optional[dict]:
    """Pobiera i parsuje blok „Informacje z gry" z publicznej strony profilu forum.

    Zwraca słownik z kluczami: uid, frakcja, ranga_frakcji, organizacja, ranga_organizacji,
    reputacja, punkty_karne — brakujące pola zostają None, jeśli nie odnaleziono ich w HTML.
    """
    result = {
        "uid": None, "frakcja": None, "ranga_frakcji": None,
        "organizacja": None, "ranga_organizacji": None,
        "reputacja": None, "punkty_karne": None,
    }
    html = await _fetch_html_bypassing_cloudflare(profile_url)
    if html is None:
        return None

    # Blok "Informacje z gry" to niestandardowa grupa pól profilowych IPS — zwykle
    # renderowana jako lista <dl>/<dt><dd> lub <ul><li><span class="ipsType_light">Etykieta</span>Wartość</li>.
    # Poniższy parser (oparty wyłącznie na re, bez zewnętrznych bibliotek) jest celowo tolerancyjny
    # na oba warianty; jeśli motyw forum zmieni znaczniki, wystarczy dostosować wzorce poniżej
    # (sprawdzić realny <li>/<dt> na produkcji).
    marker_m = re.search(r"Informacje z gry", html)
    if marker_m is not None:
        # Bierzemy wycinek HTML zaczynający się od nagłówka bloku — z grubsza odpowiadający
        # zawartości kontenera, który w oryginalnym parserze wybierany był po tekście.
        container = html[marker_m.start():marker_m.start() + 12000]
    else:
        # Nagłówek "Informacje z gry" nie został znaleziony w tej dokładnej formie (może być
        # inaczej sformatowany, podzielony na tagi, albo motyw forum używa innej nazwy sekcji).
        # Zamiast od razu się poddawać, próbujemy sparsować pola z CAŁEJ strony — większość
        # niestandardowych pól profilowych IPS i tak occur tylko raz na stronie, więc to zwykle
        # nadal trafi we właściwe wartości (kosztem odrobiny wolniejszego przetwarzania regexów).
        logger.info(
            f"Nie znaleziono nagłówka 'Informacje z gry' na {profile_url} — "
            f"próbuję sparsować pola z całej strony jako fallback."
        )
        container = html

    pairs: list[tuple[str, str]] = []
    dt_dd_re = re.compile(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", re.IGNORECASE | re.DOTALL)
    for dt_html, dd_html in dt_dd_re.findall(container):
        pairs.append((_strip_tags(dt_html), _strip_tags(dd_html)))
    if not pairs:
        li_re = re.compile(r"<li\b[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
        label_re = re.compile(r"<(strong|b|span)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
        for li_html in li_re.findall(container):
            label_m = label_re.search(li_html)
            if not label_m:
                continue
            label = _strip_tags(label_m.group(2))
            value = _strip_tags(li_html).replace(label, "", 1).strip()
            if label and value:
                pairs.append((label, value))

    ranga_seen = 0
    for label, value in pairs:
        norm = label.replace("⤷", "").strip().lower()
        if norm == "uid":
            result["uid"] = value
        elif norm == "frakcja":
            result["frakcja"] = value
        elif norm == "organizacja":
            result["organizacja"] = value
        elif norm == "ranga":
            # Pierwsza napotkana "Ranga" należy do Frakcji, druga do Organizacji (kolejność jak na stronie).
            if ranga_seen == 0:
                result["ranga_frakcji"] = value
            else:
                result["ranga_organizacji"] = value
            ranga_seen += 1
        elif norm in ("reputacja", "rp", "punkty rp", "punkty_rp", "reputation", "punkty reputacji", "ilość reputacji"):
            result["reputacja"] = value
            result["rp_points"] = value
        elif norm in ("punkty karne", "punkty_karne"):
            result["punkty_karne"] = value

    # "Reputacja" i "Punkty Karne" bywają renderowane jako oddzielne bloki (nagłówek + wartość
    # w osobnym elemencie, np. karta statystyk), a nie jako dt/dd czy li z etykietą — potwierdzone
    # na realnym profilu (np. projectrpg.pl/profile/28465-hasiu/): "Reputacja" / "Punkty Karne"
    # jako nagłówek, a wartość liczbowa tuż pod spodem. Dodatkowy fallback tekstowy na taki układ.
    container_text = _strip_tags(container)
    if result["reputacja"] is None:
        m = re.search(r"(?:Reputacja|Punkty\s*RP|Punkty\s*Reputacji|RP)\D{0,10}([\d\s]+)", container_text, re.IGNORECASE)
        if m:
            result["reputacja"] = m.group(1).strip()
            result["rp_points"] = result["reputacja"]
    if result["reputacja"] is None:
        m = re.search(r"\b(?:Reputacja|Punkty\s*RP)\b\D{0,10}([\d\s]+)", _strip_tags(html), re.IGNORECASE)
        if m:
            result["reputacja"] = m.group(1).strip()
            result["rp_points"] = result["reputacja"]
    if result["punkty_karne"] is None:
        m = re.search(r"Punkty Karne\D{0,10}([\d\s]+)", container_text)
        if m:
            result["punkty_karne"] = m.group(1).strip()

    # Ostatnia deska ratunku dla UID: niezależnie od tego, czy powyższy parser dt/dd/li coś
    # znalazł, jeśli UID wciąż nieznane — szukamy wprost wzorca "UID" + liczba w czystym
    # tekście strony (obejmuje to też przypadki, gdy pole UID w ogóle nie jest częścią
    # bloku "Informacje z gry", tylko np. osobnej karty/tabeli).
    if result["uid"] is None:
        page_text = _strip_tags(html)
        m = re.search(r"\bUID\b\D{0,15}(\d{2,10})", page_text, re.IGNORECASE)
        if m:
            result["uid"] = m.group(1).strip()
        else:
            uid_idx = html.upper().find("UID")
            if uid_idx != -1:
                snippet = _strip_tags(html[max(0, uid_idx - 200):uid_idx + 200])
                logger.info(
                    f"[Weryfikacja UID] Nie udało się sparsować numeru UID na {profile_url}, "
                    f"ale strona zawiera fragment w pobliżu słowa 'UID': {snippet!r}"
                )
            else:
                logger.info(
                    f"[Weryfikacja UID] Strona profilu {profile_url} w ogóle nie zawiera słowa 'UID' — "
                    f"struktura strony mogła się zmienić albo pole jest niepubliczne."
                )

    return result


async def _fetch_server_player_extra(nick: str) -> Optional[dict]:
    """Pobiera ID gracza i jego ping z własnego endpointu HTTP serwera MTA (Lua fetchRemote).

    Wymaga config.PRPG_SERVER_API_URL (np. "http://s.projectrpg.pl:22004/api/player").
    Oczekiwany format odpowiedzi JSON: {"id": <int>, "ping": <int>}.
    Dopóki endpoint nie jest skonfigurowany, zwraca None (pola ID/Ping pokazują się jako niedostępne).
    """
    if not SERVER_API_URL:
        return None
    headers = {"Authorization": f"Bearer {SERVER_API_KEY}"} if SERVER_API_KEY else {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SERVER_API_URL, params={"nick": nick}, headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception as e:
        logger.debug(f"Błąd pobierania danych serwera dla '{nick}': {e}")
        return None

# Oficjalna baza danych prac, indywidualnych ulepszeń i ekonomii ProjectRPG (stan: 2026)
JOBS_DATABASE = {
    "kurier": {
        "name": "Kurier RedEx / TransMax",
        "icon": "🚚",
        "location": "Las Venturas (Redsands East) / San Fierro (Garcia)",
        "req": "Prawo Jazdy kat. C • 800 RP",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — TOP]",
        "actions_per_hour": "50 – 70 paczek / h",
        "exp_rp_gain": "30 – 60 EXP / h",
        "base_min": 3400,
        "base_max": 4200,
        "upg_tiers": {
            "tier1": {"label": "P1: Couriero (100% wartości, 90% tempo)", "mult": 1.00, "desc": "Pojazd Couriero, podstawowa ładownia."},
            "tier2": {"label": "P2: Speedvan (110% wartości, 130% tempo)", "mult": 1.25, "desc": "Speedvan (21k $, 4k RP, 2.1k XP)."},
            "tier3": {"label": "P3: Carrier (120% wartości, 160% tempo)", "mult": 1.50, "desc": "Carrier + Sortownik (31.5k $, 8k RP, 3.15k XP)."},
            "tier4": {"label": "P4: Carrier MAX (130% wartości, 200% tempo)", "mult": 1.85, "desc": "Wymaksowany Carrier, 100% dura, ekspres (7.5k - 10k $)."}
        },
        "individual_trees": [
            "🚗 `Typ pojazdu:` Couriero ➔ **Speedvan** *(21k $, 4k RP, 2.1k XP)* ➔ **Carrier** *(31.5k $, 8k RP, 3.15k XP)*",
            "📦 `Wartość przesyłek:` 100% ➔ **110%** *(21k $, 4k RP, 2.1k XP)* ➔ **120%** ➔ **130%**",
            "⚡ `Tempo pracy:` 90% ➔ **130%** *(15k $, 1.8k RP, 1.2k XP)* ➔ **160%** ➔ **200%**",
            "🛡️ `Wytrzymałość pojazdu:` 50% ➔ **75%** *(5k $, 1k RP, 600 XP)* ➔ **100%**",
            "📱 `Sortownik paczek:` Ulepszenie bagażnika & tabletu *(25k $, 3k RP, 1.5k XP)*"
        ],
        "tip": "Praca w CO-OP jest możliwa, lecz nie wpływa znacząco na stawkę. Najważniejsze to tempo 200% i Carrier!"
    },
    "kosiarki": {
        "name": "Koszenie Trawy / Kosiarki",
        "icon": "🌿",
        "location": "Las Venturas (Whitewood / Rockshore) / Angel Pine",
        "req": "Prawo Jazdy kat. B • 300 RP",
        "rating": "⭐⭐⭐⭐☆ [4/5 — Bardzo dobra]",
        "actions_per_hour": "700 – 800 kg trawy / h",
        "exp_rp_gain": "50 – 120 EXP / h • 40 – 60 RP / h",
        "base_min": 3500,
        "base_max": 4200,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Kosiarka standardowa, ~4,200 $ z Premium."},
            "tier2": {"label": "Kosz Powiększony (+25%)", "mult": 1.25, "desc": "Rzadsze zjazdy na opróżnianie kosza."},
            "tier3": {"label": "Silnik Sportowy (+55%)", "mult": 1.55, "desc": "Szybkie manewrowanie po trawnikach."},
            "tier4": {"label": "MAX LVL: Mulczer (+95%)", "mult": 1.95, "desc": "Podwójne noże, zbiór 800 kg/h (~8,200 $ z Premium)."}
        },
        "individual_trees": [
            "🧺 `Pojemność Kosza:` Kosz Standard ➔ Powiększony ➔ Zbiornik XXL",
            "🚜 `Silnik Kosiarki:` Silnik 1.0 ➔ Silnik 1.6 ➔ Silnik Sportowy",
            "✂️ `Noże Tnące:` Noże standardowe ➔ Podwójne ostrza ➔ Listwa mulczująca",
            "⭐ `Bonus Jakości:` Dodatkowa premia za 100% skoszonej posesji"
        ],
        "tip": "Na maksymalnym ulepszeniu kosiarki osiągają ~8,200 $/h, co czyni je jedną z najlepszych prac!"
    },
    "magazynier": {
        "name": "Magazynier (Bayside)",
        "icon": "📦",
        "location": "Bayside Marina (Północ Mapy)",
        "req": "Brak wymagań (0 RP)",
        "rating": "⭐⭐⭐⭐☆ [4/5 — Rekord EXP]",
        "actions_per_hour": "240 – 250 paczek / h",
        "exp_rp_gain": "550 – 600 EXP / h • 50 – 70 RP / h (TOP!)",
        "base_min": 3350,
        "base_max": 3750,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Noszenie ręczne, 4,000 - 4,500 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+15%)", "mult": 1.15, "desc": "Wózek paletowy, 4,600 - 5,200 $ z Premium."},
            "tier3": {"label": "4 LVL (+25%)", "mult": 1.25, "desc": "Wózek widłowy Forklift, 5,000 - 5,600 $ z Premium."},
            "tier4": {"label": "5 LVL MAX (+35%)", "mult": 1.35, "desc": "Wymaksowany poziom pracy, 5,100 - 6,000 $ z Premium."}
        },
        "individual_trees": [
            "📈 `Poziomy Pracy (1–5):` Stały serwerowy bonus +5% gotówki za każdy wbity poziom",
            "🛒 `Narzędzia:` Ręce ➔ Wózek paletowy ➔ Wózek widłowy Forklift",
            "⚡ `Kondycja:` Szybszy chód z paczką i mniejsze zmęczenie"
        ],
        "tip": "Najlepsza praca na start dla nowego gracza ze względu na rekordowy przyrost EXP (do 600/h) i RP!"
    },
    "grzybiarz": {
        "name": "Grzybiarz (Lasy Flint County)",
        "icon": "🍄",
        "location": "Lasy Flint County / Back o Beyond",
        "req": "Brak wymagań (0 RP)",
        "rating": "⭐⭐⭐⭐☆ [4/5 — Stabilny zysk]",
        "actions_per_hour": "~600 grzybów / h",
        "exp_rp_gain": "400 – 500 EXP / h • 10 – 30 RP / h",
        "base_min": 4150,
        "base_max": 4600,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Zbieranie podstawowe: 5,000 - 5,500 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+12%)", "mult": 1.12, "desc": "Większy plecak na grzyby, szybszy zbiór."},
            "tier3": {"label": "4 LVL (+20%)", "mult": 1.20, "desc": "Atlas grzybów, wykrywanie borowików i trufli."},
            "tier4": {"label": "5 LVL MAX (+28%)", "mult": 1.28, "desc": "Wymaksowany poziom: 6,300 - 7,000 $ z Premium."}
        },
        "individual_trees": [
            "🧺 `Ekwipunek:` Koszyk wiklinowy ➔ Plecak ➔ Skrzynia próżniowa",
            "📖 `Ceny w Skupie:` Pieczarka (8.36-9.88$) • Borowik (9.12-10.64$) • Muchomor (6.08-7.60$)"
        ],
        "tip": "Zbieraj głównie borowiki (do 10.64 $ za sztukę) w gęstych lasach Flint County."
    },
    "kutry": {
        "name": "Kutry Rybackie (Morze & Ocean)",
        "icon": "🛥️",
        "location": "Bayside Marina / Port San Fierro",
        "req": "1,500 RP • Licencja na łodzie (W)",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — TOP Profit]",
        "actions_per_hour": "2,300 – 2,500 kg ryb / h (kurs 3.0$ – 3.3$ / kg)",
        "exp_rp_gain": "50 – 70 EXP / h",
        "base_min": 5400,
        "base_max": 5850,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "~2,300 kg ryb/h: 6,500 - 7,000 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+10%)", "mult": 1.10, "desc": "Lepsze sieci, połów ~2,400 kg ryb/h."},
            "tier3": {"label": "4 LVL (+18%)", "mult": 1.18, "desc": "Echosonda, szybsze wyciąganie sieci z wody."},
            "tier4": {"label": "5 LVL MAX (+25%)", "mult": 1.25, "desc": "~2,500 kg ryb/h: 7,500 - 8,500 $ z Premium."}
        },
        "individual_trees": [
            "🎣 `Sieci:` Małe sieci ➔ Sieci głębinowe ➔ Włoki dalekomorskie",
            "🐟 `Cena w skupie:` Średnio 3.00 $ – 3.30 $ za 1 kg złowionych ryb"
        ],
        "tip": "Wypływaj daleko od brzegu i szukaj zagęszczonych ławic na radarze."
    },
    "drwal": {
        "name": "Drwal (Ścinanie & Pielęgnacja)",
        "icon": "🌲",
        "location": "Lasy Whetstone / Shady Creeks / Las Paysadas",
        "req": "700 RP (Pielęgnacja) • 1,300 RP (Ścinanie) • 800 RP (Magazyn)",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — Do 10k $ / h]",
        "actions_per_hour": "750 – 800 akcji pielęgnacji / h",
        "exp_rp_gain": "350 – 400 EXP / h • 15 – 30 RP / h",
        "base_min": 5000,
        "base_max": 6650,
        "upg_tiers": {
            "tier1": {"label": "💧 Pielęgnacja (0 LVL)", "mult": 0.75, "desc": "Pielęgnacja drzew: 4,500 - 5,000 $ z Premium (700 RP)."},
            "tier2": {"label": "💧 Pielęgnacja (5 LVL)", "mult": 1.00, "desc": "Pielęgnacja 5 LVL: 6,000 - 7,000 $ z Premium."},
            "tier3": {"label": "🪓 Ścinanie (0 LVL)", "mult": 1.15, "desc": "Ścinanie drzew: 6,000 - 8,000 $ z Premium (1,300 RP)."},
            "tier4": {"label": "🪓 Ścinanie (5 LVL MAX)", "mult": 1.45, "desc": "Ścinanie 5 LVL: 7,500 - 10,000 $ z Premium!"}
        },
        "individual_trees": [
            "💧 `Pielęgnacja (700 RP):` Podlewanie i okrzesywanie (4,500 - 7,000 $)",
            "🪓 `Ścinanie (1,300 RP):` Piła spalinowa Stihl (6,000 - 10,000 $)",
            "📦 `Magazynowanie (800 RP):` Wywóz Boxville do Las Paysadas (5,000 - 7,000 $)"
        ],
        "tip": "Moduł Ścinania (1300 RP) oferuje jedne z najwyższych stawek w całej grze (do 10k $/h)!"
    },
    "lawety": {
        "name": "Lawety / Transport Kontenerów",
        "icon": "🚛",
        "location": "Las Venturas (Spinybed — Port Towarowy)",
        "req": "Prawo Jazdy kat. C • 2,500 RP • Licencja Transportowa (8,000 $)",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — Mediana 10,100 $]",
        "actions_per_hour": "6 – 10 kursów / h (tryb Solo lub Konwój CO-OP)",
        "exp_rp_gain": "75 – 110 EXP na 1 kurs (wg poziomu silnika)",
        "base_min": 3350,
        "base_max": 4600,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (1 LVL Silnika)", "mult": 1.00, "desc": "6 kursów/h: 4,000 - 5,500 $ z Premium (75-85 EXP/kurs)."},
            "tier2": {"label": "2 LVL Silnika (+25%)", "mult": 1.25, "desc": "Szybszy ciągnik: 5,500 - 7,000 $ z Premium (85-95 EXP/kurs)."},
            "tier3": {"label": "3 LVL Silnika (+45%)", "mult": 1.45, "desc": "8 kursów/h: 6,500 - 8,000 $ z Premium (100-110 EXP/kurs)."},
            "tier4": {"label": "MAX LVL: Przeładowane (+80%)", "mult": 1.80, "desc": "10 kursów/h + ładunki przeładowane: 8,000 - 10,100 $ z Premium!"}
        },
        "individual_trees": [
            "🚜 `Ulepszenie Silnika:` 1 LVL (75 EXP) ➔ 2 LVL (85 EXP) ➔ 3 LVL (100-110 EXP na kurs)",
            "🚛 `Typ Ładunku:` Zwykłe kontenery ➔ Ciężkie ➔ Ładunki przeładowane (+bonus)",
            "👥 `Tryb Konwoju (CO-OP):` Wspólna jazda z innym kierowcą po trasie",
            "📜 `Licencja Transportowa:` Jednorazowy zakup w Urzędzie za 8,000 $"
        ],
        "tip": "Mediana zarobków z forum wynosi aż ~10,100 $/h na MAX ulepszeniu i bonusie TOP 3."
    },
    "szambiarz": {
        "name": "Szambiarz (Wywóz Nieczystości)",
        "icon": "💩",
        "location": "Bone County / Wasteland",
        "req": "Prawo Jazdy kat. C • 800 RP",
        "rating": "⭐⭐⭐⭐☆ [4/5 — Stabilna]",
        "actions_per_hour": "20,000 – 30,000 L / h",
        "exp_rp_gain": "150 – 250 EXP / h • 0 – 10 RP / h",
        "base_min": 4600,
        "base_max": 5400,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Zbiornik 20,000L: 5,500 - 6,500 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+10%)", "mult": 1.10, "desc": "Szybsza pompa ssąca, 6,000 - 7,200 $ z Premium."},
            "tier3": {"label": "4 LVL (+18%)", "mult": 1.18, "desc": "Zbiornik powiększony 25,000L."},
            "tier4": {"label": "5 LVL MAX (+25%)", "mult": 1.25, "desc": "Zbiór 30,000L/h: 7,000 - 8,000 $ z Premium."}
        },
        "individual_trees": [
            "🛢️ `Pojemność Cysterny:` 20,000 L ➔ 25,000 L ➔ 30,000 L",
            "💨 `Wydajność Pompy:` Skrócony czas opróżniania studzienek"
        ],
        "tip": "Wybieraj studzienki o maksymalnym zapełnieniu, aby skrócić czas postoju."
    },
    "dodo": {
        "name": "Dodo — Gaszenie Pożarów",
        "icon": "🛩️",
        "location": "Lotnisko San Fierro / Las Venturas",
        "req": "1,000 RP • Licencja Lotnicza (L)",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — Do 9,000 $]",
        "actions_per_hour": "50 – 60 ugaszonych pożarów / h",
        "exp_rp_gain": "200 – 300 EXP / h • 10 – 20 RP / h",
        "base_min": 5000,
        "base_max": 5750,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "50 pożarów/h: 6,000 - 6,900 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+12%)", "mult": 1.12, "desc": "Szybszy zrzut wody, 6,800 - 7,800 $ z Premium."},
            "tier3": {"label": "4 LVL (+20%)", "mult": 1.20, "desc": "Większy zbiornik na wodę gaśniczą."},
            "tier4": {"label": "5 LVL MAX (+30%)", "mult": 1.30, "desc": "60 pożarów/h: 8,000 - 9,000 $ z Premium!"}
        },
        "individual_trees": [
            "✈️ `Samolot:` Dodo gaśniczy ze zbiornikiem podkadłubowym",
            "🎯 `Precyzja:` Premia za bezpośrednie trafienie w ognisko pożaru"
        ],
        "tip": "Ćwicz niskie przeloty, aby gasić pożary za pierwszym podejściem bez nawrotów."
    },
    "gornik": {
        "name": "Górnik / Kopalnia (Hunter Quarry)",
        "icon": "⛏️",
        "location": "Las Venturas (Hunter Quarry)",
        "req": "4,000 RP • Licencja Górnicza",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — Diamenty]",
        "actions_per_hour": "Wydobycie ciągłe + 4 – 6 diamentów / h",
        "exp_rp_gain": "Zależne od ilości przepalonej rudy",
        "base_min": 3750,
        "base_max": 5000,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Węgiel i miedź: 4,500 - 6,000 $ z Premium."},
            "tier2": {"label": "Wzmacniany Kilof (+25%)", "mult": 1.25, "desc": "Żelazo i srebro, 5,500 - 7,500 $ z Premium."},
            "tier3": {"label": "Piec Hutniczy (+45%)", "mult": 1.45, "desc": "Sztabki złota + 3-4 diamenty/h."},
            "tier4": {"label": "MAX LVL (+70%)", "mult": 1.70, "desc": "Czyste diamenty (4-6 sztuk/h): 7,000 - 10,000 $ z Premium!"}
        },
        "individual_trees": [
            "⛏️ `Narzędzia:` Kilof stalowy ➔ Tytanowy ➔ Diamentowy",
            "💎 `Diamenty:` 4 – 6 diamentów na godzinę przy pełnym ulepszeniu",
            "🔥 `Piec Hutniczy:` Wytapianie sztabek i sprzedaż na górce kursowej"
        ],
        "tip": "Zbieraj surowce i sprzedawaj w skupie dopiero, gdy kurs osiągnie maksymalny poziom!"
    },
    "sweepery": {
        "name": "Sprzątanie Ulic / Sweepery",
        "icon": "🛣️",
        "location": "El Quebrados / Las Venturas / San Fierro",
        "req": "Prawo Jazdy kat. B",
        "rating": "⭐⭐⭐☆☆ [3.5/5 — Spokojna]",
        "actions_per_hour": "800 – 850 kg śmieci / h",
        "exp_rp_gain": "350 – 400 EXP / h • 0 – 10 RP / h",
        "base_min": 3500,
        "base_max": 4150,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "800 kg/h: 4,200 - 5,000 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+12%)", "mult": 1.12, "desc": "Szersze szczotki, 4,800 - 5,600 $ z Premium."},
            "tier3": {"label": "4 LVL (+20%)", "mult": 1.20, "desc": "Mocniejsza turbina, 5,000 - 5,900 $ z Premium."},
            "tier4": {"label": "5 LVL MAX (+28%)", "mult": 1.28, "desc": "850 kg/h: 5,400 - 6,300 $ z Premium."}
        },
        "individual_trees": [
            "🧹 `Szczotki:` Standard ➔ Poszerzone ➔ Podwójne szczotki boczne",
            "🌪️ `Turbina:` Wyższa dopuszczalna prędkość zamiatania"
        ],
        "tip": "Miasteczko El Quebrados jest uważane przez graczy za najszybszą trasę na sweeperach."
    },
    "taxi": {
        "name": "Aplikacja Transportowa (Bolt / Taxi)",
        "icon": "🚕",
        "location": "Cała mapa San Andreas",
        "req": "Własny pojazd 4-osobowy • Moduł aplikacji",
        "rating": "⭐⭐⭐⭐⭐ [5/5 — Rekord]",
        "actions_per_hour": "Kursy miejskie i międzymiastowe",
        "exp_rp_gain": "Zależne od przejechanych kilometrów",
        "base_min": 4150,
        "base_max": 5000,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Zwykła APK)", "mult": 1.00, "desc": "5,000 - 6,000 $ z Premium (bez kosztów paliwa)."},
            "tier2": {"label": "APK Średnia (+30%)", "mult": 1.30, "desc": "Lepsze zlecenia, 6,500 - 8,000 $ z Premium."},
            "tier3": {"label": "APK Zaawansowana (+60%)", "mult": 1.60, "desc": "Zlecenia VIP i międzymiastowe, 8,000 - 10,000 $ z Premium."},
            "tier4": {"label": "APK PRO MAX (+100%)", "mult": 2.00, "desc": "Aplikacja PRO: 9,000 - 12,000 $ z Premium!"}
        },
        "individual_trees": [
            "📱 `Wersja Aplikacji:` Zwykła APK (5-6k $) ➔ APK PRO (9-12k $)",
            "🏎️ `Pojazd:` Wymagany własny 4-drzwiowy samochód z modułem"
        ],
        "tip": "Używaj ekonomicznego pojazdu na LPG, aby zminimalizować koszty paliwa!"
    },
    "wedkarz": {
        "name": "Wędkarz (Brzeg / Łódź)",
        "icon": "🐟",
        "location": "Pomosty, rzeki i jeziora San Andreas",
        "req": "Wędka • Przynęty (Opcjonalnie: własna łódź)",
        "rating": "⭐⭐⭐⭐☆ [4/5 — 36 $/kg]",
        "actions_per_hour": "Połowy ciągłe ryb",
        "exp_rp_gain": "EXP za każdą wyciągniętą rybę",
        "base_min": 3750,
        "base_max": 5000,
        "upg_tiers": {
            "tier1": {"label": "Wędka Zwykła (36$/kg)", "mult": 1.00, "desc": "Podstawowe ryby brzegowe: 4,500 - 5,500 $ z Premium."},
            "tier2": {"label": "Wędka Węglowa (+20%)", "mult": 1.20, "desc": "Mniejsza szansa na zerwanie ryby, 5,500 - 6,500 $ z Premium."},
            "tier3": {"label": "Wędka Morska (+40%)", "mult": 1.40, "desc": "Połowy z własnej łodzi, 6,500 - 7,800 $ z Premium."},
            "tier4": {"label": "Wędka PRO MAX (+60%)", "mult": 1.60, "desc": "Ciężkie okazy morskie: 7,500 - 9,000 $ z Premium!"}
        },
        "individual_trees": [
            "🎣 `Stawka:` Stałe 36 $ za każdy 1 kg ryb",
            "⚠️ `Ryzyko:` Ulepszanie wędki u NPC może spalić poziom przy niepowodzeniu!"
        ],
        "tip": "Wędkarstwo z łodzi na pełnym morzu przynosi znacznie cięższe okazy."
    },
}


def _build_job_embed(job_key: str, tier_key: str = "tier1", premium: bool = True, server_mult: float = 1.0) -> discord.Embed:
    """Tworzy nowoczesny, schludny i czytelny embed kalkulatora zarobków ProjectRPG."""
    job = JOBS_DATABASE.get(job_key, JOBS_DATABASE["kurier"])
    tier_info = job["upg_tiers"].get(tier_key, job["upg_tiers"]["tier1"])

    mult_upg = tier_info["mult"]
    mult_prem = 1.20 if premium else 1.00
    total_multiplier = mult_upg * mult_prem * server_mult

    final_min = int(job["base_min"] * total_multiplier)
    final_max = int(job["base_max"] * total_multiplier)
    avg = int((final_min + final_max) / 2)

    avg_15m = int(avg / 4)
    avg_30m = int(avg / 2)

    embed = discord.Embed(
        title=f"{job['icon']}  {job['name']}",
        color=discord.Color(0x2ecc71) if premium else discord.Color(0x3498db)
    )
    embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")

    # Karta Zysków
    profit_card = (
        f"```fix\n"
        f"💰 ŚREDNI ZAROBEK:  ~{format_number(avg)} $ / godzina\n"
        f"📊 WIDEŁKI STAWEK:  {format_number(final_min)} $ — {format_number(final_max)} $ / h\n"
        f"⏱️ 30 MINUT PRACY:  ~{format_number(avg_30m)} $    •  15 MINUT: ~{format_number(avg_15m)} $\n"
        f"```"
    )
    embed.description = f"### 💼 Szacowane Wynagrodzenie (ProjectRPG)\n{profit_card}"

    # Parametry i Wymagania w zwięzłych polach
    embed.add_field(
        name="📌 Informacje o Pracy",
        value=f"• **Lokalizacja:** `{job['location']}`\n"
              f"• **Wymagania:** `{job['req']}`\n"
              f"• **Ocena Opłacalności:** {job['rating']}",
        inline=False
    )

    embed.add_field(
        name="⚡ Wydajność & Przyrost EXP",
        value=f"• **Średnie tempo:** `{job.get('actions_per_hour', 'Brak')}`\n"
              f"• **Zdobywany EXP/RP:** `{job.get('exp_rp_gain', 'Brak')}`",
        inline=False
    )

    # Indywidualne ulepszenia tej pracy
    upgrades_formatted = "\n".join(job["individual_trees"])
    embed.add_field(
        name="🛠️ Indywidualne Drzewko Ulepszeń w Grze (1:1)",
        value=upgrades_formatted,
        inline=False
    )

    # Tabela poziomów ulepszeń z aktywnym wskaźnikiem
    tier_lines = []
    for k, v in job["upg_tiers"].items():
        t_min = int(job["base_min"] * v["mult"] * mult_prem * server_mult)
        t_max = int(job["base_max"] * v["mult"] * mult_prem * server_mult)
        if k == tier_key:
            tier_lines.append(f"🟢 **{v['label']}** ➔ `~{format_number((t_min + t_max)//2)} $/h` *(Wybrany)*\n　└ *{v['desc']}*")
        else:
            tier_lines.append(f"⚪ **{v['label']}** ➔ `~{format_number((t_min + t_max)//2)} $/h`")

    embed.add_field(
        name="📈 Poziomy Ulepszeń i Przeliczenia",
        value="\n".join(tier_lines),
        inline=False
    )

    # Podsumowanie mnożników i Pro-Tip
    prem_tag = "✅ WŁĄCZONE (+20%)" if premium else "❌ WYŁĄCZONE (1.0x)"
    evt_tag = f"`{server_mult}x`" if server_mult > 1.0 else "`1.0x`"
    embed.add_field(
        name="⚙️ Aktywne Mnożniki",
        value=f"• **Konto Premium:** {prem_tag}  •  **Bonus Eventowy:** {evt_tag}  •  **Łączny Mnożnik:** `x{total_multiplier:.2f}`",
        inline=False
    )

    embed.add_field(
        name="💡 Wskazówka z Forum",
        value=f"> *{job['tip']}*",
        inline=False
    )

    embed.set_footer(
        text="AegisBot • Zweryfikowane Poradniki ProjectRPG Forum (2026)",
        icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png"
    )
    return embed


class JobDashboardView(discord.ui.View):
    """Nowoczesny, interaktywny panel kalkulatora prac."""

    def __init__(self, current_job: str = "kurier", current_tier: str = "tier1", premium: bool = True, server_mult: float = 1.0, user_id: int = 0) -> None:
        super().__init__(timeout=180)
        self.current_job = current_job
        self.current_tier = current_tier
        self.premium = premium
        self.server_mult = server_mult
        self.user_id = user_id

        # Opcje menu z ładnymi ikonami
        job_options = [
            discord.SelectOption(
                label=data["name"][:100],
                value=key,
                emoji=data["icon"],
                default=(key == self.current_job),
                description=f"{data['req']} • {data['rating'][:15]}"[:100]
            )
            for key, data in JOBS_DATABASE.items()
        ]
        self.job_select.options = job_options[:25]
        self._update_button_labels()

    def _update_button_labels(self) -> None:
        self.prem_btn.label = f"Premium (+20%): {'WŁĄCZONE' if self.premium else 'WYŁĄCZONE'}"
        self.prem_btn.style = discord.ButtonStyle.success if self.premium else discord.ButtonStyle.secondary
        self.prem_btn.emoji = "💎" if self.premium else "⚪"

        self.event_btn.label = f"Mnożnik: {self.server_mult}x"
        self.event_btn.style = discord.ButtonStyle.primary if self.server_mult > 1.0 else discord.ButtonStyle.secondary
        self.event_btn.emoji = "🔥"

        job = JOBS_DATABASE.get(self.current_job, JOBS_DATABASE["kurier"])
        tier_info = job["upg_tiers"].get(self.current_tier, job["upg_tiers"]["tier1"])
        tier_short = tier_info['label'].split(':')[0]
        self.tier_btn.label = f"Ulepszenie: {tier_short}"
        self.tier_btn.emoji = "⚡"

    @discord.ui.select(placeholder="📌 Wybierz inną pracę z listy...", min_values=1, max_values=1, custom_id="job_select_menu")
    async def job_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.current_job = select.values[0]
        self.current_tier = "tier1"
        self._update_button_labels()
        for opt in self.job_select.options:
            opt.default = (opt.value == self.current_job)
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Ulepszenie: P1", style=discord.ButtonStyle.secondary, custom_id="job_tier_btn", row=1)
    async def tier_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        tiers = ["tier1", "tier2", "tier3", "tier4"]
        cur_idx = tiers.index(self.current_tier) if self.current_tier in tiers else 0
        self.current_tier = tiers[(cur_idx + 1) % len(tiers)]
        self._update_button_labels()
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Premium (+20%): WŁĄCZONE", style=discord.ButtonStyle.success, custom_id="job_prem_btn", row=1)
    async def prem_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.premium = not self.premium
        self._update_button_labels()
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Mnożnik: 1.0x", style=discord.ButtonStyle.secondary, custom_id="job_event_btn", row=1)
    async def event_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        mults = [1.0, 1.15, 1.30, 1.50, 2.00]
        cur_idx = mults.index(self.server_mult) if self.server_mult in mults else 0
        self.server_mult = mults[(cur_idx + 1) % len(mults)]
        self._update_button_labels()
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)


def _query_ase_full() -> dict:
    """Odpytuje serwer MTA ProjectRPG przez protokół ASE (UDP) i zwraca pełne dane oraz listę graczy online."""
    result = {
        'online': False,
        'players': 0,
        'max_players': 900,
        'name': 'ProjectRPG',
        'gamemode': '▶ ProjectRPG',
        'version': '1.6',
        'ping_ms': 0,
        'player_list': []
    }
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.5)
    try:
        t_start = time.monotonic()
        s.sendto(b's', (SERVER_ASE_IP, SERVER_ASE_PORT))
        data, _ = s.recvfrom(65535)
        result['ping_ms'] = round((time.monotonic() - t_start) * 1000)
        result['online'] = True

        pos = 4  # Pomiń nagłówek 'EYE1'

        def read_field():
            nonlocal pos
            length = data[pos]
            pos += 1
            value = data[pos:pos + length - 1].decode('utf-8', errors='replace')
            pos += length - 1
            return value

        read_field()  # game ("mta")
        read_field()  # port ("22003")
        result['name'] = read_field()
        result['gamemode'] = read_field()
        read_field()  # map
        result['version'] = read_field()
        read_field()  # passworded ("0" / "1")
        players_str = read_field()
        max_players_str = read_field()

        result['players'] = int(players_str) if players_str.isdigit() else 0
        result['max_players'] = int(max_players_str) if max_players_str.isdigit() else 900

        # Wyciągnij listę nicków graczy z pozostałych bajtów odpowiedzi ASE
        remaining = data[pos:]
        player_names = []
        idx = 0
        while idx < len(remaining) - 2:
            if remaining[idx] == 0x01 and remaining[idx + 1] == 0x3f:
                idx += 2
                if idx >= len(remaining):
                    break
                name_len = remaining[idx]
                idx += 1
                if name_len > 1 and idx + name_len - 1 <= len(remaining):
                    raw_name = remaining[idx:idx + name_len - 1].decode('utf-8', errors='replace')
                    idx += name_len - 1
                    if raw_name:
                        clean = re.sub(r'#([a-fA-F0-9]{6})', '', raw_name)
                        clean = re.sub(r'[^\x20-\x7e\u0080-\uffff]', '', clean).strip()
                        if clean and clean not in player_names:
                            player_names.append(clean)
                else:
                    idx += 1
            else:
                idx += 1

        result['player_list'] = player_names
        result['players_list'] = player_names
    except Exception as e:
        logger.debug(f"ASE query error: {e}")
    finally:
        s.close()
    return result


class PlayerFilterModal(discord.ui.Modal, title="🔎 Filtruj listę graczy"):
    """Modal pozwalający na filtrowanie listy graczy bez opuszczania paginatora."""

    query = discord.ui.TextInput(
        label="Fragment nicku",
        placeholder="np. Vercetti — zostaw puste, aby wyczyścić filtr",
        required=False,
        max_length=32
    )

    def __init__(self, paginator: "PlayerListPaginator") -> None:
        super().__init__()
        self.paginator = paginator
        self.query.default = paginator.filter_query or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.paginator.filter_query = self.query.value.strip()
        self.paginator.page = 0
        self.paginator._recompute()
        await interaction.response.edit_message(embed=self.paginator.build_embed(), view=self.paginator)


class PageJumpSelect(discord.ui.Select):
    """Rozwijana lista do natychmiastowego przeskoczenia na wybraną stronę."""

    def __init__(self, paginator: "PlayerListPaginator") -> None:
        self.paginator = paginator
        options = [
            discord.SelectOption(label=f"Strona {i + 1}", value=str(i))
            for i in range(paginator.max_pages)
        ][:25]
        super().__init__(placeholder="⤴️ Przeskocz do strony...", options=options, custom_id="prpg_jump")

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.paginator.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        self.paginator.page = int(self.values[0])
        self.paginator._update_buttons()
        await interaction.response.edit_message(embed=self.paginator.build_embed(), view=self.paginator)


class PlayerListPaginator(discord.ui.View):
    """Nowoczesny paginator listy graczy: sortowanie, filtrowanie na żywo, odświeżanie i szybki skok stron."""

    def __init__(self, players: list[str], total_online: int, max_players: int, ping_ms: int, user_id: int) -> None:
        super().__init__(timeout=120)
        self.all_players = players
        self.total_online = total_online
        self.max_players = max_players
        self.ping_ms = ping_ms
        self.user_id = user_id
        self.page = 0
        self.per_page = 20
        self.sort_mode = "default"  # "default" | "az" | "za"
        self.filter_query = ""
        self.message: Optional[discord.Message] = None
        self._recompute(reset_page=False)

    def _recompute(self, reset_page: bool = True) -> None:
        """Przelicza przefiltrowaną/posortowaną listę graczy i odświeża stan przycisków."""
        players = self.all_players
        if self.filter_query:
            q = self.filter_query.lower()
            players = [p for p in players if q in p.lower()]
        if self.sort_mode == "az":
            players = sorted(players, key=str.lower)
        elif self.sort_mode == "za":
            players = sorted(players, key=str.lower, reverse=True)
        self.players = players
        self.max_pages = max(1, math.ceil(len(players) / self.per_page))
        if reset_page:
            self.page = 0
        self.page = min(self.page, self.max_pages - 1)
        self._update_buttons()
        self._rebuild_jump_select()

    def _rebuild_jump_select(self) -> None:
        # Usuń poprzedni select (jeśli istnieje) i dodaj nowy, dopasowany do liczby stron.
        for item in list(self.children):
            if isinstance(item, PageJumpSelect):
                self.remove_item(item)
        if self.max_pages > 1:
            self.add_item(PageJumpSelect(self))

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= self.max_pages - 1)
        self.page_indicator.label = f"Strona {self.page + 1} / {self.max_pages}"
        sort_labels = {"default": "🔤 Sortuj A-Z", "az": "🔤 Sortuj Z-A", "za": "🔤 Bez sortowania"}
        self.sort_btn.label = sort_labels[self.sort_mode]
        self.filter_btn.style = discord.ButtonStyle.success if self.filter_query else discord.ButtonStyle.secondary
        self.filter_btn.label = f"🔎 Filtr: {self.filter_query}" if self.filter_query else "🔎 Filtruj"

    def build_embed(self) -> discord.Embed:
        fill_pct = int((self.total_online / self.max_players * 100)) if self.max_players else 0
        start = self.page * self.per_page
        end = start + self.per_page
        page_players = self.players[start:end]

        embed = discord.Embed(
            title="👥  Lista Graczy Online — ProjectRPG (MTA:SA)",
            color=discord.Color(0x5865F2)
        )
        embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")

        fill_bar = '█' * (fill_pct // 10) + '░' * (10 - (fill_pct // 10))
        filter_line = f"Filtr           : \"{self.filter_query}\" ({len(self.players)} pasuje)\n" if self.filter_query else ""
        embed.description = (
            f"```yaml\n"
            f"Status Serwera : ONLINE (Ping: {self.ping_ms} ms)\n"
            f"Liczba Graczy  : {self.total_online} / {self.max_players} ({fill_pct}%)\n"
            f"Zapełnienie    : [{fill_bar}]\n"
            f"{filter_line}"
            f"```"
        )

        if page_players:
            half = math.ceil(len(page_players) / 2)
            col1 = [f"`{start + i + 1:02d}.` **{p}**" for i, p in enumerate(page_players[:half])]
            col2 = [f"`{start + half + i + 1:02d}.` **{p}**" for i, p in enumerate(page_players[half:])]

            embed.add_field(name="📋 Gracze (Część 1)", value="\n".join(col1) if col1 else "—", inline=True)
            embed.add_field(name="📋 Gracze (Część 2)", value="\n".join(col2) if col2 else "—", inline=True)
        elif self.filter_query:
            embed.add_field(name="📋 Lista Graczy", value=f"*Brak graczy pasujących do filtra „{self.filter_query}”.*", inline=False)
        else:
            embed.add_field(name="📋 Lista Graczy", value="*Brak graczy na tej stronie.*", inline=False)

        embed.set_footer(text=f"Strona {self.page + 1} z {self.max_pages}  •  Użyj /prpg szukaj <nick>, aby sprawdzić gracza")
        return embed

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="◀ Poprzednia", style=discord.ButtonStyle.secondary, custom_id="prpg_prev", row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Strona 1 / 1", style=discord.ButtonStyle.primary, disabled=True, custom_id="prpg_page_ind", row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="Następna ▶", style=discord.ButtonStyle.secondary, custom_id="prpg_next", row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        if self.page < self.max_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔤 Sortuj A-Z", style=discord.ButtonStyle.secondary, custom_id="prpg_sort", row=1)
    async def sort_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        order = {"default": "az", "az": "za", "za": "default"}
        self.sort_mode = order[self.sort_mode]
        self._recompute()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔎 Filtruj", style=discord.ButtonStyle.secondary, custom_id="prpg_filter", row=1)
    async def filter_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        await interaction.response.send_modal(PlayerFilterModal(self))

    @discord.ui.button(label="🔄 Odśwież", style=discord.ButtonStyle.primary, custom_id="prpg_refresh", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        await interaction.response.defer()
        info = await _get_ase_data(force_refresh=True)
        if not info["online"]:
            await interaction.followup.send(embed=error_embed("Serwer Niedostępny", "Nie udało się połączyć z serwerem ProjectRPG."), ephemeral=True)
            return
        self.all_players = info.get("player_list", [])
        self.total_online = info["players"]
        self.max_players = info["max_players"]
        self.ping_ms = info["ping_ms"]
        self._recompute(reset_page=False)
        await interaction.edit_original_response(embed=self.build_embed(), view=self)


class ProjectRPGCog(commands.Cog):
    """Komendy i narzędzia dedykowane dla graczy serwera MTA: ProjectRPG."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._status_messages: dict[int, discord.Message] = {}
        self._status_fail_counts: dict[int, int] = {}
        self._status_restored = False

    async def cog_load(self) -> None:
        # "Rozgrzewa" cloudscraper zaraz po starcie bota, a potem co CLOUDSCRAPER_WARMUP_INTERVAL
        # minut odświeża sesję w tle — zamiast czekać, aż zrobi to dopiero PIERWSZE realne
        # zapytanie (np. pierwsza aplikacja rekrutacyjna). Dokładnie to ręcznie odtwarzał
        # użytkownik, wchodząc najpierw na forum w przeglądarce przed wysłaniem zgłoszenia:
        # pierwsze trafienie na "zimne" wyzwanie Cloudflare bywa niestabilne (czasem trzeba
        # kilku prób, zanim się uda — patrz CLOUDSCRAPER_MAX_ATTEMPTS w _cloudscraper_get),
        # więc rozgrzewając sesję cyklicznie w tle, ten zawodny "pierwszy strzał" prawie
        # nigdy nie trafia na realne zgłoszenie użytkownika, a cache nie zdąży wygasnąć
        # między jednym a drugim zgłoszeniem.
        if not self._cloudscraper_warmup.is_running():
            self._cloudscraper_warmup.start()

    @tasks.loop(minutes=10)
    async def _cloudscraper_warmup(self) -> None:
        if cloudscraper is None:
            return
        try:
            loop = asyncio.get_running_loop()
            html = await loop.run_in_executor(None, lambda: _cloudscraper_get(FORUM_URL, None, None, timeout=20, retry=True))
            if html:
                logger.info("Rozgrzewka cloudscraper (forum ProjectRPG) zakończona sukcesem — sesja gotowa do użycia.")
            else:
                logger.warning("Rozgrzewka cloudscraper (forum ProjectRPG) nie powiodła się mimo kilku prób — realne wyszukiwania mogą chwilowo zawodzić (bot i tak zawsze zwróci przynajmniej link do wyszukiwarki forum).")
        except Exception as e:
            logger.debug(f"Błąd podczas rozgrzewki cloudscraper: {e}")

    @_cloudscraper_warmup.before_loop
    async def _before_cloudscraper_warmup(self) -> None:
        await self.bot.wait_until_ready()

    async def cog_unload(self) -> None:
        # Zatrzymujemy pętlę rozgrzewki przy odładowaniu coga (np. /owner reload projectrpg),
        # żeby po ponownym załadowaniu nie powstały dwie równoległe pętle rozgrzewające.
        self._cloudscraper_warmup.cancel()


    prpg_group = app_commands.Group(
        name="prpg",
        description="Komendy i kalkulatory dla serwera MTA: ProjectRPG"
    )

    def _build_status_embed(self, info: dict) -> discord.Embed:
        """Buduje nowoczesny embed statusu serwera ProjectRPG z danymi live."""
        if info['online']:
            status_badge = "🟢 ONLINE"
            players = info['players']
            max_players = info['max_players']
            fill_pct = int((players / max_players * 10)) if max_players else 0
            bar = '█' * fill_pct + '░' * (10 - fill_pct)
            players_str = f"`{players}` / `{max_players}`  [{bar}]"
            version = info.get('version', '1.6')
            ping = info.get('ping_ms', 0)
        else:
            status_badge = "🔴 OFFLINE"
            players_str = "Niedostępne"
            version = "1.6"
            ping = 0

        embed = discord.Embed(
            title="🎮  Status Serwera — ProjectRPG (MTA:SA)",
            description=(
                f"```yaml\n"
                f"Status : {status_badge}\n"
                f"Adres  : {SERVER_IP}:{SERVER_PORT}\n"
                f"Wersja : MTA:SA {version}\n"
                f"Ping   : {ping} ms\n"
                f"```"
            ),
            color=discord.Color(0x2ecc71) if info['online'] else discord.Color(0xe74c3c)
        )
        embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")
        embed.add_field(name="👥 Gracze Online", value=players_str, inline=False)
        embed.add_field(
            name="🔗 Szybkie Odnośniki",
            value=f"• [Oficjalne Forum]({FORUM_URL})  •  [Apelacje od Kar]({FORUM_URL}/forum/38-apelacje-od-kar/)  •  [Frakcje]({FORUM_URL}/forum/34-frakcje/)",
            inline=False
        )
        embed.set_footer(text=f"Edytowano: {datetime.now(POLAND_TZ).strftime('%H:%M')} (30s)  •  Ping: {ping} ms")
        return embed

    def _build_status_view(self) -> discord.ui.View:
        """Buduje przycisk 'Odwiedź Forum' używany pod wiadomością statusu."""
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Odwiedź Forum",
            style=discord.ButtonStyle.link,
            url=FORUM_URL,
            emoji="🌐"
        ))
        return view

    def _remember_status_location(self, guild_id: int, channel_id: int, message_id: int) -> None:
        """Zapisuje na dysku, gdzie stoi wiadomość statusu danego serwera (przetrwa restart bota)."""
        state = _load_status_state()
        state[str(guild_id)] = {"channel_id": channel_id, "message_id": message_id}
        _save_status_state(state)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Po (ponownym) połączeniu bota: jeśli /prpg status było gdzieś wywołane wcześniej,
        usuwa starą wiadomość (jeśli jeszcze istnieje) i wkleja nową w tym samym kanale."""
        if self._status_restored:
            return
        self._status_restored = True
        await self._restore_status_messages()

    async def _restore_status_messages(self) -> None:
        state = _load_status_state()
        if not state:
            return

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _query_ase_full)
        embed = self._build_status_embed(info)

        new_state = {}
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
                    logger.warning(f"Nie znaleziono kanału statusu ({channel_id}) dla serwera {gid_str}: {e}")
                    continue

            old_message_id = loc.get("message_id")
            if old_message_id:
                try:
                    old_msg = await channel.fetch_message(int(old_message_id))
                    await old_msg.delete()
                except Exception:
                    pass  # stara wiadomość mogła już nie istnieć — nic się nie stanie

            try:
                new_msg = await channel.send(embed=embed, view=self._build_status_view())
            except Exception as e:
                logger.error(f"Nie udało się przywrócić wiadomości statusu dla serwera {gid_str}: {e}")
                continue

            self._status_messages[guild_id] = new_msg
            new_state[gid_str] = {"channel_id": channel_id, "message_id": new_msg.id}
            logger.info(f"Przywrócono status ProjectRPG po restarcie bota (serwer {gid_str}).")

        _save_status_state(new_state)
        if self._status_messages and not self._refresh_status.is_running():
            self._refresh_status.start()

    FAIL_THRESHOLD = 3  # ile kolejnych nieudanych edycji z rzędu = wiadomość uznana za "martwą"

    @tasks.loop(seconds=30)
    async def _refresh_status(self) -> None:
        """Co 30 sekund odpytuje serwer ASE i edytuje wiadomości statusu.

        Pojedynczy nieudany edit (np. chwilowy rate-limit Discorda) jest ignorowany.
        Dopiero po kilku nieudanych próbach z rzędu wiadomość jest uznawana za
        faktycznie "zawieszoną" — wtedy jest usuwana i tworzona od nowa."""
        if not self._status_messages:
            return
        try:
            info = await asyncio.get_event_loop().run_in_executor(None, _query_ase_full)
            embed = self._build_status_embed(info)
        except Exception as e:
            logger.warning(f"Błąd podczas budowania embeda statusu ProjectRPG: {e}")
            return

        dead = []
        for gid, msg in list(self._status_messages.items()):
            try:
                await msg.edit(embed=embed)
                self._status_fail_counts[gid] = 0
                continue
            except Exception as e:
                fails = self._status_fail_counts.get(gid, 0) + 1
                self._status_fail_counts[gid] = fails
                logger.debug(f"Nieudana edycja statusu ({gid}), próba {fails}/{self.FAIL_THRESHOLD}: {e}")
                if fails < self.FAIL_THRESHOLD:
                    continue  # to mogła być chwilowa usterka — spróbujemy znów za 30s

            logger.warning(f"Wiadomość statusu ({gid}) zawiesiła się/zniknęła, tworzę nową.")
            self._status_fail_counts[gid] = 0

            channel = msg.channel
            try:
                await msg.delete()
            except Exception:
                pass  # już mogła nie istnieć — nic się nie stanie

            try:
                new_msg = await channel.send(embed=embed, view=self._build_status_view())
                self._status_messages[gid] = new_msg
                self._remember_status_location(gid, channel.id, new_msg.id)
            except Exception as e:
                logger.error(f"Nie udało się utworzyć nowej wiadomości statusu dla {gid}: {e}")
                dead.append(gid)

        for gid in dead:
            del self._status_messages[gid]
            self._status_fail_counts.pop(gid, None)

    @_refresh_status.error
    async def _refresh_status_error(self, error: Exception) -> None:
        """Łapie każdy nieoczekiwany błąd pętli, żeby task.loop się nie zatrzymał na stałe."""
        logger.error(f"Pętla odświeżania statusu ProjectRPG napotkała błąd: {error}", exc_info=error)
        if not self._refresh_status.is_running():
            self._refresh_status.restart()

    @_refresh_status.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    @prpg_group.command(name="status", description="Wyświetla aktualny status serwera MTA: ProjectRPG z liczbą graczy (odświeżanie co 30s).")
    async def prpg_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _query_ase_full)

        embed = self._build_status_embed(info)

        # Wysyłane jako zwykła wiadomość na kanał (nie jako odpowiedź na interakcję),
        # żeby nie pojawiał się nad nią napis "użytkownik użył /prpg status".
        msg = await interaction.channel.send(embed=embed, view=self._build_status_view())

        # Cicho kończymy interakcję (widoczne tylko dla osoby, która użyła komendy).
        await interaction.followup.send("Gotowe ✅", ephemeral=True)

        self._status_messages[interaction.guild_id] = msg
        self._remember_status_location(interaction.guild_id, interaction.channel_id, msg.id)
        if not self._refresh_status.is_running():
            self._refresh_status.start()

    @prpg_group.command(name="gracze", description="Wyświetla interaktywną listę wszystkich graczy aktualnie połączonych z serwerem ProjectRPG.")
    async def prpg_gracze(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        info = await _get_ase_data()

        if not info['online']:
            await interaction.followup.send(embed=error_embed("Serwer Niedostępny", "Nie udało się połączyć z serwerem ProjectRPG."))
            return

        players = info.get('player_list', [])
        if not players:
            embed = info_embed(
                "👥 Gracze Online — ProjectRPG",
                f"Serwer jest online (`{info['players']}` graczy), lecz lista nazwisk jest obecnie przetwarzana przez protokół ASE."
            )
            await interaction.followup.send(embed=embed)
            return

        paginator = PlayerListPaginator(
            players=players,
            total_online=info['players'],
            max_players=info['max_players'],
            ping_ms=info['ping_ms'],
            user_id=interaction.user.id
        )
        msg = await interaction.followup.send(embed=paginator.build_embed(), view=paginator, wait=True)
        paginator.message = msg

    @prpg_group.command(name="organizacja", description="Pokazuje, ilu graczy online aktualnie należy do danej organizacji (domyślnie Team77).")
    @app_commands.describe(nazwa="Nazwa organizacji do sprawdzenia (domyślnie: Team77)")
    async def prpg_organizacja(self, interaction: discord.Interaction, nazwa: Optional[str] = "Team77") -> None:
        await interaction.response.defer()

        nazwa = (nazwa or "Team77").strip() or "Team77"

        info = await _get_ase_data()
        if not info['online']:
            await interaction.followup.send(embed=error_embed("Serwer Niedostępny", "Nie udało się połączyć z serwerem ProjectRPG."))
            return

        players = info.get('player_list', [])
        if not players:
            embed = info_embed(
                "🏢 Organizacja — ProjectRPG",
                f"Serwer jest online (`{info['players']}` graczy), lecz lista nazwisk jest obecnie przetwarzana "
                f"przez protokół ASE — spróbuj ponownie za chwilę."
            )
            await interaction.followup.send(embed=embed)
            return

        result = await _get_organization_count(players, nazwa, guild=interaction.guild, db=self.bot.db)

        matched = result["matched"]
        matched_details = result.get("matched_details", {})

        embed = discord.Embed(
            title=f"🏢 Organizacja: {nazwa}",
            color=discord.Color(0x5865F2)
        )
        embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")
        embed.description = (
            f"```yaml\n"
            f"Aktywnych online : {len(matched)}\n"
            f"Serwer ProjectRPG: {result['total']} graczy online\n"
            f"```"
        )

        if matched:
            listing_lines = []
            for i, n in enumerate(matched[:50]):
                r = matched_details.get(n)
                if r:
                    listing_lines.append(f"`{i + 1:02d}.` **{n}** *(⤷ {r})*")
                else:
                    listing_lines.append(f"`{i + 1:02d}.` **{n}**")
            listing = "\n".join(listing_lines)
            embed.add_field(name="📋 Gracze online z tej organizacji", value=listing, inline=False)
            if len(matched) > 50:
                embed.add_field(name="\u200b", value=f"*...i {len(matched) - 50} więcej.*", inline=False)
        else:
            embed.add_field(
                name="📋 Gracze online z tej organizacji",
                value="*Brak graczy online z tej organizacji w tej chwili.*",
                inline=False
            )

        source_label = (
            "zbiorczy endpoint serwera" if result.get("source") == "bulk"
            else "endpoint serwera (per gracz)" if result.get("source") == "per_player"
            else "baza organizacji + forum ProjectRPG"
        )
        footer = f"Źródło: {source_label} • Wynik cache'owany na {int(ORG_COUNT_CACHE_TTL)}s"
        if result.get("errors"):
            footer = f"⚠️ {result['errors']} błędów weryfikacji • {footer}"
        embed.set_footer(text=footer)

        await interaction.followup.send(embed=embed)

    async def _nick_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Podpowiada nicki graczy aktualnie online w miarę wpisywania (z cache'a ASE)."""
        info = await _get_ase_data()
        if not info["online"]:
            return []
        players = info.get("player_list", [])
        if not current:
            top = players[:25]
        else:
            top = [p for p, _ in _rank_matches(players, current, limit=25)]
        return [app_commands.Choice(name=p, value=p) for p in top]

    async def _build_player_profile_embed(self, nick: str, info: dict) -> discord.Embed:
        """Buduje pełną kartę gracza łącząc dane z ASE (nick/status), forum (UID, organizacja,
        służba, reputacja) i własnego endpointu serwera (ID, ping per-gracz)."""
        forum_ref = await _find_forum_profile_url(nick)
        exact_match = bool(forum_ref and forum_ref.get("exact", True))
        # Karta gracza pokazuje szczegóły (UID, frakcja, organizacja, reputacja) tylko dla
        # POTWIERDZONEGO profilu — jeśli forum_ref to tylko link do wyszukiwarki (exact=False),
        # scrapowanie strony wyników wyszukiwania i tak niczego by nie znalazło.
        game_info = await _scrape_forum_game_info(forum_ref["profile_url"]) if (forum_ref and exact_match) else None
        server_extra = await _fetch_server_player_extra(nick)

        na = "*— brak integracji —*"
        not_found = "*— nie znaleziono na forum —*"
        unsure = "*— brak pewnego dopasowania, sprawdź link niżej —*"

        server_id = str(server_extra["id"]) if server_extra and server_extra.get("id") is not None else na

        # Jeśli w ogóle nie udało się znaleźć profilu forum dla tego nicku (ani przez API,
        # ani przez publiczną wyszukiwarkę), wszystkie pola forum dostają jeden spójny komunikat.
        if forum_ref is None:
            uid = sluzba = organizacja = reputacja = not_found
        elif not exact_match:
            uid = sluzba = organizacja = reputacja = unsure
        else:
            uid = game_info["uid"] if game_info and game_info.get("uid") else na

            sluzba = na
            if game_info and game_info.get("frakcja"):
                sluzba = game_info["frakcja"]
                if game_info.get("ranga_frakcji"):
                    sluzba += f" (⤷ {game_info['ranga_frakcji']})"

            organizacja = na
            if game_info and game_info.get("organizacja"):
                organizacja = game_info["organizacja"]
                if game_info.get("ranga_organizacji"):
                    organizacja += f" (⤷ {game_info['ranga_organizacji']})"
                _PLAYER_ORG_CACHE[nick.lower()] = {
                    "org": game_info["organizacja"],
                    "rank": game_info.get("ranga_organizacji") or "",
                    "ts": time.monotonic()
                }
            elif game_info:
                _PLAYER_ORG_CACHE[nick.lower()] = {
                    "org": "",
                    "rank": "",
                    "ts": time.monotonic()
                }

            reputacja = na
            if game_info and game_info.get("reputacja"):
                reputacja = game_info["reputacja"]

        if server_extra and server_extra.get("ping") is not None:
            ping = f"{server_extra['ping']} ms *(per-gracz)*"
        else:
            ping = f"{info['ping_ms']} ms *(ping serwera — brak endpointu per-gracz)*"

        embed = discord.Embed(
            title=f"🪪  Karta Gracza — {nick}",
            color=discord.Color(0x5865F2)
        )
        embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")
        embed.add_field(name="🆔 ID (serwer)", value=server_id, inline=True)
        embed.add_field(name="🔑 UID (forum)", value=uid, inline=True)
        embed.add_field(name="🏷️ Nick", value=f"`{nick}`", inline=True)
        embed.add_field(name="🏢 Organizacja", value=organizacja, inline=False)
        embed.add_field(name="🚓 Służba", value=sluzba, inline=False)
        embed.add_field(name="⭐ Reputacja", value=reputacja, inline=True)
        embed.add_field(name="📶 Ping", value=ping, inline=True)
        if forum_ref and exact_match:
            embed.add_field(name="🔗 Profil forum", value=f"[Otwórz profil]({forum_ref['profile_url']})", inline=False)
        elif forum_ref:
            embed.add_field(name="🔗 Forum", value=f"[Szukaj na forum]({forum_ref['profile_url']})", inline=False)
        embed.set_footer(text="🟢 Gracz online na ProjectRPG  •  Dane forum/serwera zależne od skonfigurowanych integracji")
        return embed

    @prpg_group.command(name="szukaj", description="Sprawdza w czasie rzeczywistym, czy dany gracz jest online na serwerze ProjectRPG.")
    @app_commands.describe(nick="Nick lub fragment nicku gracza, którego szukasz")
    @app_commands.autocomplete(nick=_nick_autocomplete)
    async def prpg_szukaj(self, interaction: discord.Interaction, nick: str) -> None:
        await interaction.response.defer()

        info = await _get_ase_data()

        if not info['online']:
            await interaction.followup.send(embed=error_embed("Błąd Połączenia", "Serwer ProjectRPG nie odpowiada na zapytania ASE."))
            return

        players = info.get('player_list', [])
        ranked = _rank_matches(players, nick, limit=25)

        # Dokładnie jedno trafienie (lub idealne dopasowanie nicku) -> pełna karta gracza.
        exact = [p for p, kind in ranked if kind == "exact"]
        if exact:
            embed = await self._build_player_profile_embed(exact[0], info)
            await interaction.followup.send(embed=embed)
            return
        if len(ranked) == 1:
            embed = await self._build_player_profile_embed(ranked[0][0], info)
            await interaction.followup.send(embed=embed)
            return

        if ranked:
            lines = []
            badges = {"exact": "🎯", "starts": "▶️", "contains": "🔹"}
            for p, kind in ranked[:15]:
                lines.append(f"{badges[kind]} {_highlight_match(p, nick)}")
            embed = discord.Embed(
                title=f"🔎  Wyniki Wyszukiwania: `{nick}`",
                color=discord.Color(0x2ecc71)
            )
            embed.description = (
                f"```yaml\n"
                f"Status : 🟢 W GRZE (ONLINE)\n"
                f"Ping   : {info['ping_ms']} ms\n"
                f"Gracze : {info['players']} / {info['max_players']}\n"
                f"```\n"
                f"**Znalezione dopasowania ({len(ranked)}):**\n" +
                "\n".join(lines) +
                f"\n\n*Wpisz pełny, dokładny nick, aby zobaczyć pełną kartę gracza (ID, UID, organizacja, służba, reputacja, ping).*"
            )
            if len(ranked) > 15:
                embed.description += f"\n*...oraz {len(ranked) - 15} innych graczy. Doprecyzuj wyszukiwanie, aby zawęzić wyniki.*"
            embed.set_footer(text=f"ProjectRPG Live ASE Search • Czas zapytania: {info['ping_ms']} ms  •  🎯 dokładne  ▶️ zaczyna się od  🔹 zawiera")
        else:
            embed = discord.Embed(
                title=f"🔎  Wyniki Wyszukiwania: `{nick}`",
                color=discord.Color(0xe74c3c)
            )
            description = (
                f"```yaml\n"
                f"Status : 🔴 OFFLINE lub błędny nick\n"
                f"Gracze : {info['players']} / {info['max_players']} online\n"
                f"```\n"
                f"> Gracz o nicku zawierającym **'{nick}'** nie został odnaleziony na serwerze.\n"
            )
            # Podpowiedzi na wypadek literówki (dopasowanie rozmyte przez difflib)
            close = difflib.get_close_matches(nick.lower(), [p.lower() for p in players], n=5, cutoff=0.55)
            if close:
                close_display = [p for p in players if p.lower() in close]
                description += f"> 💡 Może chodziło Ci o: " + ", ".join(f"`{p}`" for p in close_display[:5]) + "\n"
            description += "> Użyj `/prpg gracze`, aby przejrzeć pełną listę aktywnych osób."
            embed.description = description
            embed.set_footer(text=f"ProjectRPG Live ASE Search • Czas zapytania: {info['ping_ms']} ms")
        await interaction.followup.send(embed=embed)

    @prpg_group.command(name="zarobki", description="Oficjalne zarobki prac ProjectRPG z forum (Zaktualizowane na 2026 rok).")
    @app_commands.describe(
        praca="Wybierz pracę dorywczą (lub zostaw puste dla interaktywnego panelu)",
        ulepszenie="Poziom ulepszenia pracy",
        konto_premium="Aktywne Konto Premium (+20% gotówki)",
        mnoznik="Aktywny mnożnik serwerowy (Weekend / Event / Happy Hours)"
    )
    @app_commands.choices(praca=[
        app_commands.Choice(name="Kurier RedEx / TransMax (Couriero/Speedvan/Carrier)", value="kurier"),
        app_commands.Choice(name="Koszenie Trawy / Kosiarki (300 RP • do 8,200 $)", value="kosiarki"),
        app_commands.Choice(name="Drwal (Ścinanie do 10,000 $ • Pielęgnacja 700 RP)", value="drwal"),
        app_commands.Choice(name="Kutry Rybackie (1,500 RP • do 8,500 $)", value="kutry"),
        app_commands.Choice(name="Lawety / Transport Kontenerów (Spinybed LV • do 10,100 $)", value="lawety"),
        app_commands.Choice(name="Dodo — Gaszenie Pożarów (1,000 RP • do 9,000 $)", value="dodo"),
        app_commands.Choice(name="Górnik / Kopalnia (4,000 RP • do 10,000 $ + Diamenty)", value="gornik"),
        app_commands.Choice(name="Aplikacja Transportowa (Taxi / Bolt • APK PRO do 12,000 $)", value="taxi"),
        app_commands.Choice(name="Szambiarz (800 RP • do 8,000 $)", value="szambiarz"),
        app_commands.Choice(name="Grzybiarz (0 RP • do 7,000 $)", value="grzybiarz"),
        app_commands.Choice(name="Magazynier (Bayside • 0 RP • Najwięcej EXP 600/h)", value="magazynier"),
        app_commands.Choice(name="Sprzątanie Ulic / Sweepery (0 RP • do 6,300 $)", value="sweepery"),
        app_commands.Choice(name="Wędkarz (36 $ za 1 kg ryb)", value="wedkarz"),
    ])
    @app_commands.choices(ulepszenie=[
        app_commands.Choice(name="0 LVL (Brak ulepszeń / Start — 1.00x)", value="tier1"),
        app_commands.Choice(name="Poziom 2 (Ulepszenie Podstawowe)", value="tier2"),
        app_commands.Choice(name="Poziom 3 (Ulepszenie Zaawansowane)", value="tier3"),
        app_commands.Choice(name="MAX LVL (Wymaksowany poziom pracy)", value="tier4"),
    ])
    @app_commands.choices(konto_premium=[
        app_commands.Choice(name="Konto Premium (+20% gotówki — Domyślnie)", value="tak"),
        app_commands.Choice(name="Brak (Konto Zwykłe — 1.00x)", value="nie"),
    ])
    @app_commands.choices(mnoznik=[
        app_commands.Choice(name="Standardowy (1.00x)", value="1.0"),
        app_commands.Choice(name="Happy Hours (+15% — 1.15x)", value="1.15"),
        app_commands.Choice(name="Weekend Bonus (+30% — 1.30x)", value="1.30"),
        app_commands.Choice(name="Event Specjalny (+50% — 1.50x)", value="1.50"),
        app_commands.Choice(name="Mega Event (2.00x Kasa)", value="2.00"),
    ])
    async def prpg_zarobki(
        self,
        interaction: discord.Interaction,
        praca: Optional[str] = "kurier",
        ulepszenie: Optional[str] = "tier1",
        konto_premium: Optional[str] = "tak",
        mnoznik: Optional[str] = "1.0"
    ) -> None:
        selected_job = praca or "kurier"
        selected_tier = ulepszenie or "tier1"
        is_premium = (konto_premium != "nie")
        server_mult = float(mnoznik or "1.0")

        embed = _build_job_embed(selected_job, selected_tier, is_premium, server_mult)
        view = JobDashboardView(
            current_job=selected_job,
            current_tier=selected_tier,
            premium=is_premium,
            server_mult=server_mult,
            user_id=interaction.user.id
        )
        await interaction.response.send_message(embed=embed, view=view)

    @prpg_group.command(name="tuning", description="Kalkulator kosztów i zestawów tuningu aut na ProjectRPG.")
    @app_commands.describe(kategoria="Wybierz kategorię komponentów tuningu")
    @app_commands.choices(kategoria=[
        app_commands.Choice(name="Zestawy Prowadzenia (Torowy / Wyścigowy / Drift)", value="zestawy"),
        app_commands.Choice(name="Mechanika (ECU V4, Turbo V5, Silnik)", value="mechanika"),
        app_commands.Choice(name="Zawieszenie i Napęd (GZ, Hydraulika, AWD)", value="zawieszenie"),
        app_commands.Choice(name="Wizualny & Optyczny (Szyby, Lampy, Licznik)", value="wizualny"),
    ])
    async def prpg_tuning(self, interaction: discord.Interaction, kategoria: str) -> None:
        if kategoria == "zestawy":
            embed = discord.Embed(
                title="🏎️  Zestawy Komponentów Prowadzenia (Tuning)",
                color=discord.Color(0xf1c40f)
            )
            embed.description = (
                "```fix\n"
                "• Zestaw Uliczny     : 50,000 PLN\n"
                "• Zestaw Wyścigowy   : 100,000 PLN\n"
                "• Zestaw Drifterski  : 150,000 PLN\n"
                "• Zestaw Torowy      : 250,000 PLN\n"
                "```\n"
                "> Zestawy modyfikują geometrię zawieszenia i zachowanie pojazdu na zakrętach."
            )
        elif kategoria == "mechanika":
            embed = discord.Embed(
                title="⚙️  Tuning Mechaniczny Silnika (ProjectRPG)",
                color=discord.Color(0xe67e22)
            )
            embed.description = (
                "```fix\n"
                "ECU        V1: 10,500  V2: 18,000  V3: 30,000  V4: 40,000\n"
                "Turbo      V1: 15,000  V2: 25,000  V3: 40,000  V4: 50,000  V5: 60,000\n"
                "C.F.I.     V1: 125,000 V2: 150,000 V3: 175,000 V4: 225,000 V5: 250,000\n"
                "LPG        25L: 20,000  50L: 25,000  75L: 35,000\n"
                "Bak paliwa 100L: 45,000 125L: 55,000 150L: 65,000\n"
                "```\n"
                "> Ceny w PLN, wg oficjalnego cennika warsztatu tuningu mechanicznego."
            )
        elif kategoria == "zawieszenie":
            embed = discord.Embed(
                title="🔩  Zawieszenie i Układ Napędowy",
                color=discord.Color(0x95a5a6)
            )
            embed.description = (
                "```fix\n"
                "• Gwintowane zawieszenie : 32,500 PLN\n"
                "• Hydraulika             : 45,000 PLN\n"
                "• System ABS             : 15,000 PLN\n"
                "• Zmiana napędu — RWD    : 30,000 PLN\n"
                "• Zmiana napędu — FWD    : 20,000 PLN\n"
                "• Zmiana napędu — AWD    : 50,000 PLN\n"
                "• Zmiana napędu — MZN    : 75,000 PLN\n"
                "```"
            )
        else:
            embed = discord.Embed(
                title="🎨  Tuning Wizualny i Dodatki",
                color=discord.Color(0x9b59b6)
            )
            embed.description = (
                "```fix\n"
                "• Radio                  : 500 PLN\n"
                "• CB-radio               : 1,500 PLN\n"
                "• Ogranicznik            : 2,000 PLN\n"
                "• Wykrywacz fotoradarów  : 2,500 PLN\n"
                "```\n"
                "> Cennik konkretnych części karoserii (spojlery, progi, zderzaki, felgi...) "
                "znajdziesz w komendzie `/prpg tuning-wizualny <pojazd>`."
            )

        embed.set_footer(text="Oficjalny cennik warsztatów ProjectRPG (dodatki oraz tuning mechaniczny)")
        await interaction.response.send_message(embed=embed)

    async def _tuning_pojazd_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Podpowiada nazwy pojazdów, dla których istnieją dane cennika tuningu wizualnego."""
        if not current:
            top = ALL_TUNABLE_VEHICLES[:25]
        else:
            top = [v for v, _ in _rank_matches(ALL_TUNABLE_VEHICLES, current, limit=25)]
        return [app_commands.Choice(name=v, value=v) for v in top]

    @prpg_group.command(name="tuning-wizualny", description="Zaawansowany kalkulator cennika części tuningu wizualnego dla konkretnego pojazdu.")
    @app_commands.describe(pojazd="Nazwa pojazdu (np. Elegy, Sultan, Jester)")
    @app_commands.autocomplete(pojazd=_tuning_pojazd_autocomplete)
    async def prpg_tuning_wizualny(self, interaction: discord.Interaction, pojazd: str) -> None:
        # Dopasowanie nazwy pojazdu ignorujące wielkość liter, z fallbackiem na najbliższe
        # dopasowanie (difflib), gdyby ktoś ominął autouzupełnianie i wpisał nazwę ręcznie.
        normalized = {v.lower(): v for v in ALL_TUNABLE_VEHICLES}
        matched_vehicle = normalized.get(pojazd.strip().lower())
        if matched_vehicle is None:
            close = difflib.get_close_matches(pojazd, ALL_TUNABLE_VEHICLES, n=1, cutoff=0.6)
            matched_vehicle = close[0] if close else None

        if matched_vehicle is None:
            embed = error_embed(
                "❌ Nie znaleziono pojazdu",
                f"Brak danych cennika tuningu wizualnego dla `{pojazd}`.\n"
                "Zacznij wpisywać nazwę pojazdu, aby zobaczyć podpowiedzi (np. `Elegy`, `Sultan`, `Jester`, `Flash`, `Stratum`, `Uranus`, `Blade`, `Savanna`, `Remington`, `Tornado`, `Slamvan`)."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Zbieramy pasujące części pogrupowane wg kategorii, licząc też widełki cenowe (najtańsza
        # i najdroższa opcja w danej kategorii), żeby dać graczowi orientację co do budżetu.
        matching_categories: dict[str, list[tuple[int, Optional[int]]]] = {}
        for category, parts in VISUAL_TUNING_PARTS.items():
            found = [(part_id, cena) for part_id, cena, vehicles in parts if matched_vehicle in vehicles]
            if found:
                matching_categories[category] = found

        if not matching_categories:
            embed = info_embed(
                f"🚗 Tuning Wizualny — {matched_vehicle}",
                "Nie znaleziono żadnych części tuningu wizualnego przypisanych do tego pojazdu w aktualnej bazie."
            )
            await interaction.response.send_message(embed=embed)
            return

        min_total = sum(min((c for _, c in parts if c is not None), default=0) for parts in matching_categories.values())
        max_total = sum(max((c for _, c in parts if c is not None), default=0) for parts in matching_categories.values())

        lines = []
        for category, parts in matching_categories.items():
            parts_sorted = sorted(parts, key=lambda p: (p[1] is None, p[1]))
            entries = ", ".join(
                f"`ID {pid}` — {format_number(cena)} PLN" if cena is not None else f"`ID {pid}` — cena nieznana"
                for pid, cena in parts_sorted
            )
            lines.append(f"**{category}** ({len(parts)}):\n{entries}")

        embed = discord.Embed(
            title=f"🎨 Kalkulator Tuningu Wizualnego — {matched_vehicle}",
            description="\n\n".join(lines),
            color=discord.Color(0x9b59b6)
        )
        embed.add_field(
            name="💰 Szacunkowy budżet (1 część z każdej pasującej kategorii)",
            value=f"Minimalny: **{format_number(min_total)} PLN**\nMaksymalny: **{format_number(max_total)} PLN**",
            inline=False
        )
        embed.set_footer(text="Dane na podstawie oficjalnego poradnika forum ProjectRPG (tuning wizualny)")
        await interaction.response.send_message(embed=embed)

    @prpg_group.command(name="taryfikator", description="Wyszukiwarka kar, mandatów i punktów karnych frakcji SAPD.")
    @app_commands.describe(wykroczenie="Wybierz kategorię wykroczenia")
    @app_commands.choices(wykroczenie=[
        app_commands.Choice(name="Przekroczenie prędkości", value="predkosc"),
        app_commands.Choice(name="Ucieczka przed policją (Pościg)", value="poscig"),
        app_commands.Choice(name="Jazda pod prąd / Taranowanie", value="taran"),
        app_commands.Choice(name="Posiadanie nielegalnych substancji", value="narkotyki"),
        app_commands.Choice(name="Odmowa przyjęcia mandatu", value="odmowa"),
    ])
    async def prpg_taryfikator(self, interaction: discord.Interaction, wykroczenie: str) -> None:
        if wykroczenie == "predkosc":
            embed = create_embed(
                title="🚓 Taryfikator SAPD: Przekroczenie Prędkości",
                description="**Teren Zabudowany (Limit 80 km/h):**\n"
                            "• Przekroczenie o 10-30 km/h: `500 PLN` | 2 pkt\n"
                            "• Przekroczenie o 31-50 km/h: `1,200 PLN` | 4 pkt\n"
                            "• Przekroczenie o 50+ km/h: `2,500 PLN` | 6 pkt + ewentualne zatrzymanie PJ\n\n"
                            "**Autostrada (Limit 120-140 km/h):**\n"
                            "• Przekroczenie o 20-40 km/h: `800 PLN` | 2 pkt\n"
                            "• Przekroczenie o 40+ km/h: `1,800 PLN` | 4 pkt",
                color=config.ERROR_COLOR
            )
        elif wykroczenie == "poscig":
            embed = create_embed(
                title="🚨 Taryfikator SAPD: Ucieczka przed Frakcją (Pościg)",
                description="• **Rozpoczęcie pościgu (Brak zatrzymania do kontroli):** `30-60 minut aresztu` lub mandat `5,000 - 15,000 PLN`\n"
                            "• **Taranowanie radiowozów podczas pościgu:** +`15 minut aresztu` za każdy taran\n"
                            "• **Wjazd do strefy DM podczas pościgu:** Natychmiastowe otwarcie ognia (KOD 3) + konfiskata pojazdu na Parking Policyjny (`5,000 PLN` wykup).",
                color=config.ERROR_COLOR
            )
        elif wykroczenie == "taran":
            embed = create_embed(
                title="🚗 Taryfikator SAPD: Kolizje i Jazda pod prąd",
                description="• **Jazda pod prąd autostradą / drogą szybkiego ruchu:** `1,500 PLN` | 4 pkt\n"
                            "• **Niezachowanie ostrożności i spowodowanie kolizji:** `1,000 PLN` + pokrycie szkód poszkodowanego\n"
                            "• **Celowy taran (DM pojazdem):** Zgłoszenie do administracji serwera (Zabranie Prawa Jazdy 1-3 dni).",
                color=config.ERROR_COLOR
            )
        elif wykroczenie == "narkotyki":
            embed = create_embed(
                title="🌿 Taryfikator SAPD: Nielegalne Substancje i Przedmioty",
                description="• **Posiadanie marihuany / kokainy:** `20-40 minut aresztu` (w zależności od gramatury) + konfiskata\n"
                            "• **Nielegalna broń palna bez licencji:** `45 minut aresztu` + konfiskata broni\n"
                            "• **Posiadanie wytrychów / narzędzi do włamań:** Mandat `2,500 PLN`.",
                color=config.ERROR_COLOR
            )
        else:
            embed = create_embed(
                title="⚖️ Odmowa Przyjęcia Mandatu",
                description="W przypadku odmowy uiszczenia nałożonej kary grzywny przez funkcjonariusza SAPD:\n\n"
                            "• **Przelicznik aresztu:** Każde `500 PLN` mandatu = `5 minut więzienia` (maksymalnie do 90 minut).\n"
                            "• **Pojazd kierowcy:** Zostaje odholowany na Parking Policyjny na koszt właściciela.",
                color=config.ERROR_COLOR
            )

        embed.set_footer(text="Oficjalny Taryfikator San Andreas Police Department (SAPD)")
        await interaction.response.send_message(embed=embed)

    @prpg_group.command(name="apelacja", description="Generuje gotowy wzór apelacji od kary na forum ProjectRPG.")
    @app_commands.describe(typ="Wybierz rodzaj otrzymanej kary")
    @app_commands.choices(typ=[
        app_commands.Choice(name="Prawo Jazdy (ZPJ)", value="prawko"),
        app_commands.Choice(name="Wyciszenie (Mute)", value="mute"),
        app_commands.Choice(name="Ban na serwer", value="ban"),
        app_commands.Choice(name="Blokada Broni (ZPB)", value="bron"),
    ])
    async def prpg_apelacja(self, interaction: discord.Interaction, typ: str) -> None:
        wzory = {
            "prawko": (
                "🚗 Wzór Apelacji: Zabranie Prawa Jazdy (ZPJ)",
                "```md\n"
                "Tytuł wątku: Apelacja od zabrania prawa jazdy - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Nick: <Twój aktualny nick>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <np. 2 dni>\n"
                "Licencja: <A / B / C>\n"
                "Powód: <Powód z kary>\n"
                "Wyjaśnienie: <Dokładne wyjaśnienie sytuacji>\n"
                "Coś od siebie: <Przeprosiny i obietnica poprawy>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
            "mute": (
                "🔇 Wzór Apelacji: Wyciszenie Czatu (Mute)",
                "```md\n"
                "Tytuł wątku: Apelacja od wyciszenia - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Nick: <Twój aktualny nick>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <np. 24h>\n"
                "Powód: <Powód z kary>\n"
                "Wyjaśnienie: <Dlaczego doszło do przewinienia>\n"
                "Coś od siebie: <Przeprosiny i deklaracja kultury wypowiedzi>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
            "ban": (
                "⛔ Wzór Apelacji: Blokada Konta (Ban)",
                "```md\n"
                "Tytuł wątku: Apelacja od bana - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Serial: <Twój Serial MTA z F8>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <Permanentna / Liczba dni>\n"
                "Powód: <Powód bana>\n"
                "Wyjaśnienie: <Szczegółowe wyjaśnienie sprawy>\n"
                "Coś od siebie: <Uzasadnienie dlaczego powinieneś otrzymać unbana>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
            "bron": (
                "🔫 Wzór Apelacji: Blokada Broni (ZPB)",
                "```md\n"
                "Tytuł wątku: Apelacja od blokady broni - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Nick: <Twój aktualny nick>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <np. 3 dni>\n"
                "Powód: <Powód kary>\n"
                "Wyjaśnienie: <Wyjaśnienie okoliczności użycia broni>\n"
                "Coś od siebie: <Przeprosiny>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
        }

        title, content = wzory.get(typ, ("Wzór Apelacji", "Brak wzoru"))
        embed = info_embed(title, f"Skopiuj poniższy wzór, uzupełnij pola i wklej na oficjalnym forum:\n\n{content}")
        await interaction.response.send_message(embed=embed)

    @prpg_group.command(name="frakcje", description="Informacje o frakcjach serwerowych i zarobkach na ProjectRPG.")
    async def prpg_frakcje(self, interaction: discord.Interaction) -> None:
        embed = info_embed(
            "🏢 Frakcje Serwerowe na ProjectRPG",
            "Przegląd głównych służb mundurowych i korporacji na serwerze:\n\n"
            "🚓 **San Andreas Police Department (SAPD):**\n"
            "• Rola: Ochrona porządku, pościgi, kontrole drogowe, obsługa stref DM.\n"
            "• Wymagania: Wiek 15+, sprawny mikrofon, brak kar w ostatnim czasie, znajomość KRD.\n\n"
            "🚑 **San Andreas Medical Department (SAMD):**\n"
            "• Rola: Ratownictwo medyczne, reanimacje, obsługa wypadków (RP medyczne).\n"
            "• Wymagania: Wysoka kultura osobista, podstawowa wiedza z pierwszej pomocy.\n\n"
            "🚒 **San Andreas Fire Department (SAFD):**\n"
            "• Rola: Gaszenie pożarów, ratownictwo techniczne, usuwanie plam oleju i wiatrołomów.\n\n"
            "🚕 **San Andreas Transport Corporation (SATC):**\n"
            "• Rola: Przewóz osób taksówkami, autobusami i limuzynami po całym stanie.\n\n"
            "🔧 **San Andreas Road Assistance (SARA):**\n"
            "• Rola: Naprawa infrastruktury drogowej, odholowywanie porzuconych aut."
        )
        embed.set_footer(text="Rekrutacje ogłaszane są cyklicznie w dziale Frakcji na forum")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectRPGCog(bot))