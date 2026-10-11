"""
Serwis obsługi transakcji finansowych w systemie ekonomii bota.
Gwarantuje spójność operacji w bazie danych SQLite i zabezpieczenie przed ujemnym stanem konta/podwójnym wydawaniem.
"""

import logging
from core.database import Database

logger = logging.getLogger("AegisBot.EconomyService")

class EconomyService:
    """Moduł operacyjny finansów użytkowników."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_account(self, guild_id: int, user_id: int) -> dict:
        """Pobiera konto użytkownika lub tworzy nowe w bazie z saldem 0."""
        row = await self.db.fetchone(
            "SELECT wallet, bank, last_daily, last_weekly, last_work, last_rob FROM economy_accounts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        if not row:
            await self.db.execute(
                "INSERT OR IGNORE INTO economy_accounts (guild_id, user_id, wallet, bank) VALUES (?, ?, 0, 0)",
                (guild_id, user_id)
            )
            return {"wallet": 0, "bank": 0, "last_daily": None, "last_weekly": None, "last_work": None, "last_rob": None}
        return dict(row)

    async def add_money(self, guild_id: int, user_id: int, amount: int, destination: str = "wallet") -> bool:
        """Dodaje środki do portfela lub banku."""
        if amount <= 0:
            return False

        await self.get_account(guild_id, user_id)
        column = "wallet" if destination == "wallet" else "bank"

        await self.db.execute(
            f"UPDATE economy_accounts SET {column} = {column} + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id)
        )
        return True

    async def remove_money(self, guild_id: int, user_id: int, amount: int, source: str = "wallet") -> bool:
        """Pobiera środki z portfela lub banku. Zwraca False jeśli saldo jest niewystarczające."""
        if amount <= 0:
            return False

        acc = await self.get_account(guild_id, user_id)
        column = "wallet" if source == "wallet" else "bank"

        if acc[column] < amount:
            return False

        await self.db.execute(
            f"UPDATE economy_accounts SET {column} = {column} - ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id)
        )
        return True

    async def deposit(self, guild_id: int, user_id: int, amount: int) -> tuple[bool, str]:
        """Wpłaca środki z portfela do banku."""
        acc = await self.get_account(guild_id, user_id)
        if amount <= 0:
            return False, "Kwota wpłaty musi być większa od zera."
        if acc["wallet"] < amount:
            return False, "Nie posiadasz wystarczającej gotówki w portfelu."

        await self.db.execute("UPDATE economy_accounts SET wallet = wallet - ?, bank = bank + ? WHERE guild_id = ? AND user_id = ?", (amount, amount, guild_id, user_id))
        return True, f"Pomyślnie wpłacono **{amount}** 🪙 do banku."

    async def withdraw(self, guild_id: int, user_id: int, amount: int) -> tuple[bool, str]:
        """Wypłaca środki z banku do portfela."""
        acc = await self.get_account(guild_id, user_id)
        if amount <= 0:
            return False, "Kwota wypłaty musi być większa od zera."
        if acc["bank"] < amount:
            return False, "Nie posiadasz wystarczającej kwoty na koncie bankowym."

        await self.db.execute("UPDATE economy_accounts SET bank = bank - ?, wallet = wallet + ? WHERE guild_id = ? AND user_id = ?", (amount, amount, guild_id, user_id))
        return True, f"Pomyślnie wypłacono **{amount}** 🪙 z banku."

    async def transfer(self, guild_id: int, sender_id: int, recipient_id: int, amount: int) -> tuple[bool, str]:
        """Przelewa podaną kwotę pomiędzy dwoma użytkownikami z poziomu portfela."""
        if sender_id == recipient_id:
            return False, "Nie możesz przesłać pieniędzy samemu sobie."

        if amount <= 0:
            return False, "Kwota przelewu musi być większa od zera."

        sender_acc = await self.get_account(guild_id, sender_id)
        if sender_acc["wallet"] < amount:
            return False, "Nie posiadasz wystarczającej ilości gotówki w portfelu."

        await self.db.execute("UPDATE economy_accounts SET wallet = wallet - ? WHERE guild_id = ? AND user_id = ?", (amount, guild_id, sender_id))
        await self.add_money(guild_id, recipient_id, amount, destination="wallet")

        return True, "Przelew zakończony sukcesem."

    async def buy_item(self, guild_id: int, user_id: int, item_id: int) -> tuple[bool, str, dict | None]:
        """Kupuje przedmiot ze sklepu i dodaje go do ekwipunku użytkownika."""
        item = await self.db.fetchone("SELECT * FROM shop_items WHERE guild_id = ? AND item_id = ?", (guild_id, item_id))
        if not item:
            return False, "Przedmiot o podanym ID nie istnieje w sklepie.", None

        acc = await self.get_account(guild_id, user_id)
        if acc["wallet"] < item["price"]:
            return False, f"Nie masz wystarczającej ilości gotówki w portfelu ({item['price']} 🪙).", None

        await self.db.execute("UPDATE economy_accounts SET wallet = wallet - ? WHERE guild_id = ? AND user_id = ?", (item["price"], guild_id, user_id))
        await self.db.execute(
            """
            INSERT INTO inventories (guild_id, user_id, item_id, quantity) VALUES (?, ?, ?, 1)
            ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET quantity = quantity + 1
            """,
            (guild_id, user_id, item_id)
        )
        return True, f"Pomyślnie zakupiono przedmiot **{item['name']}** za **{item['price']}** 🪙!", dict(item)
