"""
Serwis systemu stażu/awansów. Prowadzi drabinkę rang (nazwa -> próg punktowy -> rola)
oraz historię przyznanych "plusów" (punktów), z których liczy się aktualna suma i ranga.
"""

from core.database import Database


class RankService:
    """Punkty ("plusy") do awansów oraz konfigurowalna drabinka rang."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._tables_checked = False

    async def _ensure_tables(self) -> None:
        """Upewnia się, że tabele rank_thresholds, rank_points oraz strikes istnieją."""
        if self._tables_checked:
            return
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS rank_thresholds (
                guild_id INTEGER NOT NULL,
                rank_name TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, rank_name)
            );
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS rank_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS strikes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._tables_checked = True

    # --- Drabinka rang -----------------------------------------------------

    async def set_rank(self, guild_id: int, rank_name: str, threshold: int, role_id: int) -> None:
        await self._ensure_tables()
        await self.db.execute(
            """
            INSERT INTO rank_thresholds (guild_id, rank_name, threshold, role_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, rank_name) DO UPDATE SET threshold = ?, role_id = ?
            """,
            (guild_id, rank_name, threshold, role_id, threshold, role_id)
        )

    async def get_ladder(self, guild_id: int) -> list[dict]:
        """Zwraca skonfigurowaną drabinkę rang posortowaną rosnąco po progu punktowym."""
        await self._ensure_tables()
        rows = await self.db.fetchall(
            "SELECT rank_name, threshold, role_id FROM rank_thresholds WHERE guild_id = ? ORDER BY threshold ASC",
            (guild_id,)
        )
        return [dict(r) for r in rows]

    @staticmethod
    def current_rank(ladder: list[dict], points: int) -> dict | None:
        """Najwyższa ranga, na jaką zasługują dane punkty (albo None, jeśli poniżej najniższego progu)."""
        current = None
        for rank in ladder:
            if points >= rank["threshold"]:
                current = rank
        return current

    @staticmethod
    def next_rank(ladder: list[dict], points: int) -> dict | None:
        """Najbliższa ranga do zdobycia (albo None, jeśli już najwyższa)."""
        for rank in ladder:
            if points < rank["threshold"]:
                return rank
        return None

    # --- Punkty --------------------------------------------------------------

    async def adjust_points(
        self, guild_id: int, user_id: int, amount: int, admin_id: int, reason: str, remove: bool = False
    ) -> None:
        await self._ensure_tables()
        signed_amount = -abs(amount) if remove else abs(amount)
        await self.db.execute(
            "INSERT INTO rank_points (guild_id, user_id, amount, reason, admin_id) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, signed_amount, reason, admin_id)
        )

    async def get_points(self, guild_id: int, user_id: int) -> int:
        await self._ensure_tables()
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM rank_points WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        return max(row["total"], 0) if row else 0

    async def get_leaderboard(self, guild_id: int, limit: int = 10) -> list[dict]:
        await self._ensure_tables()
        rows = await self.db.fetchall(
            """
            SELECT user_id, SUM(amount) AS total FROM rank_points
            WHERE guild_id = ?
            GROUP BY user_id
            HAVING total > 0
            ORDER BY total DESC
            LIMIT ?
            """,
            (guild_id, limit)
        )
        return [dict(r) for r in rows]

    # --- Statystyki all-time (dane już zbierane dziennie przez ActivityCog) --

    async def get_alltime_voice_seconds(self, guild_id: int, user_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(seconds), 0) AS total FROM voice_activity WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        return row["total"] if row else 0

    async def get_alltime_messages(self, guild_id: int, user_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(count), 0) AS total FROM message_activity WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        return row["total"] if row else 0

    # --- Minusy (kary) --------------------------------------------------------

    async def adjust_strikes(
        self, guild_id: int, user_id: int, amount: int, admin_id: int | None, reason: str, remove: bool = False
    ) -> None:
        await self._ensure_tables()
        signed_amount = -abs(amount) if remove else abs(amount)
        await self.db.execute(
            "INSERT INTO strikes (guild_id, user_id, amount, reason, admin_id) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, signed_amount, reason, admin_id)
        )

    async def get_strike_count(self, guild_id: int, user_id: int) -> int:
        await self._ensure_tables()
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM strikes WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        return max(row["total"], 0) if row else 0