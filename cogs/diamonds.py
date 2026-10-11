"""
Moduł systemu Diamentów (Aegis Diamonds).
Obsługuje zdobywanie, przeglądanie i zarządzanie diamentami przez użytkowników serwera.

Komendy publiczne:
  /diament       - Dodaje +1 diament do konta użytkownika.
  /topdiax       - Prestiżowy ranking użytkowników z największą liczbą diamentów.

Komendy administracyjne (grupa /diamentadmin):
  /diamentadmin dodaj  - Dodaje określoną liczbę diamentów wskazanemu użytkownikowi.
  /diamentadmin usun   - Usuwa określoną liczbę diamentów wskazanemu użytkownikowi.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import create_embed, error_embed

logger = logging.getLogger("AegisBot.Cogs.Diamonds")

# ─── Stałe Stylistyczne i Konfiguracja ─────────────────────────────────────────

DIAMOND_EMOJI = "💎"
DAILY_DIAMONDS = 1   # Ile diamentów dodaje /diament
TOP_LIMIT = 10       # Ile pozycji pokazuje /topdiax

COLOR_DIAMOND = 0x00E5FF  # Błyszczący błękit diamentowy / cyan
COLOR_GOLD = 0xF1C40F     # Złoto na podium rankingu
COLOR_ADMIN = 0x9B59B6    # Fiolet administracyjny


# ─── Cog ─────────────────────────────────────────────────────────────────────

class DiamondsCog(commands.Cog):
    """System diamentów – waluta prestiżu serwera z nową oprawą wizualną."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Wewnętrzne metody bazy danych (MySQL + SQLite) ─────────────────────────

    async def _get_diamonds(self, guild_id: int, user_id: int) -> int:
        """Zwraca aktualną liczbę diamentów użytkownika (0 jeśli brak wpisu)."""
        row = await self.bot.db.fetchone(
            "SELECT diamonds FROM user_diamonds WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        return row["diamonds"] if row else 0

    async def _upsert_diamonds(self, guild_id: int, user_id: int, delta: int) -> int:
        """
        Dodaje *delta* diamentów (może być ujemne).
        Zwraca nowy stan konta (minimum 0).
        Działa bezbłędnie zarówno na MySQL (GameHost) jak i SQLite.
        """
        is_mysql = getattr(self.bot.db, "is_mysql", False)

        if is_mysql:
            query = """
                INSERT INTO user_diamonds (guild_id, user_id, diamonds)
                VALUES (%s, %s, GREATEST(0, %s))
                ON DUPLICATE KEY UPDATE
                    diamonds = GREATEST(0, CAST(diamonds AS SIGNED) + %s)
            """
            await self.bot.db.execute(
                query,
                (guild_id, user_id, max(0, delta), delta)
            )
        else:
            query = """
                INSERT INTO user_diamonds (guild_id, user_id, diamonds)
                VALUES (?, ?, MAX(0, ?))
                ON CONFLICT(guild_id, user_id) DO UPDATE
                    SET diamonds = MAX(0, diamonds + ?)
            """
            await self.bot.db.execute(
                query,
                (guild_id, user_id, max(0, delta), delta)
            )

    async def _resolve_user_name(self, guild: discord.Guild | None, user_id: int) -> str:
        """Zwraca czysty tekst nicku użytkownika bez oznaczania @."""
        if guild:
            member = guild.get_member(user_id)
            if member:
                return discord.utils.escape_markdown(member.display_name)

            try:
                member = await guild.fetch_member(user_id)
                return discord.utils.escape_markdown(member.display_name)
            except (discord.NotFound, discord.HTTPException):
                pass

            try:
                row = await self.bot.db.fetchone(
                    "SELECT display_name FROM member_names WHERE guild_id = ? AND user_id = ?",
                    (guild.id, user_id)
                )
                if row and row.get("display_name"):
                    return discord.utils.escape_markdown(row["display_name"])
            except Exception:
                pass

        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            return discord.utils.escape_markdown(user.display_name or user.name)
        except Exception:
            return f"Użytkownik {user_id}"

    # ── Komendy publiczne ─────────────────────────────────────────────────────

    @app_commands.command(
        name="diament",
        description="Dodaj sobie diament! 💎"
    )
    async def diament(self, interaction: discord.Interaction) -> None:
        """Dodaje +1 diament użytkownikowi i wyświetla odświeżony embed prestiżu."""
        new_total = await self._upsert_diamonds(
            interaction.guild_id,
            interaction.user.id,
            DAILY_DIAMONDS
        )

        guild_name = interaction.guild.name if interaction.guild else "Serwer"
        avatar_url = interaction.user.display_avatar.url if interaction.user else None

        embed = create_embed(
            title="✨ Nowy diament!",
            description=(
                f"Do Twojego konta został pomyślnie dodany nowy diament!\n"
            ),
            color=COLOR_DIAMOND,
            fields=[
                {"name": "💎 OTRZYMANO", "value": f"**+{DAILY_DIAMONDS} {DIAMOND_EMOJI}**", "inline": True},
                {"name": "📊 STAN KONTA", "value": f"**{new_total} {DIAMOND_EMOJI}**", "inline": True},
            ],
            author_name="SYSTEM DIAMENTÓW • KOPALNIA",
            author_icon_url=avatar_url,
            footer_text=f"Wpisz /topdiax, aby sprawdzić ranking • {guild_name}"
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="topdiax",
        description="Ranking diamentów na serwerze. 💎"
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

        guild_name = interaction.guild.name if interaction.guild else "Serwer"

        if not rows:
            empty_embed = create_embed(
                title=f"{DIAMOND_EMOJI} Ranking Diamentów — {guild_name}",
                description="Nikt jeszcze nie zebrał żadnych diamentów na tym serwerze.\nUżyj komendy `/diament`, aby zdobyć pierwszy kryształ!",
                color=COLOR_DIAMOND,
                footer_text=f"Bądź pierwszy na podium! • {guild_name}"
            )
            await interaction.response.send_message(embed=empty_embed)
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        total_server_diamonds = sum(row["diamonds"] for row in rows)

        for idx, row in enumerate(rows):
            user_nick = await self._resolve_user_name(interaction.guild, row["user_id"])

            if idx < 3:
                prefix = medals[idx]
                lines.append(f"{prefix} **#{idx + 1}** **{user_nick}** — **{row['diamonds']:,} {DIAMOND_EMOJI}**")
            else:
                lines.append(f"▫️ **#{idx + 1}** {user_nick} — **{row['diamonds']:,} {DIAMOND_EMOJI}**")

        embed = create_embed(
            title=f"🏆 Top 10 Posiadaczy Diamentów — {guild_name}",
            description=(
                f"Oto najlepsi posiadacze diamentów z organizacji:\n\n"
                + "\n".join(lines)
            ),
            color=COLOR_GOLD,
            fields=[
                {
                    "name": "💎 Łącznie w TOP 10",
                    "value": f"**{total_server_diamonds:,} {DIAMOND_EMOJI}**",
                    "inline": False
                }
            ],
            author_name=f"RANKING DIAMENTÓW • {guild_name.upper()}",
            footer_text="Użyj /diament, aby powiększyć swój stan posiadania!"
        )

        await interaction.response.send_message(embed=embed)

    # ── Komendy administracyjne ───────────────────────────────────────────────

    diament_group = app_commands.Group(
        name="diamentadmin",
        description="Zarządzanie diamentami dla administratorów",
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

        embed = create_embed(
            title="🛡️ Pomyślnie Dodano Diamenty",
            description=f"Administrator zmodyfikował stan diamentów dla gracza {target.mention}.",
            color=COLOR_ADMIN,
            fields=[
                {"name": "👤 UŻYTKOWNIK", "value": target.mention, "inline": True},
                {"name": "⚡ DODANA KWOTA", "value": f"**+{ilosc:,} {DIAMOND_EMOJI}**", "inline": True},
                {"name": "💎 NOWY STAN KONTA", "value": f"**{new_total:,} {DIAMOND_EMOJI}**", "inline": False},
            ],
            author_name="ADMINISTRACJA • ZARZĄDZANIE DIAMENTAMI",
            footer_text=f"Wykonane przez: {interaction.user.display_name}"
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
        actual_removed = current - new_total

        embed = create_embed(
            title="🛡️ Pomyślnie Usunięto Diamenty",
            description=f"Administrator odjął diamenty z konta gracza {target.mention}.",
            color=COLOR_ADMIN,
            fields=[
                {"name": "👤 UŻYTKOWNIK", "value": target.mention, "inline": True},
                {"name": "⚡ ODJĘTA KWOTA", "value": f"**-{actual_removed:,} {DIAMOND_EMOJI}**", "inline": True},
                {"name": "💎 NOWY STAN KONTA", "value": f"**{new_total:,} {DIAMOND_EMOJI}**", "inline": False},
            ],
            author_name="ADMINISTRACJA • ZARZĄDZANIE DIAMENTAMI",
            footer_text=f"Wykonane przez: {interaction.user.display_name}"
        )
        await interaction.response.send_message(embed=embed)
        logger.info(
            f"[Diamonds] {interaction.user} ({interaction.user.id}) usunął "
            f"{actual_removed} diamentów od {target} ({target.id}) "
            f"na serwerze {interaction.guild_id}. Nowy stan: {new_total}."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiamondsCog(bot))
