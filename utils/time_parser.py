"""
Moduł parsowania formatów czasowych (np. 10m, 2h, 3d, 1w) na sekundy oraz formatowania sekund na czytelny tekst.
"""

import re

TIME_REGEX = re.compile(r"^(\d+)\s*([sSmMhHdDwW]?)$")

TIME_MULTIPLIERS = {
    "": 1,
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800
}

def parse_time(time_str: str) -> int | None:
    """
    Parsuje ciąg znaków w formacie czasowym (np. '10m', '2h', '3d') na całkowitą liczbę sekund.
    Zwraca None w przypadku błędnego formatu.
    """
    if not time_str:
        return None

    match = TIME_REGEX.match(time_str.strip())
    if not match:
        return None

    value, unit = match.groups()
    unit = unit.lower()

    try:
        seconds = int(value) * TIME_MULTIPLIERS.get(unit, 1)
        return seconds if seconds > 0 else None
    except (ValueError, KeyError):
        return None

def format_time(seconds: int) -> str:
    """
    Konwertuje sekundy na pełny, czytelny ciąg znaków w języku polskim.
    Przykład: 7320 -> '2 godziny 2 minuty'
    """
    if seconds <= 0:
        return "0 sekund"

    weeks, seconds = divmod(seconds, 604800)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []

    if weeks > 0:
        if weeks == 1:
            parts.append("1 tydzień")
        elif 2 <= weeks <= 4:
            parts.append(f"{weeks} tygodnie")
        else:
            parts.append(f"{weeks} tygodni")

    if days > 0:
        if days == 1:
            parts.append("1 dzień")
        else:
            parts.append(f"{days} dni")

    if hours > 0:
        if hours == 1:
            parts.append("1 godzina")
        elif 2 <= (hours % 10) <= 4 and not (12 <= (hours % 100) <= 14):
            parts.append(f"{hours} godziny")
        else:
            parts.append(f"{hours} godzin")

    if minutes > 0:
        if minutes == 1:
            parts.append("1 minuta")
        elif 2 <= (minutes % 10) <= 4 and not (12 <= (minutes % 100) <= 14):
            parts.append(f"{minutes} minuty")
        else:
            parts.append(f"{minutes} minut")

    if seconds > 0 or not parts:
        if seconds == 1:
            parts.append("1 sekunda")
        elif 2 <= (seconds % 10) <= 4 and not (12 <= (seconds % 100) <= 14):
            parts.append(f"{seconds} sekundy")
        else:
            parts.append(f"{seconds} sekund")

    return " ".join(parts)
