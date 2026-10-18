"""
Widoki Discord UI (przyciski, paginacja, select menu) dla modułu floty pojazdów Team77.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

if TYPE_CHECKING:
    from services.fleet_service import FleetService

ITEMS_PER_PAGE = 6


def build_fleet_embed(
    vehicles: list[dict[str, Any]],
    page: int,
    total_pages: int,
    search_query: str | None = None,
) -> discord.Embed:
    """Tworzy estetyczny embed ze stroną floty organizacji."""
    embed = discord.Embed(
        title="🚗 Flota Pojazdów Organizacji Team77",
        description=(
            f"Oficjalna baza pojazdów pobrana na żywo z **[ProjectRPG.pl/vehicles](https://projectrpg.pl/vehicles/)**.\n"
            f"Łącznie w organizacji: **{len(vehicles)}** pojazdów.\n"
            f"> *Wybierz pojazd z poniższego menu, aby sprawdzić jego tuning i dane.*"
        ),
        color=0xE67E22,
    )
    if search_query:
        embed.description = f"🔍 Wyniki wyszukiwania dla: `{search_query}` ({len(vehicles)} aut)\n\n" + embed.description

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = vehicles[start_idx:end_idx]

    if not page_items:
        embed.add_field(name="Brak wyników", value="Nie znaleziono pojazdów spełniających kryteria.", inline=False)
        return embed

    for idx, v in enumerate(page_items, start_idx + 1):
        fuel_badge = "⛽" if v.get("fuel") == "Benzyna" else "🛢️"
        cap = f" ({v['capacity']})" if v.get("capacity") else ""
        engine_str = f"{fuel_badge} {v.get('fuel', '—')}{cap}"
        
        embed.add_field(
            name=f"{idx}. #{v['id']} • {v['model']}",
            value=(
                f"> 👤 **Właściciel:** `{v['owner']}`\n"
                f"> ⚙️ **Silnik:** {engine_str}\n"
                f"> 🔗 **[Zobacz na Forum]({v['detail_url']})**"
            ),
            inline=True,
        )

    embed.set_footer(
        text=f"Strona {page + 1} z {max(1, total_pages)} • Team77 MTA:SA ProjectRPG",
        icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png",
    )
    return embed


def build_detail_embed(detail: dict[str, Any]) -> discord.Embed:
    """Tworzy embed ze szczegółową specyfikacją techniczną danego pojazdu."""
    embed = discord.Embed(
        title=f"🚘 Specyfikacja Pojazdu #{detail['id']} • {detail['model']}",
        url=detail.get("url"),
        color=0x3498DB,
    )
    embed.add_field(
        name="📋 Główne Informacje",
        value=(
            f"• **VUID:** `#{detail['id']}`\n"
            f"• **Model:** `{detail['model']}`\n"
            f"• **Silnik:** `{detail['engine']}`\n"
            f"• **Przebieg:** `{detail['mileage']}`\n"
            f"• **Tablica:** `{detail['plate']}`\n"
            f"• **Przegląd techniczny:** `{detail['tech_review']}`\n"
            f"• **Przynależność:** `{detail['membership']}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="👤 Ostatni Kierowca",
        value=f"**{detail['last_driver']}**",
        inline=False,
    )
    embed.add_field(
        name="🎨 Tuning Wizualny",
        value=f"```\n{detail['visual_tuning']}\n```",
        inline=False,
    )
    embed.add_field(
        name="🔧 Tuning Mechaniczny",
        value=f"```\n{detail['mechanical_tuning']}\n```",
        inline=False,
    )
    if detail.get("lamp_color"):
        embed.add_field(name="💡 Kolor Świateł", value=f"`{detail['lamp_color']}`", inline=True)

    if detail.get("recent_drivers"):
        drivers_list = "\n".join(f"• {d}" for d in detail["recent_drivers"])
        embed.add_field(name="🕒 Historia Ostatnich Kierowców", value=drivers_list, inline=False)

    embed.set_footer(
        text="Dane pobrane na żywo z bazy pojazdów ProjectRPG.pl",
        icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png",
    )
    return embed


class FleetVehicleSelect(ui.Select):
    """Dropdown pozwalający wybrać konkretny pojazd z bieżącej strony, by podejrzeć detale."""

    def __init__(self, vehicles_on_page: list[dict[str, Any]], fleet_service: FleetService) -> None:
        self.fleet_service = fleet_service
        options = []
        for v in vehicles_on_page[:25]:
            label = f"#{v['id']} {v['model'][:25]}"
            desc = f"Właściciel: {v['owner'][:20]} • {v.get('fuel', '')}"
            options.append(discord.SelectOption(label=label, value=str(v["id"]), description=desc[:50]))

        super().__init__(
            placeholder="🔎 Wybierz pojazd z tej strony, by sprawdzić szczegóły...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        vuid = int(self.values[0])
        detail = await self.fleet_service.get_vehicle_detail(vuid)
        if not detail:
            await interaction.followup.send(f"❌ Nie udało się pobrać szczegółów dla pojazdu #{vuid}.", ephemeral=True)
            return

        embed = build_detail_embed(detail)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Otwórz na Forum", url=detail["url"], emoji="🔗"))
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class FleetPaginationView(ui.View):
    """Widok paginacji dla listy floty Team77 z przyciskami i select menu."""

    def __init__(
        self,
        vehicles: list[dict[str, Any]],
        fleet_service: FleetService,
        search_query: str | None = None,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.vehicles = vehicles
        self.fleet_service = fleet_service
        self.search_query = search_query
        self.current_page = 0
        self.total_pages = max(1, math.ceil(len(vehicles) / ITEMS_PER_PAGE))
        self._update_components()

    def _update_components(self) -> None:
        """Odświeża stan przycisków i selektora dla bieżącej strony."""
        self.clear_items()

        # Przyciski nawigacji
        btn_first = ui.Button(label="⏮", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0))
        btn_prev = ui.Button(label="◀ Poprzednia", style=discord.ButtonStyle.primary, disabled=(self.current_page == 0))
        btn_counter = ui.Button(
            label=f"{self.current_page + 1} / {self.total_pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        btn_next = ui.Button(
            label="Następna ▶",
            style=discord.ButtonStyle.primary,
            disabled=(self.current_page >= self.total_pages - 1),
        )
        btn_last = ui.Button(
            label="⏭",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_page >= self.total_pages - 1),
        )
        btn_refresh = ui.Button(label="Odśwież 🔄", style=discord.ButtonStyle.success)

        async def _first(interaction: discord.Interaction):
            self.current_page = 0
            self._update_components()
            await interaction.response.edit_message(
                embed=build_fleet_embed(self.vehicles, self.current_page, self.total_pages, self.search_query),
                view=self,
            )

        async def _prev(interaction: discord.Interaction):
            if self.current_page > 0:
                self.current_page -= 1
            self._update_components()
            await interaction.response.edit_message(
                embed=build_fleet_embed(self.vehicles, self.current_page, self.total_pages, self.search_query),
                view=self,
            )

        async def _next(interaction: discord.Interaction):
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
            self._update_components()
            await interaction.response.edit_message(
                embed=build_fleet_embed(self.vehicles, self.current_page, self.total_pages, self.search_query),
                view=self,
            )

        async def _last(interaction: discord.Interaction):
            self.current_page = self.total_pages - 1
            self._update_components()
            await interaction.response.edit_message(
                embed=build_fleet_embed(self.vehicles, self.current_page, self.total_pages, self.search_query),
                view=self,
            )

        async def _refresh(interaction: discord.Interaction):
            await interaction.response.defer()
            refreshed = await self.fleet_service.get_fleet(organization="Team77", force_refresh=True)
            if self.search_query:
                q = self.search_query.lower()
                self.vehicles = [
                    v for v in refreshed
                    if q in str(v["id"]) or q in v["model"].lower() or q in v["owner"].lower()
                ]
            else:
                self.vehicles = refreshed
            self.total_pages = max(1, math.ceil(len(self.vehicles) / ITEMS_PER_PAGE))
            self.current_page = min(self.current_page, self.total_pages - 1)
            self._update_components()
            await interaction.edit_original_response(
                embed=build_fleet_embed(self.vehicles, self.current_page, self.total_pages, self.search_query),
                view=self,
            )

        btn_first.callback = _first
        btn_prev.callback = _prev
        btn_next.callback = _next
        btn_last.callback = _last
        btn_refresh.callback = _refresh

        self.add_item(btn_first)
        self.add_item(btn_prev)
        self.add_item(btn_counter)
        self.add_item(btn_next)
        self.add_item(btn_last)
        self.add_item(btn_refresh)

        # Select menu dla bieżącej strony
        start_idx = self.current_page * ITEMS_PER_PAGE
        page_items = self.vehicles[start_idx : start_idx + ITEMS_PER_PAGE]
        if page_items:
            select = FleetVehicleSelect(page_items, self.fleet_service)
            self.add_item(select)
