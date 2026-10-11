"""
Serwis naliczania punktów doświadczenia (XP), zarządzania poziomami oraz nagrodami w postaci ról.
"""

import random
import logging
from datetime import datetime
import discord

from core.database import Database
from utils.helpers import calculate_level_from_xp

logger = logging.getLogger("AegisBot.LevelingService")

class LevelingService:
    """Moduł operacyjny naliczania XP i awansów użytkowników."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add_xp(
        self,
        guild: discord.Guild,
        member: discord.Member,
        min_xp: int = 15,
        max_xp: int = 25
    ) -> tuple[bool, int, list[discord.Role]]:
        """
        Losuje i dodaje XP użytkownikowi, sprawdza awans na kolejny poziom.
        Zwraca tuple: (leveled_up: bool, new_level: int, roles_granted: list[discord.Role])
        """
        row = await self.db.fetchone(
            "SELECT xp, level, last_xp_gain FROM levels WHERE guild_id = ? AND user_id = ?",
            (guild.id, member.id)
        )

        current_xp = row["xp"] if row else 0
        current_level = row["level"] if row else 0

        xp_to_add = random.randint(min_xp, max_xp)
        new_xp = current_xp + xp_to_add

        new_level, _, _ = calculate_level_from_xp(new_xp)
        leveled_up = new_level > current_level

        await self.db.execute(
            """
            INSERT INTO levels (guild_id, user_id, xp, level, last_xp_gain)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = ?,
                level = ?,
                last_xp_gain = CURRENT_TIMESTAMP
            """,
            (guild.id, member.id, new_xp, new_level, new_xp, new_level)
        )

        roles_granted = []
        if leveled_up:
            level_roles = await self.db.fetchall(
                "SELECT role_id FROM level_roles WHERE guild_id = ? AND level <= ?",
                (guild.id, new_level)
            )

            for lr in level_roles:
                role = guild.get_role(lr["role_id"])
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Nagroda za osiągnięcie poziomu {new_level}")
                        roles_granted.append(role)
                    except discord.HTTPException:
                        pass

        return leveled_up, new_level, roles_granted

    async def set_xp(self, guild_id: int, user_id: int, xp: int) -> int:
        """Ustawia dokładną ilość XP dla użytkownika."""
        new_xp = max(0, xp)
        new_level, _, _ = calculate_level_from_xp(new_xp)
        await self.db.execute(
            """
            INSERT INTO levels (guild_id, user_id, xp, level, last_xp_gain)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = ?,
                level = ?,
                last_xp_gain = CURRENT_TIMESTAMP
            """,
            (guild_id, user_id, new_xp, new_level, new_xp, new_level)
        )
        return new_level
