"""
Moduł systemu Diamentów.
Obsługuje zdobywanie, przeglądanie i zarządzanie diamentami przez użytkowników serwera.

Komendy publiczne:
  /diament       - Odbierz codzienny diament dla siebie.
  /topdiax       - Ranking użytkowników z największą liczbą diamentów.

Komendy administracyjne (grupa /diamentadmin):
  /diamentadmin dodaj  - Dodaje określoną liczbę diamentów wskazanemu użytkownikowi.
  /diamentadmin usun   - Usuwa określoną liczbę diamentów wskazanemu użytkownikowi.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import success_embed, error_embed, info_embed

logger = logging.getLogger("AegisBot.Cogs.Diamonds")

# ─── Stałe ───────────────────────────────────────────────────────────────────

DIAMOND_EMOJI = "💎"
DAILY_DIAMONDS = 1   # Ile diamentów dodaje /diament
TOP_LIMIT = 10       # Ile miejsc pokazuje /topdiax

# ─── Cog ─────────────────────────────────────────────────────────────────────

class DiamondsCog(commands.Cog):
    """System diamentów – waluta prestiżu serwera."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Wewnętrzne helpery ────────────────────────────────────────────────────

    async def _get_diamonds(self, guild_id: int, user_id: int) -> int:
        """Zwraca aktualną liczbę diamentów użytkownika (0 jeśli brak rekordu)."""
        row = await self.bot.db.fetchone(
            "SELECT diamonds FROM user_diamonds WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        return row["diamonds"] if row else 0

    async def _upsert_diamonds(self, guild_id: int, user_id: int, delta: int) -> int:
        """Dodaje *delta* diamentów (może być ujemne). Zwraca nową wartość (min 0)."""
        await self.bot.db.execute(
            """
            INSERT INTO user_diamonds (guild_id, user_id, diamonds)
            VALUES (?, ?, MAX(0, ?))
            ON CONFLICT(guild_id, user_id) DO UPDATE
                SET diamonds = MAX(0, diamonds + ?)
            """,
            (guild_id, user_id, max(0, delta), delta)
        )
        return await self._get_diamonds(guild_id, user_id)


    # ── Komendy publiczne ─────────────────────────────────────────────────────

    @app_commands.command(
        name="diament",
        description="Dodaj sobie diament! 💎"
    )
    async def diament(self, interaction: discord.Interaction) -> None:
        """Dodaje +1 diament użytkownikowi bez żadnych ograniczeń."""
        new_total = await self._upsert_diamonds(interaction.guild_id, interaction.user.id, DAILY_DIAMONDS)

        embed = success_embed(
            "Diament Dodany!",
            f"Twoja łączna liczba diamentów: **{new_total} {DIAMOND_EMOJI}**"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="topdiax",
        description="Pokazuje ranking użytkowników z największą liczbą diamentów. 💎"
    )
    async def topdiax(self, interaction: discord.Interaction) -> None:
        """Tabela liderów diamentów – top 10 według liczby posiadanych diamentów."""
        rows = await self.bot.db.fetchall(
            """
            SELECT user_id, diamonds
            FROM user_diamonds
            WHERE guild_id = ? AND diamonds > 0
            ORDER BY diamonds DESC
            LIMIT ?
            """,
            (interaction.guild_id, TOP_LIMIT)
        )

        if not rows:
            await interaction.response.send_message(
                embed=info_embed(
                    f"{DIAMOND_EMOJI} Ranking Diamentów",
                    "Nikt jeszcze nie zebrał żadnych diamentów na tym serwerze. Użyj `/diament`, żeby zacząć!"
                )
            )
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for idx, row in enumerate(rows):
            prefix = medals[idx] if idx < 3 else f"**#{idx + 1}**"
            member = interaction.guild.get_member(row["user_id"])
            name = member.mention if member else f"ID: {row['user_id']}"
            lines.append(f"{prefix} {name} — **{row['diamonds']} {DIAMOND_EMOJI}**")

        embed = info_embed(
            f" Ranking Diamentów — {interaction.guild.name}",
            "\n".join(lines)
        )
        await interaction.response.send_message(embed=embed)

    # ── Komendy administracyjne ───────────────────────────────────────────────

    diament_group = app_commands.Group(
        name="diamentadmin",
        description="Zarządzanie diamentami użytkowników (tylko administratorzy)",
        default_permissions=discord.Permissions(administrator=True)
    )

    @diament_group.command(
        name="dodaj",
        description="Dodaje określoną liczbę diamentów wskazanemu użytkownikowi."
    )
    @app_commands.describe(
        target="Użytkownik, któremu chcesz dodać diamenty",
        ilosc="Liczba diamentów do dodania"
    )
    @is_administrator()
    async def diamentadd(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        ilosc: app_commands.Range[int, 1, 1_000_000]
    ) -> None:
        new_total = await self._upsert_diamonds(interaction.guild_id, target.id, ilosc)
        embed = success_embed(
            "Dodano Diamenty",
            f"Dodano **+{ilosc} {DIAMOND_EMOJI}** dla {target.mention}.\n"
            f"Nowy stan konta: **{new_total} {DIAMOND_EMOJI}**"
        )
        await interaction.response.send_message(embed=embed)
        logger.info(
            f"[Diamonds] {interaction.user} ({interaction.user.id}) dodał "
            f"{ilosc} diamentów dla {target} ({target.id}) "
            f"na serwerze {interaction.guild_id}. Nowy stan: {new_total}."
        )

    @diament_group.command(
        name="usun",
        description="Usuwa określoną liczbę diamentów wskazanemu użytkownikowi."
    )
    @app_commands.describe(
        target="Użytkownik, któremu chcesz odjąć diamenty",
        ilosc="Liczba diamentów do usunięcia"
    )
    @is_administrator()
    async def diamentusun(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        ilosc: app_commands.Range[int, 1, 1_000_000]
    ) -> None:
        current = await self._get_diamonds(interaction.guild_id, target.id)
        if current == 0:
            await interaction.response.send_message(
                embed=error_embed(
                    "Brak Diamentów",
                    f"{target.mention} nie posiada żadnych diamentów do odjęcia."
                ),
                ephemeral=True
            )
            return

        new_total = await self._upsert_diamonds(interaction.guild_id, target.id, -ilosc)
        actual_removed = current - new_total  # uwzględnia limit >= 0

        embed = success_embed(
            "Usunięto Diamenty",
            f"Usunięto **-{actual_removed} {DIAMOND_EMOJI}** od {target.mention}.\n"
            f"Nowy stan konta: **{new_total} {DIAMOND_EMOJI}**"
        )
        await interaction.response.send_message(embed=embed)
        logger.info(
            f"[Diamonds] {interaction.user} ({interaction.user.id}) usunął "
            f"{actual_removed} diamentów od {target} ({target.id}) "
            f"na serwerze {interaction.guild_id}. Nowy stan: {new_total}."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiamondsCog(bot))
