"""
Punkt wejściowy aplikacji AegisBot.
Zarządza uruchamianiem pętli zdarzeń, sprawdzaniem zmiennych środowiskowych i obsługą sygnałów.
Automatycznie ponawia próbę uruchomienia bota po przejściowych błędach sieciowych
(np. chwilowy brak DNS przy przełączaniu sieci na telefonie), zamiast kończyć proces.
"""

import asyncio
import logging
import os
import subprocess
import sys

import config
from core.bot import AegisBot
from core.logging_config import setup_logging

# Maksymalny czas oczekiwania między kolejnymi próbami ponownego uruchomienia (sekundy).
MAX_RETRY_DELAY = 60
# Początkowy czas oczekiwania po pierwszym błędzie (sekundy).
INITIAL_RETRY_DELAY = 5

print(f"PYTHON VERSION: {sys.version}")

def maybe_autoupdate_ytdlp(logger: logging.Logger) -> None:
    """Aktualizuje yt-dlp PRZED startem bota (o ile nie wyłączono zmienną env).

    To jest, statystycznie, najczęstsza pojedyncza przyczyna błędu "Sign in to
    confirm you're not a bot" mimo poprawnych cookies: YouTube regularnie zmienia
    coś w swoim froncie, extraktor w starej wersji yt-dlp się na tym wykłada,
    a maintainerzy łatają to zwykle w ciągu kilku dni nowym wydaniem. Cookies
    i PO Tokeny nic nie dadzą, jeśli sam parser jest nieaktualny.

    Uruchamiane raz, synchronicznie, PRZED importem/użyciem yt_dlp przez Cogi -
    bo `pip install -U` nie ma wpływu na już zaimportowany moduł w tym samym
    procesie. Wyłączalne przez MUSIC_YTDLP_AUTOUPDATE=false, gdyby np. hosting
    nie pozwalał na pip install przy starcie albo user chciał przypiąć wersję."""
    if os.getenv("MUSIC_YTDLP_AUTOUPDATE", "true").strip().lower() in ("0", "false", "no", "off"):
        logger.info("Auto-aktualizacja yt-dlp wyłączona (MUSIC_YTDLP_AUTOUPDATE=false).")
        return
    try:
        logger.info("Sprawdzam aktualizacje yt-dlp przed startem bota...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"],
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode == 0:
            logger.info("yt-dlp sprawdzone/zaktualizowane pomyślnie.")
        else:
            logger.warning(f"Aktualizacja yt-dlp zwróciła kod {result.returncode}: {result.stderr.strip()[:500]}")
    except Exception as exc:
        logger.warning(f"Nie udało się sprawdzić/zaktualizować yt-dlp (kontynuuję mimo to): {exc}")


async def run_with_reconnect(logger: logging.Logger) -> None:
    """Uruchamia bota w pętli, automatycznie ponawiając próbę po błędach sieciowych/połączeniowych."""
    delay = INITIAL_RETRY_DELAY

    while True:
        bot = AegisBot()
        try:
            async with bot:
                await bot.start(config.DISCORD_TOKEN)
            # bot.start() zakończył się bez wyjątku (np. bot.close() wywołane celowo) — kończymy pętlę.
            logger.info("Bot zakończył działanie w sposób kontrolowany. Zatrzymywanie.")
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.error(
                f"Utracono połączenie lub napotkano błąd podczas działania bota: {exc}. "
                f"Ponawiam próbę za {delay}s...",
                exc_info=True
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)
            continue


def main() -> None:
    """Rozpoczyna działanie bota."""
    logger = setup_logging(config.BASE_DIR)

    if not config.DISCORD_TOKEN:
        logger.critical("KRYTYCZNY BŁĄD: Brak tokenu Discord w pliku .env! Uzupełnij pole DISCORD_TOKEN.")
        sys.exit(1)

    maybe_autoupdate_ytdlp(logger)

    try:
        asyncio.run(run_with_reconnect(logger))
    except KeyboardInterrupt:
        logger.info("Otrzymano sygnał wyłączenia (KeyboardInterrupt). Przerwanie pętli zdarzeń.")


if __name__ == "__main__":
    main()