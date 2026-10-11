"""
Serwis obsługi wpłat członków na konto organizacji (w grze).
Prowadzi kolejkę zgłoszeń do zatwierdzenia, historię wszystkich operacji
(wliczając ręczne korekty administracji) oraz ranking honorowych dawców.
Liczą się do rankingu WYŁĄCZNIE zatwierdzone wpłaty typu 'dobrowolne'.
"""

import logging
from datetime import datetime, timezone
from core.database import Database

logger = logging.getLogger("AegisBot.DonationService")

DONATION_TYPES = ("dobrowolne", "obowiazkowe")


class DonationService:
    """Moduł operacyjny wpłat na konto organizacji i rankingu dawców."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def submit(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        donation_type: str,
        proof_url: str,
        nick: str,
        login: str,
    ) -> int:
        """Rejestruje nowe zgłoszenie wpłaty ze statusem 'pending', czekające na admina."""
        cursor = await self.db.execute(
            """
            INSERT INTO donations
                (guild_id, user_id, amount, donation_type, status, source, proof_url, submitted_by, nick, login)
            VALUES (?, ?, ?, ?, 'pending', 'zgloszenie', ?, ?, ?, ?)
            """,
            (guild_id, user_id, amount, donation_type, proof_url, user_id, nick, login)
        )
        return cursor.lastrowid

    async def set_review_message(self, donation_id: int, message_id: int) -> None:
        """Zapisuje ID wiadomości z panelem zatwierdzania (do ewentualnej edycji/odtworzenia widoku)."""
        await self.db.execute(
            "UPDATE donations SET review_message_id = ? WHERE donation_id = ?",
            (message_id, donation_id)
        )

    async def get(self, donation_id: int) -> dict | None:
        """Pobiera pojedyncze zgłoszenie po ID."""
        row = await self.db.fetchone("SELECT * FROM donations WHERE donation_id = ?", (donation_id,))
        return dict(row) if row else None

    async def decide(self, donation_id: int, decided_by: int, approve: bool) -> tuple[bool, str, dict | None]:
        """Zatwierdza lub odrzuca zgłoszenie oczekujące. Zwraca (sukces, komunikat, rekord)."""
        donation = await self.get(donation_id)
        if not donation:
            return False, "Nie znaleziono takiego zgłoszenia.", None
        if donation["status"] != "pending":
            return False, f"To zgłoszenie zostało już rozpatrzone (status: `{donation['status']}`).", donation

        new_status = "approved" if approve else "rejected"
        await self.db.execute(
            "UPDATE donations SET status = ?, decided_by = ?, decided_at = ? WHERE donation_id = ?",
            (new_status, decided_by, datetime.now(timezone.utc), donation_id)
        )
        donation["status"] = new_status
        return True, "OK", donation

    async def admin_adjust(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        donation_type: str,
        admin_id: int,
        reason: str,
        remove: bool = False,
    ) -> int:
        """Ręczna korekta administracyjna (dodanie lub usunięcie kwoty z licznika danego użytkownika).
        Zapisywana od razu jako zatwierdzona (bez kolejki), z pełną historią kto/kiedy/dlaczego."""
        signed_amount = -abs(amount) if remove else abs(amount)
        cursor = await self.db.execute(
            """
            INSERT INTO donations
                (guild_id, user_id, amount, donation_type, status, source, reason, submitted_by, decided_by, decided_at)
            VALUES (?, ?, ?, ?, 'approved', 'admin', ?, ?, ?, ?)
            """,
            (guild_id, user_id, signed_amount, donation_type, reason, admin_id, admin_id, datetime.now(timezone.utc))
        )
        return cursor.lastrowid

    async def get_user_total(self, guild_id: int, user_id: int, donation_type: str = "dobrowolne") -> int:
        """Suma zatwierdzonych wpłat danego użytkownika (domyślnie tylko dobrowolne)."""
        row = await self.db.fetchone(
            """
            SELECT COALESCE(SUM(amount), 0) AS total FROM donations
            WHERE guild_id = ? AND user_id = ? AND donation_type = ? AND status = 'approved'
            """,
            (guild_id, user_id, donation_type)
        )
        return max(row["total"], 0) if row else 0

    async def get_leaderboard(self, guild_id: int, donation_type: str = "dobrowolne", limit: int = 10) -> list[dict]:
        """Zwraca ranking TOP dawców (suma zatwierdzonych wpłat danego typu), malejąco."""
        rows = await self.db.fetchall(
            """
            SELECT user_id, SUM(amount) AS total FROM donations
            WHERE guild_id = ? AND donation_type = ? AND status = 'approved'
            GROUP BY user_id
            HAVING total > 0
            ORDER BY total DESC
            LIMIT ?
            """,
            (guild_id, donation_type, limit)
        )
        return [dict(r) for r in rows]

    async def get_pending(self, guild_id: int) -> list[dict]:
        """Zwraca wszystkie oczekujące zgłoszenia danej gildii (np. do odtworzenia widoków po restarcie)."""
        rows = await self.db.fetchall(
            "SELECT * FROM donations WHERE guild_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (guild_id,)
        )
        return [dict(r) for r in rows]

    async def get_all_pending(self) -> list[dict]:
        """Zwraca wszystkie oczekujące zgłoszenia ze wszystkich gildii (do rejestracji widoków na starcie bota)."""
        rows = await self.db.fetchall("SELECT * FROM donations WHERE status = 'pending'")
        return [dict(r) for r in rows]