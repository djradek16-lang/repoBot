"""
Niestandardowy menadżer cooldownów przechowywanych w pamięci dla akcji tekstowych (XP) oraz operacji ekonomicznych.
"""

import time

class CooldownManager:
    """Zarządza czasami oczekiwania (cooldownami) w pamięci operacyjnej."""

    def __init__(self) -> None:
        self._cooldowns: dict[str, float] = {}

    def is_on_cooldown(self, key: str, cooldown_seconds: float) -> tuple[bool, float]:
        """
        Sprawdza czy dany klucz (np. 'xp:guild_id:user_id') jest aktywny na cooldownie.
        Zwraca krotkę (is_active, remaining_seconds).
        """
        now = time.time()
        last_time = self._cooldowns.get(key, 0.0)
        elapsed = now - last_time

        if elapsed < cooldown_seconds:
            return True, cooldown_seconds - elapsed

        self._cooldowns[key] = now
        return False, 0.0

    def reset_cooldown(self, key: str) -> None:
        """Kasuje cooldown dla podanego klucza."""
        self._cooldowns.pop(key, None)

    def cleanup(self, max_age_seconds: float = 3600.0) -> None:
        """Usuwa przestarzałe wpisy z pamięci w celu uniknięcia wycieków pamięci."""
        now = time.time()
        to_delete = [k for k, v in self._cooldowns.items() if now - v > max_age_seconds]
        for k in to_delete:
            del self._cooldowns[k]
