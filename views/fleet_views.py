"""
Widoki Discord UI (przyciski, paginacja, select menu) dla modułu floty pojazdów Team77.
Generuje 2-kolumnową grafikę kart floty (Pillow) 1:1 zgodną z podglądem HTML/CSS.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

from services.fleet_image_service import generate_fleet_cards_image

if TYPE_CHECKING:
    from services.fleet_service import FleetService

ITEMS_PER_PAGE = 4


def get_vehicle_emoji(model_name: str) -> str:
    """Zwraca dedykowany emoji w zależności od typu pojazdu."""
    m = model_name.lower()
    if any(x in m for x in ("nrg", "fcr", "pcj", "bf-400", "sanchez", "freeway", "wayfarer", "faggio", "pizzaboy")):
        return "🏍️"
    if any(x in m for x in ("dinghy", "speeder", "marquis", "jetmax", "tropic", "coastguard", "reefer", "predator")):
        return "🛥️"
    if any(x in m for x in ("rancher", "huntley", "landstalker", "patriot", "mesa", "sandking", "yosemite", "bobcat")):
        return "🚙"
    if any(x in m for x in ("benson", "mule", "yankee", "boxville", "rumpo", "burrito", "pony", "camper")):
        return "🚚"
    if any(x in m for x in ("sultan", "buffalo", "jester", "elegy", "euros", "cheetah", "bullet", "turismo", "infernus", "flash", "comet", "zr-350", "banshee", "sabre", "alpha", "zenith", "fortune")):
        return "🏎️"
    return "🚗"


def build_fleet_embed(
    vehicles: list[dict[str, Any]],
    page: int,
    total_pages: int,
    search_query: str | None = None,
    image_filename: str | None = "flota.png",
    has_image: bool = True,
) -> discord.Embed:
    """Tworzy ulepszony embed ze stroną floty organizacji z wielkimi banerami kart."""
    gas_count = sum(1 for x in vehicles if x.get("fuel") == "Benzyna")
    diesel_count = sum(1 for x in vehicles if x.get("fuel") == "Diesel")
    other_count = len(vehicles) - gas_count - diesel_count

    stats_line = f"🏎️ **{len(vehicles)}** aut  │  ⛽ **{gas_count}** benzyna  │  🛢️ **{diesel_count}** diesel"
    if other_count > 0:
        stats_line += f"  │  ⚡ **{other_count}** inne"

    embed = discord.Embed(
        title="🚗 Flota Pojazdów Organizacji Team77",
        description=(
            f"{stats_line}\n"
            f"Oficjalna baza pobrana na żywo z **[ProjectRPG.pl/vehicles](https://projectrpg.pl/vehicles/)**.\n"
            f"-# 💡 *Wybierz pojazd z poniższego menu, aby wyświetlić tuning i historię.*"
        ),
        color=0xE67E22,
    )
    if search_query:
        embed.description = f"🔍 **Wyniki wyszukiwania dla:** `{search_query}` ({len(vehicles)} aut)\n\n" + embed.description

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = vehicles[start_idx:end_idx]

    if not page_items:
        embed.add_field(name="Brak wyników", value="Nie znaleziono pojazdów spełniających podane kryteria.", inline=False)
        return embed

    if has_image and image_filename:
        embed.set_image(url=f"attachment://{image_filename}")
    else:
        for idx, v in enumerate(page_items, start_idx + 1):
            cat_emoji = get_vehicle_emoji(v.get("model", ""))
            fuel_badge = "⛽" if v.get("fuel") == "Benzyna" else "🛢️"
            cap = f" ({v['capacity']})" if v.get("capacity") else ""
            engine_str = f"{fuel_badge} {v.get('fuel', '—')}{cap}"

            embed.add_field(
                name=f"{cat_emoji} #{v['id']} • {v['model']}",
                value=(
                    f"> 👤 **Właściciel:** `{v['owner']}`\n"
                    f"> ⚙️ **Silnik:** {engine_str}"
                ),
                inline=True,
            )

    embed.set_footer(
        text=f"Strona {page + 1} z {max(1, total_pages)} • Team77 ProjectRPG • Ostatnia synchronizacja: przed chwilą",
        icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png",
    )
    return embed


def build_detail_embed(detail: dict[str, Any]) -> discord.Embed:
    """Tworzy embed ze szczegółową specyfikacją techniczną danego pojazdu."""
    model_name = detail.get("model", "Pojazd")
    cat_emoji = get_vehicle_emoji(model_name)

    embed = discord.Embed(
        title=f"{cat_emoji} Specyfikacja Pojazdu #{detail['id']} • {model_name}",
        url=detail.get("url"),
        color=0x3498DB,
    )

    owner_str = detail.get("owner", "—")
    embed.add_field(
        name="📋 Podstawowe Informacje",
        value=(
            f"• 👤 **Właściciel:** `{owner_str}`\n"
            f"• ⚙️ **Silnik:** `{detail.get('engine', '—')}`\n"
            f"• 🛣️ **Przebieg:** `{detail.get('mileage', '—')}`\n"
            f"• 🚘 **Tablica:** `{detail.get('plate', '—')}`\n"
            f"• 🛠️ **Przegląd:** `{detail.get('tech_review', '—')}`\n"
            f"• 🏢 **Przynależność:** `{detail.get('membership', 'Team77')}`"
        ),
        inline=False,
    )

    mech_tuning = detail.get("mechanical_tuning", "").strip() or "Brak modyfikacji mechanicznych (Fabryczny)"
    embed.add_field(
        name="🔧 Tuning Mechaniczny",
        value=f"```\n{mech_tuning}\n```",
        inline=False,
    )

    vis_tuning = detail.get("visual_tuning", "").strip() or "Brak modyfikacji wizualnych"
    embed.add_field(
        name="🎨 Tuning Wizualny",
        value=f"```\n{vis_tuning}\n```",
        inline=False,
    )

    if detail.get("lamp_color") and detail["lamp_color"] != "Brak":
        embed.add_field(name="💡 Kolor Świateł", value=f"`{detail['lamp_color']}`", inline=True)

    last_driver = detail.get("last_driver", "—")
    drivers_val = f"**{last_driver}**"
    if detail.get("recent_drivers"):
        d_lines = [f"{i+1}. `{d}`" for i, d in enumerate(detail["recent_drivers"][:5])]
        drivers_val += "\n" + "\n".join(d_lines)

    embed.add_field(
        name="🕒 Ostatni Kierowcy w Grze",
        value=drivers_val,
        inline=False,
    )

    embed.set_footer(
        text=f"Baza pojazdów ProjectRPG.pl • VUID: #{detail['id']}",
        icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png",
    )
    return embed


def build_stats_embed(vehicles: list[dict[str, Any]]) -> discord.Embed:
    """Tworzy embed z kompleksowym raportem statystycznym floty Team77."""
    total_count = len(vehicles)
    models_counter = Counter(v["model"] for v in vehicles)
    owners_counter = Counter(v["owner"] for v in vehicles)
    fuels_counter = Counter(v.get("fuel", "Nieznane") for v in vehicles)

    top_models = "\n".join(f"• **{m}**: `{c} szt.`" for m, c in models_counter.most_common(6))
    top_owners = "\n".join(f"• **{o}**: `{c} aut`" for o, c in owners_counter.most_common(6))
    fuels_str = "  │  ".join(f"**{f}**: `{c}`" for f, c in fuels_counter.items())

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
    embed.add_field(name="👑 Najwięcej Aut w Org", value=top_owners, inline=True)
    embed.set_footer(
        text="Baza pojazdów ProjectRPG.pl • Team77",
        icon_url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png",
    )
    return embed


class FleetVehicleSelect(ui.Select):
    """Dropdown pozwalający wybrać konkretny pojazd z bieżącej strony z emoji kategorii."""

    def __init__(self, vehicles_on_page: list[dict[str, Any]], fleet_service: FleetService) -> None:
        self.fleet_service = fleet_service
        options = []
        for v in vehicles_on_page[:25]:
            emoji = get_vehicle_emoji(v.get("model", ""))
            label = f"#{v['id']} {v['model'][:25]}"
            fuel_desc = f"{v.get('fuel', '')} ({v.get('capacity', '')})" if v.get("capacity") else v.get("fuel", "")
            desc = f"{v['owner'][:18]} • {fuel_desc}"[:50]
            options.append(discord.SelectOption(label=label, emoji=emoji, value=str(v["id"]), description=desc))

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

    def _render_page_items(self) -> tuple[discord.Embed, discord.File]:
        """Tworzy embed oraz grafikę kart pojazdów dla bieżącej strony z unikalną nazwą pliku (anti-cache)."""
        import time
        start_idx = self.current_page * ITEMS_PER_PAGE
        page_items = self.vehicles[start_idx : start_idx + ITEMS_PER_PAGE]
        buf = generate_fleet_cards_image(page_items)
        fname = f"flota_p{self.current_page}_{int(time.time() * 1000)}.png"
        file = discord.File(buf, filename=fname)
        embed = build_fleet_embed(self.vehicles, self.current_page, self.total_pages, self.search_query, image_filename=fname, has_image=True)
        return embed, file

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
        btn_stats = ui.Button(label="Statystyki 📊", style=discord.ButtonStyle.secondary)

        async def _first(interaction: discord.Interaction):
            self.current_page = 0
            self._update_components()
            embed, file = self._render_page_items()
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

        async def _prev(interaction: discord.Interaction):
            if self.current_page > 0:
                self.current_page -= 1
            self._update_components()
            embed, file = self._render_page_items()
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

        async def _next(interaction: discord.Interaction):
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
            self._update_components()
            embed, file = self._render_page_items()
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

        async def _last(interaction: discord.Interaction):
            self.current_page = self.total_pages - 1
            self._update_components()
            embed, file = self._render_page_items()
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

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
            embed, file = self._render_page_items()
            await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

        async def _show_stats(interaction: discord.Interaction):
            stats_embed = build_stats_embed(self.vehicles)
            await interaction.response.send_message(embed=stats_embed, ephemeral=True)

        btn_first.callback = _first
        btn_prev.callback = _prev
        btn_next.callback = _next
        btn_last.callback = _last
        btn_refresh.callback = _refresh
        btn_stats.callback = _show_stats

        self.add_item(btn_first)
        self.add_item(btn_prev)
        self.add_item(btn_counter)
        self.add_item(btn_next)
        self.add_item(btn_last)
        self.add_item(btn_refresh)
        self.add_item(btn_stats)

        # Select menu dla bieżącej strony
        start_idx = self.current_page * ITEMS_PER_PAGE
        page_items = self.vehicles[start_idx : start_idx + ITEMS_PER_PAGE]
        if page_items:
            select = FleetVehicleSelect(page_items, self.fleet_service)
            self.add_item(select)
