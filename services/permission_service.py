"""
Serwis weryfikacji hierarchii ról i uprawnień moderacyjnych na serwerze.
"""

import discord
import config

class PermissionService:
    """Weryfikuje zasadność wykonania akcji moderacyjnej pomiędzy członkami serwera."""

    @staticmethod
    def can_moderate(
        moderator: discord.Member,
        target: discord.Member,
        bot_member: discord.Member
    ) -> tuple[bool, str]:
        """
        Sprawdza czy moderator ma prawo nałożyć karę na cel (target).
        Zwraca tuple (can_moderate: bool, reason_if_false: str).
        """
        # Blokada karania samego siebie
        if moderator.id == target.id:
            return False, "Nie możesz ukarać samego siebie."

        # Blokada karania bota przez bota/moderatora
        if target.id == bot_member.id:
            return False, "Nie można nakładać kar na bota."

        # Blokada karania właściciela serwera
        if target.id == moderator.guild.owner_id:
            return False, "Nie można nałożyć kary na Właściciela Serwera."

        # Blokada karania właściciela bota

        # Administrator serwera (oraz jego Właściciel) omijają sprawdzanie
        # hierarchii ról - posiadanie uprawnienia Administratora ma dawać
        # pełny dostęp do komend moderacyjnych niezależnie od tego, gdzie
        # dokładnie ustawiona jest jego rola na liście ról serwera.
        if moderator.id == moderator.guild.owner_id or moderator.guild_permissions.administrator:
            pass
        else:
            # Sprawdzenie hierarchii roli moderatora względem celu
            if moderator.top_role <= target.top_role:
                return False, "Twoja najwyższa rola jest niższa lub równa roli użytkownika, którego próbujesz ukarać."

        # Sprawdzenie hierarchii roli bota względem celu
        if bot_member.top_role <= target.top_role:
            return False, "Najwyższa rola bota jest niższa lub równa roli użytkownika. Uporządkuj role na serwerze."

        return True, ""