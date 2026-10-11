"""
Ogólne funkcje pomocnicze i matematyczne.
"""

def format_number(val: int) -> str:
    """Formatuje liczby całkowite dodając separatory tysięczne (np. 1 000 000)."""
    return f"{val:,}".replace(",", " ")

def calculate_xp_for_level(level: int) -> int:
    """Oblicza wymagany skumulowany XP dla wskazanego poziomu."""
    return 5 * (level ** 2) + 50 * level + 100

def calculate_level_from_xp(xp: int) -> tuple[int, int, int]:
    """
    Oblicza bieżący poziom, XP wymagany do kolejnego poziomu oraz postęp.
    Zwraca krotkę: (current_level, xp_needed_for_next_level, remaining_xp_in_level)
    """
    level = 0
    accumulated_xp = 0

    while True:
        needed = calculate_xp_for_level(level)
        if xp < accumulated_xp + needed:
            current_level_xp = xp - accumulated_xp
            return level, needed, current_level_xp
        accumulated_xp += needed
        level += 1

def truncate_string(text: str, max_length: int = 1000) -> str:
    """Przycina zbyt długie teksty z dodaniem elipsy (...) na końcu."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."
