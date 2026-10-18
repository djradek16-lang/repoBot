"""
Moduł floty pojazdów organizacji Team77 (ProjectRPG / MTA:SA).
Pobiera i wyświetla na żywo spis pojazdów należących do organizacji z oficjalnej
bazy pojazdów ProjectRPG (https://projectrpg.pl/vehicles/).
Obsługuje zarówno komendy slash (/flota), komendy hybrydowe, jak i komendy z prefiksem (!flota).
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.fleet_service import FleetService
from views.fleet_views import FleetPaginationView, build_detail_embed, build_fleet_embed

logger = logging.getLogger("AegisBot.Cogs.Fleet")


class FleetCog(commands.Cog, name="fleet"):
    """Komendy przeglądania floty pojazdów organizacji Team77."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = FleetService()
        self.bot.fleet_service = self.service

    async def _show_fleet(self, ctx_or_interaction: commands.Context | discord.Interaction, szukaj: Optional[str] = None) -> None:
        """Wspólna logika wyświetlania floty dla slash i prefix commands."""
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        
        vehicles = await self.service.get_fleet(organization="Team77")
        if not vehicles:
            msg = (
                "❌ Nie udało się pobrać floty z forum ProjectRPG. "
                "Upewnij się, że ciasteczka sesji forum w konfiguracji bota są aktualne."
            )
            if is_interaction:
                await ctx_or_interaction.followup.send(msg, ephemeral=True)
            else:
                await ctx_or_interaction.send(msg)
            return

        filtered = vehicles
        if szukaj:
            q = szukaj.strip().lower()
            filtered = [
                v for v in vehicles
                if q in str(v["id"]) or q in v["model"].lower() or q in v["owner"].lower() or q in v.get("fuel", "").lower()
            ]
            if not filtered:
                msg = (
                    f"🔍 Nie znaleziono żadnego pojazdu pasującego do zapytania: `{szukaj}`.\n"
                    f"Wpisz `/flota` lub `!flota` bez parametrów, aby zobaczyć całą flotę ({len(vehicles)} pojazdów)."
                )
                if is_interaction:
                    await ctx_or_interaction.followup.send(msg, ephemeral=True)
                else:
                    await ctx_or_interaction.send(msg)
                return

        author = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
        guild = ctx_or_interaction.guild
        thumb = guild.icon.url if guild and guild.icon else None
        if not thumb and self.bot.user and self.bot.user.display_avatar:
            thumb = self.bot.user.display_avatar.url

        view = FleetPaginationView(
            filtered,
            self.service,
            search_query=szukaj,
            guild=guild,
            thumbnail_url=thumb,
            author=author,
        )
        embed, file = view._render_page_items()
        
        if is_interaction:
            await ctx_or_interaction.followup.send(embed=embed, file=file, view=view)
        else:
            await ctx_or_interaction.send(embed=embed, file=file, view=view)

    @commands.hybrid_command(
        name="flota",
        aliases=["pojazdy", "fleet"],
        description="Wyświetl listę pojazdów należących do organizacji Team77 z bazy forum ProjectRPG.",
    )
    @app_commands.describe(
        szukaj="Filtruj po ID pojazdu, nazwie modelu (np. Sultan, Buffalo) lub nicku właściciela",
    )
    async def cmd_flota(self, ctx: commands.Context, *, szukaj: Optional[str] = None) -> None:
        """Pobiera i wyświetla flotę Team77 z forum (działa jako /flota oraz !flota)."""
        await ctx.defer()
        if ctx.interaction:
            await self._show_fleet(ctx.interaction, szukaj)
        else:
            await self._show_fleet(ctx, szukaj)

    @commands.hybrid_command(
        name="pojazd_info",
        aliases=["pojazd", "vuid"],
        description="Sprawdź szczegółowe dane, tuning i historię kierowców pojazdu z bazy ProjectRPG.",
    )
    @app_commands.describe(vuid="ID (VUID) pojazdu w świecie gry (np. 1930, 10811)")
    async def cmd_pojazd_info(self, ctx: commands.Context, vuid: int) -> None:
        """Pobiera pełną specyfikację danego pojazdu z bazy forum."""
        await ctx.defer()

        detail = await self.service.get_vehicle_detail(vuid)
        if not detail:
            msg = f"❌ Nie znaleziono danych dla pojazdu o VUID **#{vuid}** lub wystąpił błąd połączenia z forum."
            if ctx.interaction:
                await ctx.interaction.followup.send(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        embed = build_detail_embed(detail)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Otwórz na Forum", url=detail["url"], emoji="🔗"))
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="flota_statystyki",
        aliases=["flotastat", "fleetstats"],
        description="Wyświetl statystyki floty Team77: najpopularniejsze modele, rodzaje silników i właścicieli.",
    )
    async def cmd_flota_statystyki(self, ctx: commands.Context) -> None:
        """Generuje raport statystyczny floty Team77."""
        await ctx.defer()

        vehicles = await self.service.get_fleet(organization="Team77")
        if not vehicles:
            msg = "❌ Brak danych o flocie. Spróbuj ponownie później."
            if ctx.interaction:
                await ctx.interaction.followup.send(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        total_count = len(vehicles)
        models_counter = Counter(v["model"] for v in vehicles)
        owners_counter = Counter(v["owner"] for v in vehicles)
        fuels_counter = Counter(v.get("fuel", "Nieznane") for v in vehicles)

        top_models = "\n".join(f"• **{m}**: `{c} szt.`" for m, c in models_counter.most_common(6))
        top_owners = "\n".join(f"• **{o}**: `{c} aut`" for o, c in owners_counter.most_common(6))
        fuels_str = " | ".join(f"**{f}**: `{c}`" for f, c in fuels_counter.items())

        embed = discord.Embed(
            title="📊 Raport i Statystyki Floty Team77",
            description=(
                f"Łączna wielkość floty organizacji: **{total_count} pojazdów**.\n"
                f"Średnia liczba aut przypisanych do członka: **{total_count / max(1, len(owners_counter)):.1f}**\n\n"
                f"⛽ **Podział rodzajów paliwa:**\n{fuels_str}"
            ),
            color=0x2ECC71,
        )
        embed.add_field(name="🏎️ Najpopularniejsze Modele", value=top_models, inline=True)
        embed.add_field(name="👑 Najwięcej Pojazdów w Org", value=top_owners, inline=True)
        embed.set_footer(
            text="Baza pojazdów ProjectRPG.pl • Team77",
            icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png",
        )
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Ładuje Cog floty do bota."""
    await bot.add_cog(FleetCog(bot))
