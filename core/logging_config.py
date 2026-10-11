"""
Konfiguracja zaawansowanego logowania do konsoli oraz pliku z rotacją.
"""

import logging
import socket
import struct
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

# Strefa czasowa używana w logach, NIEZALEŻNIE od ustawień systemowych/kontenera.
LOG_TIMEZONE = ZoneInfo("Europe/Warsaw")

# Serwery NTP używane do wykrycia przesunięcia zegara kontenera względem realnego czasu.
_NTP_SERVERS = ("pool.ntp.org", "time.google.com", "time.cloudflare.com")

# Przesunięcie (w sekundach) dodawane do każdego znacznika czasu logów.
# Dodatnie = zegar kontenera "spóźnia się" względem realnego czasu.
_CLOCK_OFFSET_SECONDS: float = 0.0


def _query_ntp_offset(server: str, timeout: float = 2.5) -> float:
    """Wysyła minimalne zapytanie NTP i zwraca różnicę (prawdziwy_czas - czas_lokalny) w sekundach."""
    ntp_packet = b"\x1b" + 47 * b"\0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t_sent = time.time()
        sock.sendto(ntp_packet, (server, 123))
        data, _ = sock.recvfrom(48)
        t_recv = time.time()

    # Pole "transmit timestamp" serwera NTP: bajty 40-47, sekundy od 1900-01-01.
    ntp_seconds = struct.unpack("!I", data[40:44])[0]
    server_time = ntp_seconds - 2208988800  # konwersja epoki NTP (1900) -> Unix (1970)

    # Kompensacja czasu podróży pakietu (round-trip / 2), przybliżone.
    round_trip = t_recv - t_sent
    estimated_true_time = server_time + (round_trip / 2)
    local_time_at_send = t_sent

    return estimated_true_time - local_time_at_send


def _detect_clock_offset(logger: logging.Logger) -> float:
    """Próbuje wykryć przesunięcie zegara kontenera przez kilka serwerów NTP po kolei."""
    for server in _NTP_SERVERS:
        try:
            offset = _query_ntp_offset(server)
            if abs(offset) > 2:
                logger.warning(
                    f"Wykryto rozjazd zegara systemowego kontenera: {offset:+.1f}s "
                    f"(źródło: {server}). Logi będą automatycznie korygowane."
                )
            else:
                logger.debug(f"Zegar kontenera zgodny z NTP (offset {offset:+.2f}s, źródło: {server}).")
            return offset
        except Exception as e:
            logger.debug(f"Zapytanie NTP do {server} nie powiodło się: {e}")
            continue

    logger.warning(
        "Nie udało się połączyć z żadnym serwerem NTP (możliwa blokada portu UDP 123 na hostingu). "
        "Korekcja zegara logów jest wyłączona - jeśli godziny w logach są błędne, "
        "to prawdopodobnie zegar systemowy kontenera, którego nie da się stąd poprawić."
    )
    return 0.0


class TZFormatter(logging.Formatter):
    """Formatter, który liczy czas względem LOG_TIMEZONE i koryguje go o wykryte
    przesunięcie zegara kontenera (_CLOCK_OFFSET_SECONDS), więc logi są poprawne
    nawet jeśli sam zegar systemowy kontenera jest przesunięty."""

    def formatTime(self, record: logging.LogRecord, datefmt: str = None) -> str:
        corrected_ts = record.created + _CLOCK_OFFSET_SECONDS
        dt = datetime.fromtimestamp(corrected_ts, tz=LOG_TIMEZONE)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")


def setup_logging(base_dir: Path) -> logging.Logger:
    """
    Konfiguruje system logowania z plikiem rotacyjnym i wyjściem konsolowym.
    """
    global _CLOCK_OFFSET_SECONDS

    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "bot.log"

    formatter = TZFormatter(
        "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler rotacji plików (maksymalnie 5MB na plik, 5 plików zapasowych)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # Handler dla konsoli
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # Główny Logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Filtrowanie zbyt szczegółowych logów z bibliotek zewnętrznych
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    logger = logging.getLogger("AegisBot")

    # Wykrycie i zapisanie przesunięcia zegara PRZED pierwszym właściwym logiem,
    # żeby nawet komunikat startowy miał już poprawną godzinę.
    _CLOCK_OFFSET_SECONDS = _detect_clock_offset(logger)

    logger.info("System logowania został pomyślnie zainicjalizowany.")
    return logger