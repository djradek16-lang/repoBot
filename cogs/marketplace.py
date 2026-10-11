"""
Zaawansowany moduł Rynku Motoryzacyjnego dla serwera MTA ProjectRPG (/ogloszenie).

Umożliwia członkom organizacji:
- Tworzenie profesjonalnych ogłoszeń pojazdów w interaktywnym Studio Ogłoszeń (na żywo).
- Automatyczne wyliczanie wartości zainwestowanego tuningu na podstawie oficjalnego cennika warsztatów ProjectRPG.
- Szybkie pakiety modyfikacji (Full Tune FT, Prawie FT, Stock) 1 kliknięciem.
- Dołączanie zdjęć pojazdu oraz nicku MTA sprzedającego.
- Automatyczne tworzenie wątku dyskusyjnego pod każdym ogłoszeniem (dla ofert i pytań).
- Składanie ofert cenowych przez przycisk 'Złóż ofertę' z bezpośrednim powiadomieniem DM dla sprzedającego.
- Pełną trwałość w bazie danych SQLite (nawet po restartach bota) dzięki trwałym widokom (Persistent Views).
- Zarządzanie ogłoszeniami (oznaczanie jako sprzedane/zarezerwowane, podbijanie/bump z cooldownem, usuwanie).
- Przeglądarkę ofert rynkowych z filtrami (/ogloszenia) oraz panel swoich ofert (/moje_ogloszenia).
"""

import os
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import create_embed, info_embed, success_embed, error_embed, warning_embed
import config

logger = logging.getLogger("AegisBot.Cogs.Marketplace")

# ---------------------------------------------------------------------------
# Katalog tuningu — Oficjalny cennik warsztatów ProjectRPG (ID: cena w PLN)
# ---------------------------------------------------------------------------

TUNING_WIZUALNY: dict[str, list[tuple[int, int]]] = {
    "Spoiler": [
        (1000, 7000), (1001, 9000), (1002, 6000), (1003, 10000), (1014, 5400),
        (1015, 4000), (1016, 4000), (1023, 8500), (1049, 4000), (1050, 3200),
        (1058, 3800), (1060, 5500), (1138, 8700), (1139, 9500), (1146, 7800),
        (1147, 7500), (1158, 6000), (1162, 5600), (1163, 11500),
    ],
    "Progi": [
        (1007, 2100), (1026, 3500), (1030, 3000), (1036, 4000), (1039, 3600),
        (1042, 1600), (1047, 3000), (1048, 2800), (1056, 3400), (1057, 2800),
        (1069, 3400), (1090, 2700), (1093, 2400), (1101, 3200), (1102, 2300),
        (1106, 2500), (1107, 2100), (1118, 2800), (1119, 3300), (1134, 1500),
    ],
    "Wydech": [
        (1018, 2400), (1019, 1800), (1020, 1600), (1021, 1400), (1022, 1200),
        (1028, 2400), (1029, 3400), (1034, 2200), (1037, 3600), (1043, 1600),
        (1044, 1200), (1045, 2800), (1046, 2100), (1059, 2700), (1064, 1900),
        (1065, 2000), (1066, 2800), (1089, 2600), (1092, 1800), (1104, 2800),
        (1105, 3200), (1113, 2300), (1114, 4100), (1126, 2300), (1127, 2500),
        (1129, 2800), (1132, 3400), (1135, 2000), (1136, 2500),
    ],
    "Przedni zderzak": [
        (1100, 3200), (1117, 2800), (1152, 6500), (1153, 6000), (1155, 7000),
        (1157, 6500), (1160, 7500), (1165, 5000), (1166, 4700), (1169, 8500),
        (1170, 7000), (1171, 8000), (1172, 6500), (1173, 5500), (1174, 2000),
        (1175, 1800), (1179, 3800), (1181, 3100), (1182, 3300), (1185, 2800),
        (1188, 3800), (1189, 4100), (1190, 1800), (1191, 2000),
    ],
    "Tylny zderzak": [
        (1140, 5500), (1141, 6000), (1148, 5000), (1149, 5500), (1150, 3500),
        (1151, 4000), (1154, 5000), (1156, 4750), (1159, 5250), (1161, 4500),
        (1167, 4250), (1168, 4000), (1176, 1600), (1177, 1400), (1178, 2800),
        (1180, 3800), (1183, 3300), (1184, 3100), (1186, 4400), (1187, 4000),
        (1192, 2400), (1193, 2600),
    ],
    "Dach": [
        (1006, 900), (1032, 1800), (1033, 2600), (1035, 2300), (1038, 1600),
        (1053, 1800), (1054, 1100), (1055, 1200), (1061, 1500), (1067, 1400),
        (1068, 2200), (1088, 1300), (1091, 1900), (1103, 3000), (1128, 7000),
    ],
    "Maska": [(1011, 3900), (1012, 4200), (1142, 3500), (1144, 3200)],
    "Tylne lampy": [(1109, 2800), (1110, 2300)],
    "Przednie lampy": [(1115, 2800), (1116, 3500)],
    "Reflektory": [(1013, 500), (1024, 500)],
    "Wenty": [(1004, 4200), (1005, 3900)],
}

TUNING_MECHANICZNY: dict[str, dict[str, int]] = {
    "ECU": {"V1": 10500, "V2": 18000, "V3": 30000, "V4": 40000},
    "Turbo": {"V1": 15000, "V2": 25000, "V3": 40000, "V4": 50000, "V5": 60000},
    "Zestaw": {"Uliczny": 50000, "Wyścigowy": 100000, "Drifterski": 150000, "Torowy": 250000},
    "C.F.I.": {"V1": 125000, "V2": 150000, "V3": 175000, "V4": 225000, "V5": 250000},
    "LPG": {"25L": 20000, "50L": 25000, "75L": 35000},
    "Bak paliwa": {"100L": 45000, "125L": 55000, "150L": 65000},
    "Zmiana napędu": {"RWD": 30000, "AWD": 50000, "FWD": 20000, "MZN": 75000},
    "Inne": {
        "Radio": 500, "CB-radio": 1500, "Ogranicznik prędkości": 2000,
        "Wykrywacz fotoradarów": 2500, "System ABS": 15000,
        "Gwintowane zawieszenie": 32500, "Hydraulika": 45000,
    },
}

# ---------------------------------------------------------------------------
# Motywy graficzne, etykiety i statusy
# ---------------------------------------------------------------------------

THEMES = {
    "SPRZEDAM": {
        "title": "💰 SPRZEDAM",
        "color": 0x2ECC71,  # Szmaragdowa zieleń
        "badge": "🟢 W SPRZEDAŻY",
    },
    "KUPIE": {
        "title": "🛒 KUPIĘ",
        "color": 0x3498DB,  # Szafirowy błękit
        "badge": "🔵 POSZUKIWANY",
    },
    "ZAMIENIE": {
        "title": "🔄 ZAMIENIĘ",
        "color": 0x9B59B6,  # Królewski fiolet
        "badge": "🟣 ZAMIANA",
    },
    "WYNAJME": {
        "title": "🔑 WYNAJMĘ",
        "color": 0xE67E22,  # Ciepły bursztyn
        "badge": "🟠 WYNAJEM",
    },
    "SOLD": {
        "title": "🔴 SPRZEDANE",
        "color": 0x7F8C8D,  # Ciemnoszary / archiwalny
        "badge": "🔒 ZAKOŃCZONE",
    },
    "RESERVED": {
        "title": "🟡 ZAREZERWOWANE",
        "color": 0xF1C40F,  # Złocisty żółty
        "badge": "⏳ REZERWACJA",
    },
}

def _price_str(v: int | float) -> str:
    """Formatuje liczbę jako walutę ProjectRPG (np. 1 250 000 PLN)."""
    try:
        val = int(v)
        return f"{val:,}".replace(",", " ") + " PLN"
    except Exception:
        return f"{v} PLN"


def _clean_price_input(text: str) -> str:
    """Oczyszcza wprowadzoną kwotę ceny (np. 1.2kk -> 1 200 000, 500k -> 500 000)."""
    t = text.strip().lower().replace(" ", "").replace("pln", "").replace("zł", "")
    if t.endswith("kk"):
        try:
            num = float(t[:-2].replace(",", "."))
            return str(int(num * 1_000_000))
        except ValueError:
            pass
    elif t.endswith("k"):
        try:
            num = float(t[:-1].replace(",", "."))
            return str(int(num * 1_000))
        except ValueError:
            pass
    # Usuń ewentualne kropki/przecinki jeśli to czysty ciąg cyfr
    digits = re.sub(r"[^\d]", "", t)
    return digits if digits else text.strip()


# ---------------------------------------------------------------------------
# Persistent View pod opublikowanym ogłoszeniem (działa ZAWSZE, nawet po restartach!)
# ---------------------------------------------------------------------------

class ListingActiveView(discord.ui.View):
    """
    Trwały widok Discord.py przypisany do każdego opublikowanego ogłoszenia.
    Obsługuje składanie ofert, kontakt ze sprzedającym oraz panel zarządzania.
    """

    def __init__(self, cog: "MarketplaceCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Złóż ofertę",
        style=discord.ButtonStyle.success,
        emoji="💰",
        custom_id="marketplace:offer"
    )
    async def offer_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        listing = await self.cog.get_listing_by_message(interaction.message.id)
        if not listing:
            await interaction.response.send_message(
                embed=error_embed("Błąd ogłoszenia", "Nie znaleziono danych tego ogłoszenia w bazie."),
                ephemeral=True
            )
            return

        if listing["status"] in ("SOLD", "CLOSED"):
            await interaction.response.send_message(
                embed=warning_embed("Ogłoszenie zakończone", "To ogłoszenie zostało już sprzedane lub zamknięte."),
                ephemeral=True
            )
            return

        if interaction.user.id == listing["seller_id"]:
            await interaction.response.send_message(
                embed=warning_embed("Własne ogłoszenie", "Nie możesz składać ofert na własne ogłoszenie!"),
                ephemeral=True
            )
            return

        await interaction.response.send_modal(MakeOfferModal(self.cog, listing))


    @discord.ui.button(
        label="Zarządzaj",
        style=discord.ButtonStyle.primary,
        emoji="⚙️",
        custom_id="marketplace:manage"
    )
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        listing = await self.cog.get_listing_by_message(interaction.message.id)
        if not listing:
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Nie znaleziono ogłoszenia."),
                ephemeral=True
            )
            return

        is_seller = interaction.user.id == listing["seller_id"]
        is_admin = False
        if isinstance(interaction.user, discord.Member):
            is_admin = interaction.user.guild_permissions.administrator or interaction.user.id == config.OWNER_ID

        if not (is_seller or is_admin):
            await interaction.response.send_message(
                embed=error_embed("Brak uprawnień", "Tylko autor ogłoszenia lub administracja serwera może nim zarządzać."),
                ephemeral=True
            )
            return

        view = ListingManageView(self.cog, listing)
        await interaction.response.send_message(
            embed=info_embed(
                f"⚙️ Zarządzanie: {listing['model']} (#{listing['vuid']})",
                "Wybierz akcję, którą chcesz wykonać na tym ogłoszeniu:"
            ),
            view=view,
            ephemeral=True
        )


# ---------------------------------------------------------------------------
# Panel Zarządzania Ogłoszeniem (dla Autora / Admina)
# ---------------------------------------------------------------------------

class ListingManageView(discord.ui.View):
    def __init__(self, cog: "MarketplaceCog", listing: dict) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.listing = listing

    @discord.ui.button(label="Oznacz jako Sprzedane", emoji="✅", style=discord.ButtonStyle.success)
    async def mark_sold(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_listing_status(self.listing["id"], "SOLD")
        await self.cog.refresh_listing_message(self.listing["id"])
        await interaction.response.edit_message(
            embed=success_embed("Zaktualizowano!", "Ogłoszenie zostało oznaczone jako **SPRZEDANE**! Gratulacje transakcji."),
            view=None
        )

    @discord.ui.button(label="Zarezerwuj", emoji="🟡", style=discord.ButtonStyle.secondary)
    async def mark_reserved(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_listing_status(self.listing["id"], "RESERVED")
        await self.cog.refresh_listing_message(self.listing["id"])
        await interaction.response.edit_message(
            embed=success_embed("Zarezerwowano", "Pojazd został oznaczony jako **ZAREZERWOWANY**."),
            view=None
        )

    @discord.ui.button(label="Wznów (Aktywne)", emoji="🟢", style=discord.ButtonStyle.secondary)
    async def mark_active(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_listing_status(self.listing["id"], "ACTIVE")
        await self.cog.refresh_listing_message(self.listing["id"])
        await interaction.response.edit_message(
            embed=success_embed("Wznowiono", "Ogłoszenie jest ponownie **AKTYWNE**."),
            view=None
        )

    @discord.ui.button(label="Podbij (Bump)", emoji="📢", style=discord.ButtonStyle.primary)
    async def bump_listing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bumped_at_str = self.listing.get("bumped_at")
        can_bump = True
        diff_str = ""

        if bumped_at_str:
            try:
                # Format SQLite TIMESTAMP
                last_bump = datetime.fromisoformat(str(bumped_at_str).replace(" ", "T"))
                cooldown_hours = 4
                next_bump = last_bump + timedelta(hours=cooldown_hours)
                now = datetime.now()
                if now < next_bump:
                    can_bump = False
                    remaining = next_bump - now
                    minutes = int(remaining.total_seconds() // 60)
                    hours = minutes // 60
                    mins = minutes % 60
                    diff_str = f"{hours}h {mins}m" if hours else f"{mins} min"
            except Exception:
                pass

        if not can_bump:
            await interaction.response.edit_message(
                embed=warning_embed("Cooldown podbicia", f"To ogłoszenie możesz podbić ponownie za **{diff_str}**."),
                view=None
            )
            return

        await self.cog.bump_listing(self.listing)
        await interaction.response.edit_message(
            embed=success_embed("Podbito ogłoszenie!", "Twoje ogłoszenie zostało pomyślnie odświeżone i podbite!"),
            view=None
        )

    @discord.ui.button(label="Usuń ogłoszenie", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete_listing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.delete_listing(self.listing["id"])
        await interaction.response.edit_message(
            embed=success_embed("Usunięto", "Ogłoszenie zostało usunięte z rynku i bazy danych."),
            view=None
        )


# ---------------------------------------------------------------------------
# Modal składania oferty przez kupującego
# ---------------------------------------------------------------------------

class MakeOfferModal(discord.ui.Modal, title="💰 Złóż Ofertę na Pojazd"):
    def __init__(self, cog: "MarketplaceCog", listing: dict) -> None:
        super().__init__()
        self.cog = cog
        self.listing = listing

        self.buyer_nick = discord.ui.TextInput(
            label="Twój Nick w MTA (ProjectRPG)",
            placeholder="np. Kowalski",
            required=True,
            max_length=30
        )
        self.amount = discord.ui.TextInput(
            label="Twoja Oferta w PLN (lub propozycja wymiany)",
            placeholder="np. 1 150 000 PLN lub Sultan + dopłata",
            required=True,
            max_length=40
        )
        self.message = discord.ui.TextInput(
            label="Wiadomość do sprzedającego (opcjonalnie)",
            placeholder="np. Jestem na serwerze codziennie po 18:00, chętnie wezmę od ręki!",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=400
        )
        self.add_item(self.buyer_nick)
        self.add_item(self.amount)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        buyer_nick = str(self.buyer_nick.value).strip()
        amount = str(self.amount.value).strip()
        msg_text = str(self.message.value).strip() if self.message.value else None

        # Zapisz ofertę w bazie danych
        await self.cog.bot.db.execute(
            """
            INSERT INTO marketplace_offers (listing_id, buyer_id, buyer_nick, offer_amount, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.listing["id"], interaction.user.id, buyer_nick, amount, msg_text)
        )

        seller = self.cog.bot.get_user(self.listing["seller_id"])
        guild = interaction.guild
        channel = guild.get_channel(self.listing["channel_id"]) if guild else None
        jump_url = f"https://discord.com/channels/{self.listing['guild_id']}/{self.listing['channel_id']}/{self.listing['message_id']}"

        # Wyślij powiadomienie DM do sprzedającego
        if seller:
            try:
                dm_embed = create_embed(
                    title="💰 Nowa Oferta Kupna na Rynku!",
                    description=(
                        f"Gracz **{buyer_nick}** ({interaction.user.mention}) złożył ofertę na Twój pojazd "
                        f"**{self.listing['model']}** (VUID: `#{self.listing['vuid']}`)."
                    ),
                    color=config.SUCCESS_COLOR,
                    fields=[
                        {"name": "Zaoferowana kwota / propozycja", "value": f"**{amount}**", "inline": True},
                        {"name": "Nick w grze MTA", "value": f"`{buyer_nick}`", "inline": True},
                        {"name": "Wiadomość", "value": f"> {msg_text}" if msg_text else "*Brak dodatkowej wiadomości*", "inline": False},
                        {"name": "Przejdź do ogłoszenia", "value": f"[Kliknij tutaj, aby zobaczyć ogłoszenie]({jump_url})", "inline": False}
                    ]
                )
                await seller.send(embed=dm_embed)
            except Exception as e:
                logger.warning(f"Nie udało się wysłać DM do sprzedającego {seller.id}: {e}")

        # Jeśli istnieje wątek dyskusyjny ogłoszenia, opublikuj ofertę w wątku
        if self.listing.get("thread_id") and guild:
            thread = guild.get_thread(self.listing["thread_id"])
            if thread:
                try:
                    thread_embed = create_embed(
                        title="💰 Nowa oferta kupna!",
                        description=f"Gracz **{buyer_nick}** ({interaction.user.mention}) złożył ofertę na kwotę: **{amount}**",
                        color=config.INFO_COLOR,
                        fields=[{"name": "Notatka", "value": f"> {msg_text}", "inline": False}] if msg_text else None
                    )
                    await thread.send(embed=thread_embed)
                except Exception as e:
                    logger.debug(f"Błąd wysyłania oferty do wątku: {e}")

        await interaction.response.send_message(
            embed=success_embed(
                "Oferta wysłana!",
                f"Twoja oferta w wysokości **{amount}** została pomyślnie przekazana sprzedającemu (<@{self.listing['seller_id']}>)."
            ),
            ephemeral=True
        )


# ---------------------------------------------------------------------------
# Studio Ogłoszeń — Interaktywne Pulpit Projektowania
# ---------------------------------------------------------------------------

class VehicleDataModal(discord.ui.Modal, title="🚗 Dane Podstawowe Pojazdu"):
    def __init__(self, session: dict, on_update) -> None:
        super().__init__()
        self.session = session
        self.on_update = on_update

        self.model = discord.ui.TextInput(
            label="Model pojazdu",
            placeholder="np. Sultan SB-1, Infernus, Buffalo",
            default=session.get("model", ""),
            required=True,
            max_length=50
        )
        self.vuid = discord.ui.TextInput(
            label="VUID (ID pojazdu na serwerze)",
            placeholder="np. 43861",
            default=session.get("vuid", ""),
            required=True,
            max_length=12
        )
        self.silnik = discord.ui.TextInput(
            label="Silnik / Pojemność",
            placeholder="np. 3.7dm³ (Benzyna) / 2.2dm³ (Diesel)",
            default=session.get("silnik", ""),
            required=True,
            max_length=35
        )
        self.cena = discord.ui.TextInput(
            label="Cena (PLN)",
            placeholder="np. 1 250 000 lub 1.25kk",
            default=session.get("cena", ""),
            required=True,
            max_length=20
        )
        self.przebieg = discord.ui.TextInput(
            label="Przebieg (opcjonalnie)",
            placeholder="np. 45 200 km",
            default=session.get("przebieg", "") or "",
            required=False,
            max_length=20
        )

        self.add_item(self.model)
        self.add_item(self.vuid)
        self.add_item(self.silnik)
        self.add_item(self.cena)
        self.add_item(self.przebieg)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session["model"] = str(self.model.value).strip()
        self.session["vuid"] = str(self.vuid.value).strip()
        self.session["silnik"] = str(self.silnik.value).strip()
        raw_cena = str(self.cena.value).strip()
        self.session["cena"] = _clean_price_input(raw_cena)
        self.session["przebieg"] = str(self.przebieg.value).strip() if self.przebieg.value else ""
        await self.on_update(interaction)


class ContactImageModal(discord.ui.Modal, title="📸 Zdjęcie i Nick w grze"):
    def __init__(self, session: dict, on_update) -> None:
        super().__init__()
        self.session = session
        self.on_update = on_update

        self.nick = discord.ui.TextInput(
            label="Twój Nick w MTA (ProjectRPG)",
            placeholder="np. Kowalski",
            default=session.get("contact_nick", ""),
            required=False,
            max_length=30
        )
        self.image_url = discord.ui.TextInput(
            label="Link do zdjęcia pojazdu (Imgur / Discord)",
            placeholder="https://i.imgur.com/example.png",
            default=session.get("image_url", ""),
            required=False,
            max_length=250
        )

        self.add_item(self.nick)
        self.add_item(self.image_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session["contact_nick"] = str(self.nick.value).strip()
        self.session["contact_phone"] = ""
        url = str(self.image_url.value).strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            url = f"https://{url}"
        self.session["image_url"] = url
        await self.on_update(interaction)


class NotesModal(discord.ui.Modal, title="📝 Dodatkowy Opis / Informacje"):
    def __init__(self, session: dict, on_update) -> None:
        super().__init__()
        self.session = session
        self.on_update = on_update

        self.notes = discord.ui.TextInput(
            label="Opis / Warunki wymiany / Dodatkowe uwagi",
            placeholder="np. Tylko gotówka, nie interesują mnie wymiany na motocykle. Możliwa jazda próbna!",
            style=discord.TextStyle.paragraph,
            default=session.get("notes", ""),
            required=False,
            max_length=600
        )
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session["notes"] = str(self.notes.value).strip()
        await self.on_update(interaction)


class LightingModal(discord.ui.Modal, title="💡 Światła, Licznik i Limitki"):
    def __init__(self, session: dict, on_update) -> None:
        super().__init__()
        self.session = session
        self.on_update = on_update

        self.swiatla = discord.ui.TextInput(
            label="Kolor świateł",
            placeholder="np. Lazurowe, Czerwone, Herbaciane",
            default=session.get("kolor_swiatel", ""),
            required=False,
            max_length=35
        )
        self.swiatla_limit = discord.ui.TextInput(
            label="Czy światła są LIMITOWANE? (Tak / Nie)",
            placeholder="Wpisz: Tak lub Nie",
            default="Tak" if session.get("limit_swiatla") else "Nie",
            required=False,
            max_length=10
        )
        self.licznik = discord.ui.TextInput(
            label="Kolor licznika",
            placeholder="np. Lazurowy, Pomarańczowy",
            default=session.get("kolor_licznika", ""),
            required=False,
            max_length=35
        )
        self.licznik_limit = discord.ui.TextInput(
            label="Czy licznik jest LIMITOWANY? (Tak / Nie)",
            placeholder="Wpisz: Tak lub Nie",
            default="Tak" if session.get("limit_licznik") else "Nie",
            required=False,
            max_length=10
        )

        self.add_item(self.swiatla)
        self.add_item(self.swiatla_limit)
        self.add_item(self.licznik)
        self.add_item(self.licznik_limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session["kolor_swiatel"] = str(self.swiatla.value).strip()
        self.session["limit_swiatla"] = str(self.swiatla_limit.value).strip().lower() in ("tak", "t", "yes", "true")
        self.session["kolor_licznika"] = str(self.licznik.value).strip()
        self.session["limit_licznik"] = str(self.licznik_limit.value).strip().lower() in ("tak", "t", "yes", "true")
        await self.on_update(interaction)


class MechTuningSelectorView(discord.ui.View):
    """Widok wyboru komponentów mechanicznych."""
    def __init__(self, studio: "StudioDashboardView") -> None:
        super().__init__(timeout=300)
        self.studio = studio
        self.session = studio.session

        # Select 1: ECU & Turbo
        ecu_options = [discord.SelectOption(label="ECU: Brak / Seryjne", value="ECU:BRAK")]
        for k, v in TUNING_MECHANICZNY["ECU"].items():
            ecu_options.append(discord.SelectOption(
                label=f"ECU {k} — {_price_str(v)}",
                value=f"ECU:{k}:{v}",
                default=(self.session["mechaniczny"].get("ECU") == (k, v))
            ))
        self.ecu_select = discord.ui.Select(placeholder="Wybierz ECU...", options=ecu_options[:25], row=0)
        self.ecu_select.callback = self._on_ecu_select
        self.add_item(self.ecu_select)

        # Select 2: Turbo & Zestaw
        turbo_options = [discord.SelectOption(label="Turbo: Brak / Seryjne", value="Turbo:BRAK")]
        for k, v in TUNING_MECHANICZNY["Turbo"].items():
            turbo_options.append(discord.SelectOption(
                label=f"Turbo {k} — {_price_str(v)}",
                value=f"Turbo:{k}:{v}",
                default=(self.session["mechaniczny"].get("Turbo") == (k, v))
            ))
        self.turbo_select = discord.ui.Select(placeholder="Wybierz Turbo...", options=turbo_options[:25], row=1)
        self.turbo_select.callback = self._on_turbo_select
        self.add_item(self.turbo_select)

        # Select 3: Zestaw, C.F.I., Napęd
        zestaw_options = [discord.SelectOption(label="Zestaw: Brak", value="Zestaw:BRAK")]
        for k, v in TUNING_MECHANICZNY["Zestaw"].items():
            zestaw_options.append(discord.SelectOption(
                label=f"Zestaw {k} — {_price_str(v)}",
                value=f"Zestaw:{k}:{v}",
                default=(self.session["mechaniczny"].get("Zestaw") == (k, v))
            ))
        self.zestaw_select = discord.ui.Select(placeholder="Wybierz Zestaw...", options=zestaw_options[:25], row=2)
        self.zestaw_select.callback = self._on_zestaw_select
        self.add_item(self.zestaw_select)

        # Select 4: C.F.I. Nitro & Paliwo
        cfi_options = [discord.SelectOption(label="C.F.I. (Nitro): Brak", value="C.F.I.:BRAK")]
        for k, v in TUNING_MECHANICZNY["C.F.I."].items():
            cfi_options.append(discord.SelectOption(
                label=f"C.F.I. {k} — {_price_str(v)}",
                value=f"C.F.I.:{k}:{v}",
                default=(self.session["mechaniczny"].get("C.F.I.") == (k, v))
            ))
        self.cfi_select = discord.ui.Select(placeholder="Wybierz C.F.I. (Nitro)...", options=cfi_options[:25], row=3)
        self.cfi_select.callback = self._on_cfi_select
        self.add_item(self.cfi_select)

    async def _on_ecu_select(self, interaction: discord.Interaction) -> None:
        val = self.ecu_select.values[0]
        if val == "ECU:BRAK":
            self.session["mechaniczny"].pop("ECU", None)
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["ECU"] = (k, int(v))
        await interaction.response.defer()

    async def _on_turbo_select(self, interaction: discord.Interaction) -> None:
        val = self.turbo_select.values[0]
        if val == "Turbo:BRAK":
            self.session["mechaniczny"].pop("Turbo", None)
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["Turbo"] = (k, int(v))
        await interaction.response.defer()

    async def _on_zestaw_select(self, interaction: discord.Interaction) -> None:
        val = self.zestaw_select.values[0]
        if val == "Zestaw:BRAK":
            self.session["mechaniczny"].pop("Zestaw", None)
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["Zestaw"] = (k, int(v))
        await interaction.response.defer()

    async def _on_cfi_select(self, interaction: discord.Interaction) -> None:
        val = self.cfi_select.values[0]
        if val == "C.F.I.:BRAK":
            self.session["mechaniczny"].pop("C.F.I.", None)
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["C.F.I."] = (k, int(v))
        await interaction.response.defer()

    @discord.ui.button(label="Paliwo, Napęd i Dodatki ➡", style=discord.ButtonStyle.primary, row=4)
    async def page_two(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = MechTuningPageTwoView(self.studio)
        await interaction.response.edit_message(
            embed=info_embed("⚙️ Tuning Mechaniczny (Krok 2/2)", "Wybierz Bak, LPG, Napęd oraz pozostałe komponenty:"),
            view=view
        )

    @discord.ui.button(label="Powrót do Studia ↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back_to_studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.studio.render_dashboard(interaction)


class MechTuningPageTwoView(discord.ui.View):
    """Druga strona tuningu mechanicznego (Bak, LPG, Napęd, Inne)."""
    def __init__(self, studio: "StudioDashboardView") -> None:
        super().__init__(timeout=300)
        self.studio = studio
        self.session = studio.session

        # Bak & LPG
        bak_options = [discord.SelectOption(label="Bak: Seryjny (50L/75L)", value="Bak:BRAK")]
        for k, v in TUNING_MECHANICZNY["Bak paliwa"].items():
            bak_options.append(discord.SelectOption(
                label=f"Bak {k} — {_price_str(v)}",
                value=f"Bak paliwa:{k}:{v}",
                default=(self.session["mechaniczny"].get("Bak paliwa") == (k, v))
            ))
        self.bak_select = discord.ui.Select(placeholder="Wybierz Bak paliwa...", options=bak_options, row=0)
        self.bak_select.callback = self._on_bak_select
        self.add_item(self.bak_select)

        # LPG
        lpg_options = [discord.SelectOption(label="LPG: Brak", value="LPG:BRAK")]
        for k, v in TUNING_MECHANICZNY["LPG"].items():
            lpg_options.append(discord.SelectOption(
                label=f"LPG {k} — {_price_str(v)}",
                value=f"LPG:{k}:{v}",
                default=(self.session["mechaniczny"].get("LPG") == (k, v))
            ))
        self.lpg_select = discord.ui.Select(placeholder="Wybierz instalację LPG...", options=lpg_options, row=1)
        self.lpg_select.callback = self._on_lpg_select
        self.add_item(self.lpg_select)

        # Napęd
        drive_options = [discord.SelectOption(label="Napęd: Seryjny", value="Napęd:BRAK")]
        for k, v in TUNING_MECHANICZNY["Zmiana napędu"].items():
            drive_options.append(discord.SelectOption(
                label=f"Napęd {k} — {_price_str(v)}",
                value=f"Zmiana napędu:{k}:{v}",
                default=(self.session["mechaniczny"].get("Zmiana napędu") == (k, v))
            ))
        self.drive_select = discord.ui.Select(placeholder="Wybierz Zmianę Napędu (AWD/RWD/FWD/MZN)...", options=drive_options, row=2)
        self.drive_select.callback = self._on_drive_select
        self.add_item(self.drive_select)

        # Inne komponenty (Multiselect!)
        inne_options = []
        for name, cost in TUNING_MECHANICZNY["Inne"].items():
            inne_options.append(discord.SelectOption(
                label=f"{name} — {_price_str(cost)}",
                value=f"{name}:{cost}",
                default=(name in self.session["mechaniczny"])
            ))
        self.inne_select = discord.ui.Select(
            placeholder="Wybierz pozostałe dodatki (można wybrać kilka)...",
            options=inne_options,
            min_values=0,
            max_values=len(inne_options),
            row=3
        )
        self.inne_select.callback = self._on_inne_select
        self.add_item(self.inne_select)

    async def _on_bak_select(self, interaction: discord.Interaction) -> None:
        val = self.bak_select.values[0]
        if val == "Bak:BRAK":
            self.session["mechaniczny"].pop("Bak paliwa", None)
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["Bak paliwa"] = (k, int(v))
        await interaction.response.defer()

    async def _on_lpg_select(self, interaction: discord.Interaction) -> None:
        val = self.lpg_select.values[0]
        if val == "LPG:BRAK":
            self.session["mechaniczny"].pop("LPG", None)
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["LPG"] = (k, int(v))
        await interaction.response.defer()

    async def _on_drive_select(self, interaction: discord.Interaction) -> None:
        val = self.drive_select.values[0]
        if val == "Napęd:BRAK":
            self.session["mechaniczny"].pop("Zmiana napędu", None)
            self.session["naped"] = "Seryjny"
        else:
            _, k, v = val.split(":")
            self.session["mechaniczny"]["Zmiana napędu"] = (k, int(v))
            self.session["naped"] = k
        await interaction.response.defer()

    async def _on_inne_select(self, interaction: discord.Interaction) -> None:
        # Usuń poprzednie z 'Inne'
        for k in list(TUNING_MECHANICZNY["Inne"].keys()):
            self.session["mechaniczny"].pop(k, None)
        # Dodaj nowo wybrane
        for item in self.inne_select.values:
            name, cost = item.split(":")
            self.session["mechaniczny"][name] = (name, int(cost))
        await interaction.response.defer()

    @discord.ui.button(label="⬅️ Poprzednia strona", style=discord.ButtonStyle.secondary, row=4)
    async def page_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = MechTuningSelectorView(self.studio)
        await interaction.response.edit_message(
            embed=info_embed("⚙️ Tuning Mechaniczny (Krok 1/2)", "Wybierz ECU, Turbo, Zestaw oraz C.F.I.:"),
            view=view
        )

    @discord.ui.button(label="Zapisz i Wróć do Studia ✅", style=discord.ButtonStyle.success, row=4)
    async def back_to_studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.studio.render_dashboard(interaction)


class VisualExtrasModal(discord.ui.Modal, title="🎨 Dodatki Wizualne Pojazdu"):
    def __init__(self, session: dict, on_update) -> None:
        super().__init__()
        self.session = session
        self.on_update = on_update
        extras = session.setdefault("extras", {})

        self.felgi = discord.ui.TextInput(
            label="Felgi (Nazwa / ID)",
            placeholder="np. Felgi Wires (1076), Shadow (1073)",
            default=extras.get("felgi", ""),
            required=False,
            max_length=40
        )
        self.poszerzenia = discord.ui.TextInput(
            label="Poszerzenia (Przód, Tył)",
            placeholder="np. 2, 2 lub 1, 2",
            default=extras.get("poszerzenia", ""),
            required=False,
            max_length=20
        )
        self.rozmiar = discord.ui.TextInput(
            label="Rozmiar felg",
            placeholder="np. Duże / Małe / Standardowe",
            default=extras.get("rozmiar", ""),
            required=False,
            max_length=20
        )
        self.szyby = discord.ui.TextInput(
            label="Przyciemnienie szyb",
            placeholder="np. 100%, 80%, 50%",
            default=extras.get("szyby", ""),
            required=False,
            max_length=20
        )

        self.add_item(self.felgi)
        self.add_item(self.poszerzenia)
        self.add_item(self.rozmiar)
        self.add_item(self.szyby)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session["extras"]["felgi"] = str(self.felgi.value).strip() if self.felgi.value else ""
        self.session["extras"]["poszerzenia"] = str(self.poszerzenia.value).strip() if self.poszerzenia.value else ""
        self.session["extras"]["rozmiar"] = str(self.rozmiar.value).strip() if self.rozmiar.value else ""
        self.session["extras"]["szyby"] = str(self.szyby.value).strip() if self.szyby.value else ""
        await self.on_update(interaction)


class VisualTuningView(discord.ui.View):
    """Widok wyboru tuningu wizualnego (zderzaki, spoilery, progi, wydechy itp.)."""
    def __init__(self, studio: "StudioDashboardView") -> None:
        super().__init__(timeout=300)
        self.studio = studio
        self.session = studio.session

        categories = list(TUNING_WIZUALNY.keys())
        options = [discord.SelectOption(label=cat, value=cat) for cat in categories[:25]]
        self.cat_select = discord.ui.Select(placeholder="Wybierz kategorię tuningu wizualnego...", options=options, row=0)
        self.cat_select.callback = self._on_cat_select
        self.add_item(self.cat_select)

    async def _on_cat_select(self, interaction: discord.Interaction) -> None:
        cat = self.cat_select.values[0]
        view = VisualCategoryPartsView(self.studio, cat)
        await interaction.response.edit_message(
            embed=info_embed(
                f"🎨 Tuning Wizualny: {cat}",
                "Wybierz część z listy poniżej, aby zamontować ją na pojeździe:"
            ),
            view=view
        )

    @discord.ui.button(label="Dodatki (Felgi, Szyby, Poszerzenia) 🛞", style=discord.ButtonStyle.primary, row=1)
    async def open_extras(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_update(inter: discord.Interaction) -> None:
            await inter.response.edit_message(
                embed=info_embed("🎨 Tuning Wizualny", "Zaktualizowano dodatki wizualne. Wybierz kategorię lub wróć do Studia:"),
                view=self
            )
        await interaction.response.send_modal(VisualExtrasModal(self.session, on_update))

    @discord.ui.button(label="Powrót do Studia ↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back_to_studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.studio.render_dashboard(interaction)


class VisualCategoryPartsView(discord.ui.View):
    """Wybór konkretnej części z danej kategorii wizualnej."""
    def __init__(self, studio: "StudioDashboardView", category: str) -> None:
        super().__init__(timeout=300)
        self.studio = studio
        self.session = studio.session
        self.category = category

        parts = TUNING_WIZUALNY.get(category, [])
        options = [discord.SelectOption(label="— Brak / Zdemontuj —", value="BRAK")]
        for pid, cost in parts[:24]:
            options.append(discord.SelectOption(
                label=f"ID {pid} — {_price_str(cost)}",
                value=f"{pid}:{cost}",
                default=(self.session["wizualny"].get(category) == (f"ID {pid}", cost))
            ))

        self.part_select = discord.ui.Select(placeholder=f"Wybierz {category}...", options=options, row=0)
        self.part_select.callback = self._on_part_select
        self.add_item(self.part_select)

    async def _on_part_select(self, interaction: discord.Interaction) -> None:
        val = self.part_select.values[0]
        if val == "BRAK":
            self.session["wizualny"].pop(self.category, None)
        else:
            pid, cost = val.split(":")
            self.session["wizualny"][self.category] = (f"ID {pid}", int(cost))

        # Powrót do menu kategorii wizualnych
        view = VisualTuningView(self.studio)
        await interaction.response.edit_message(
            embed=info_embed("🎨 Tuning Wizualny", f"Zapisano wybór dla **{self.category}**. Możesz wybrać kolejną kategorię:"),
            view=view
        )

    @discord.ui.button(label="Wróć do kategorii ↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = VisualTuningView(self.studio)
        await interaction.response.edit_message(
            embed=info_embed("🎨 Tuning Wizualny", "Wybierz kategorię części:"),
            view=view
        )


class StudioDashboardView(discord.ui.View):
    """
    Główny Pulpit Studia Ogłoszeń.
    Pozwala edytować wszystkie sekcje, nakładać pakiety tuningu (FT/PFT/Stock),
    sprawdzać kalkulację wartości oraz publikować ogłoszenie.
    """

    def __init__(self, cog: "MarketplaceCog", user: discord.abc.User, typ: str) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.user = user

        # Stan roboczy ogłoszenia dla użytkownika
        self.session: dict[str, Any] = {
            "typ": typ,
            "model": "",
            "vuid": "",
            "silnik": "",
            "cena": "",
            "przebieg": "",
            "naped": "Seryjny",
            "negocjacja": "Do negocjacji",
            "mechaniczny": {},
            "wizualny": {},
            "extras": {},
            "kolor_swiatel": "",
            "kolor_licznika": "",
            "limit_swiatla": False,
            "limit_licznik": False,
            "contact_nick": "",
            "contact_phone": "",
            "image_url": "",
            "notes": "",
        }

    def compute_tuning_cost(self) -> int:
        """Oblicza łączną wartość włożonego tuningu w PLN."""
        total = 0
        for _, cost in self.session["mechaniczny"].values():
            total += cost
        for _, cost in self.session["wizualny"].values():
            total += cost
        return total

    def build_preview_embed(self) -> discord.Embed:
        """Buduje elegancki embed podglądu na żywo w trakcie projektowania."""
        s = self.session
        theme = THEMES.get(s["typ"], THEMES["SPRZEDAM"])
        tuning_cost = self.compute_tuning_cost()

        model_display = s["model"] if s["model"] else "*(Wpisz model pojazdu)*"
        vuid_display = f"#{s['vuid']}" if s["vuid"] else "*(brak VUID)*"
        cena_display = _price_str(s["cena"]) if s["cena"] else "*(Wpisz cenę)*"

        # Tuning mechaniczny formatowanie
        if s["mechaniczny"]:
            mech_lines = [f"• **{k}**: {wariant}" for k, (wariant, _) in s["mechaniczny"].items()]
            mech_text = "\n".join(mech_lines)
        else:
            mech_text = "*Brak tuningu mechanicznego (seria)*"

        # Tuning wizualny formatowanie
        vis_parts = []
        for k, (wariant, _) in s["wizualny"].items():
            vis_parts.append(f"{k} ({wariant})")
        extras = s.get("extras", {})
        if extras.get("felgi"):
            vis_parts.append(f"Felgi: {extras['felgi']}")
        if extras.get("poszerzenia"):
            vis_parts.append(f"Poszerzenia: {extras['poszerzenia']}")
        if extras.get("rozmiar"):
            vis_parts.append(f"Rozmiar felg: {extras['rozmiar']}")
        if extras.get("szyby"):
            vis_parts.append(f"Szyby: {extras['szyby']}")
        vis_text = ", ".join(vis_parts) if vis_parts else "*Brak tuningu wizualnego*"

        # Lampy i licznik
        swiatla_val = s["kolor_swiatel"] or "Seryjne"
        if s["kolor_swiatel"] and s["limit_swiatla"]:
            swiatla_val += " ✨ *(LIMITOWANE)*"
        licznik_val = s["kolor_licznika"] or "Seryjny"
        if s["kolor_licznika"] and s["limit_licznik"]:
            licznik_val += " ✨ *(LIMITOWANE)*"

        fields = [
            {"name": "🏷️ VUID", "value": f"`{vuid_display}`", "inline": True},
            {"name": "🏎️ Model", "value": f"**{model_display}**", "inline": True},
            {"name": "⚡ Silnik", "value": f"`{s['silnik'] or 'Nie podano'}`", "inline": True},
            {"name": "💵 Cena wywoławcza", "value": f"**{cena_display}**\n*{s['negocjacja']}*", "inline": True},
            {"name": "🛣️ Przebieg", "value": f"`{s['przebieg'] or 'Nie podano'}`", "inline": True},
            {"name": "🎮 Napęd", "value": f"`{s['naped']}`", "inline": True},
            {"name": "⚙️ Tuning Mechaniczny", "value": mech_text, "inline": False},
            {"name": "🎨 Tuning Wizualny & Dodatki", "value": vis_text, "inline": False},
            {"name": "💡 Światła", "value": swiatla_val, "inline": True},
            {"name": "💡 Licznik", "value": licznik_val, "inline": True},
            {"name": "💎 Wartość włożonego tuningu", "value": f"💰 **{_price_str(tuning_cost)}**", "inline": True},
            {
                "name": "👤 Sprzedający",
                "value": f"Nick MTA: `{s['contact_nick'] or 'Nie podano'}`",
                "inline": False
            }
        ]

        if s["notes"]:
            fields.append({"name": "📝 Dodatkowy opis", "value": f"> {s['notes']}", "inline": False})

        embed = create_embed(
            title=f"🛠️ Studio Ogłoszeń — {theme['title']} {model_display}",
            description="Wypełnij dane pojazdu za pomocą przycisków poniżej. Podgląd aktualizuje się natychmiast!",
            color=theme["color"],
            fields=fields
        )
        if s["image_url"]:
            embed.set_image(url=s["image_url"])
        embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text=f"Kreator Ogłoszeń Team77 • Wartość tuningu: {_price_str(tuning_cost)}")
        return embed

    async def render_dashboard(self, interaction: discord.Interaction) -> None:
        """Ponownie renderuje embed oraz widok w wiadomości."""
        embed = self.build_preview_embed()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    # --- Przyciski Rzędu 0: Dane podstawowe, Tuning Mech, Tuning Wiz, Lampy ---

    @discord.ui.button(label="Dane Pojazdu", emoji="🚗", style=discord.ButtonStyle.primary, row=0)
    async def btn_data(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(VehicleDataModal(self.session, self.render_dashboard))

    @discord.ui.button(label="Tuning Mechaniczny", emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def btn_mech(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = MechTuningSelectorView(self)
        await interaction.response.edit_message(
            embed=info_embed("⚙️ Tuning Mechaniczny (Krok 1/2)", "Wybierz ECU, Turbo, Zestaw oraz C.F.I.:"),
            view=view
        )

    @discord.ui.button(label="Tuning Wizualny", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def btn_vis(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = VisualTuningView(self)
        await interaction.response.edit_message(
            embed=info_embed("🎨 Tuning Wizualny", "Wybierz kategorię części wizualnych lub dodatki:"),
            view=view
        )

    @discord.ui.button(label="Światła & Licznik", emoji="💡", style=discord.ButtonStyle.primary, row=0)
    async def btn_lights(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(LightingModal(self.session, self.render_dashboard))

    # --- Przyciski Rzędu 1: Zdjęcie/Kontakt, Notatka, Negocjacja, Szybki Pakiet ---

    @discord.ui.button(label="Zdjęcie & Nick MTA", emoji="📸", style=discord.ButtonStyle.secondary, row=1)
    async def btn_contact(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ContactImageModal(self.session, self.render_dashboard))

    @discord.ui.button(label="Dodatkowy Opis", emoji="📝", style=discord.ButtonStyle.secondary, row=1)
    async def btn_notes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(NotesModal(self.session, self.render_dashboard))

    @discord.ui.button(label="Negocjacja", emoji="🏷️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_nego(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cycle = ["Do negocjacji", "Do małej negocjacji", "Cena ostateczna"]
        curr = self.session["negocjacja"]
        next_idx = (cycle.index(curr) + 1) % len(cycle) if curr in cycle else 0
        self.session["negocjacja"] = cycle[next_idx]
        await self.render_dashboard(interaction)

    @discord.ui.button(label="⚡ Szybki Pakiet (FT/PFT)", style=discord.ButtonStyle.secondary, row=1)
    async def btn_preset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = PresetChoiceView(self)
        await interaction.response.edit_message(
            embed=info_embed(
                "⚡ Szybkie Szablony Tuningu",
                "Wybierz gotowy pakiet modyfikacji, aby uzupełnić tuning jednym kliknięciem:\n\n"
                "• **🌟 Full Tune (FT)**: ECU V4, Turbo V5, Torowy, C.F.I. V5, Bak 150L, LPG 75L, AWD, ABS, Gwint, Hydraulika, Akcesoria (~754k PLN)\n"
                "• **🏎️ Prawie Full Tune (PFT)**: ECU V4, Turbo V5, Torowy, Bak 150L, LPG 75L, AWD, ABS, Gwint (~547k PLN)\n"
                "• **🧼 Stock (Czysty)**: Zeruje cały tuning do stanu fabrycznego"
            ),
            view=view
        )

    # --- Przyciski Rzędu 2: Publikuj, Anuluj ---

    @discord.ui.button(label="Opublikuj Ogłoszenie 🚀", style=discord.ButtonStyle.success, row=2)
    async def btn_publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        s = self.session
        # Walidacja wymaganych pól
        if not s["model"]:
            await interaction.response.send_message(
                embed=error_embed("Brakujące dane", "Wprowadź **Model pojazdu** przed publikacją!"),
                ephemeral=True
            )
            return
        if not s["vuid"]:
            await interaction.response.send_message(
                embed=error_embed("Brakujące dane", "Wprowadź **VUID** pojazdu przed publikacją!"),
                ephemeral=True
            )
            return
        if not s["cena"]:
            await interaction.response.send_message(
                embed=error_embed("Brakujące dane", "Wprowadź **Cenę** pojazdu przed publikacją!"),
                ephemeral=True
            )
            return

        # Ustal kanał docelowy
        target_channel = interaction.channel
        guild = interaction.guild

        # Sprawdź kanał w konfiguracji bazy
        if guild:
            row = await self.cog.bot.db.fetchone(
                "SELECT marketplace_channel_id FROM guild_config WHERE guild_id = ?",
                (guild.id,)
            )
            if row and row["marketplace_channel_id"]:
                found = guild.get_channel(row["marketplace_channel_id"])
                if found and isinstance(found, discord.TextChannel):
                    target_channel = found

        # Fallback do .env
        env_channel_id = int(os.getenv("MARKETPLACE_CHANNEL_ID", "0") or "0")
        if env_channel_id and guild and target_channel == interaction.channel:
            found = guild.get_channel(env_channel_id)
            if found and isinstance(found, discord.TextChannel):
                target_channel = found

        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Błąd kanału", "Nie udało się określić docelowego kanału tekstowego dla ogłoszeń."),
                ephemeral=True
            )
            return

        # Stwórz finalny embed ogłoszenia
        publish_embed = self.cog.build_final_listing_embed(self.user, s)
        active_view = ListingActiveView(self.cog)

        try:
            sent_msg = await target_channel.send(embed=publish_embed, view=active_view)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Brak uprawnień", f"Bot nie ma uprawnień do wysyłania wiadomości na kanale {target_channel.mention}."),
                ephemeral=True
            )
            return

        # Automatyczne utworzenie wątku dyskusyjnego
        thread_id = None
        auto_thread_enabled = True
        if guild:
            cfg = await self.cog.bot.db.fetchone(
                "SELECT marketplace_auto_thread FROM guild_config WHERE guild_id = ?",
                (guild.id,)
            )
            if cfg and cfg["marketplace_auto_thread"] == 0:
                auto_thread_enabled = False

        if auto_thread_enabled:
            try:
                thread = await sent_msg.create_thread(
                    name=f"💬 Oferty — {s['model']} #{s['vuid']}",
                    auto_archive_duration=1440
                )
                thread_id = thread.id
                welcome_msg = create_embed(
                    title=f"💬 Dyskusja: {s['model']} (VUID: #{s['vuid']})",
                    description=(
                        f"Witaj w wątku ogłoszenia pojazdu **{s['model']}**!\n"
                        f"Tutaj możesz negocjować cenę, zadawać pytania lub składać publiczne oferty kupna.\n"
                        f"Oferty prywatne możesz złożyć za pomocą przycisku **Złóż ofertę** pod ogłoszeniem."
                    ),
                    color=config.INFO_COLOR
                )
                await thread.send(embed=welcome_msg)
            except Exception as e:
                logger.warning(f"Nie udało się utworzyć wątku dyskusyjnego dla ogłoszenia: {e}")

        # Zapisz ogłoszenie w bazie danych
        tuning_cost = self.compute_tuning_cost()
        cursor = await self.cog.bot.db.execute(
            """
            INSERT INTO marketplace_listings (
                guild_id, channel_id, message_id, thread_id, seller_id, listing_type,
                model, vuid, engine, price, mileage, drive, negotiable,
                tuning_mech, tuning_vis, extras, lights_color, speedo_color,
                is_limited_lights, is_limited_speedo, tuning_cost, image_url,
                contact_nick, contact_phone, notes, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                guild.id if guild else 0,
                target_channel.id,
                sent_msg.id,
                thread_id,
                self.user.id,
                s["typ"],
                s["model"],
                s["vuid"],
                s["silnik"] or "Nie podano",
                s["cena"],
                s["przebieg"],
                s["naped"],
                s["negocjacja"],
                json.dumps(s["mechaniczny"]),
                json.dumps(s["wizualny"]),
                json.dumps(s["extras"]),
                s["kolor_swiatel"],
                s["kolor_licznika"],
                1 if s["limit_swiatla"] else 0,
                1 if s["limit_licznik"] else 0,
                tuning_cost,
                s["image_url"],
                s["contact_nick"],
                s["contact_phone"],
                s["notes"]
            )
        )

        self.stop()
        jump_url = sent_msg.jump_url
        await interaction.response.edit_message(
            embed=success_embed(
                "Ogłoszenie Opublikowane! 🎉",
                f"Twoje ogłoszenie pojazdu **{s['model']}** (#{s['vuid']}) zostało pomyślnie opublikowane na kanale {target_channel.mention}!\n\n"
                f"🔗 [Kliknij tutaj, aby przejść do ogłoszenia]({jump_url})"
            ),
            view=None
        )

    @discord.ui.button(label="Porzuć", style=discord.ButtonStyle.danger, row=2)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=error_embed("Anulowano", "Tworzenie ogłoszenia zostało przerwane."),
            view=None
        )


class PresetChoiceView(discord.ui.View):
    """Widok wyboru gotowego szablonu tuningu."""
    def __init__(self, studio: StudioDashboardView) -> None:
        super().__init__(timeout=180)
        self.studio = studio

    @discord.ui.button(label="🌟 Full Tune (FT)", style=discord.ButtonStyle.success)
    async def apply_ft(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.studio.session["mechaniczny"] = {
            "ECU": ("V4", 40000),
            "Turbo": ("V5", 60000),
            "Zestaw": ("Torowy", 250000),
            "C.F.I.": ("V5", 250000),
            "Bak paliwa": ("150L", 65000),
            "LPG": ("75L", 35000),
            "Zmiana napędu": ("AWD", 50000),
            "System ABS": ("System ABS", 15000),
            "Gwintowane zawieszenie": ("Gwintowane zawieszenie", 32500),
            "Hydraulika": ("Hydraulika", 45000),
            "Wykrywacz fotoradarów": ("Wykrywacz fotoradarów", 2500),
            "CB-radio": ("CB-radio", 1500),
            "Ogranicznik prędkości": ("Ogranicznik prędkości", 2000),
            "Radio": ("Radio", 500),
        }
        self.studio.session["naped"] = "AWD"
        await self.studio.render_dashboard(interaction)

    @discord.ui.button(label="🏎️ Prawie FT (PFT)", style=discord.ButtonStyle.primary)
    async def apply_pft(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.studio.session["mechaniczny"] = {
            "ECU": ("V4", 40000),
            "Turbo": ("V5", 60000),
            "Zestaw": ("Torowy", 250000),
            "Bak paliwa": ("150L", 65000),
            "LPG": ("75L", 35000),
            "Zmiana napędu": ("AWD", 50000),
            "System ABS": ("System ABS", 15000),
            "Gwintowane zawieszenie": ("Gwintowane zawieszenie", 32500),
        }
        self.studio.session["naped"] = "AWD"
        await self.studio.render_dashboard(interaction)

    @discord.ui.button(label="🧼 Stock (Czysty)", style=discord.ButtonStyle.danger)
    async def apply_stock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.studio.session["mechaniczny"] = {}
        self.studio.session["wizualny"] = {}
        self.studio.session["extras"] = {}
        self.studio.session["naped"] = "Seryjny"
        await self.studio.render_dashboard(interaction)

    @discord.ui.button(label="Wróć do Studia ↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.studio.render_dashboard(interaction)


# ---------------------------------------------------------------------------
# Główna klasa Coga
# ---------------------------------------------------------------------------

class MarketplaceCog(commands.Cog):
    """Zaawansowany Rynek Motoryzacyjny ProjectRPG (/ogloszenie, /ogloszenia, /moje_ogloszenia)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Rejestracja trwałego widoku ListingActiveView przy starcie bota oraz upewnienie się, że tabele istnieją."""
        try:
            await self.bot.db.execute(
                """
                CREATE TABLE IF NOT EXISTS marketplace_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    seller_id INTEGER NOT NULL,
                    listing_type TEXT NOT NULL,
                    model TEXT NOT NULL,
                    vuid TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    price TEXT NOT NULL,
                    mileage TEXT,
                    drive TEXT,
                    negotiable TEXT DEFAULT 'Do negocjacji',
                    tuning_mech TEXT DEFAULT '{}',
                    tuning_vis TEXT DEFAULT '{}',
                    extras TEXT DEFAULT '{}',
                    lights_color TEXT,
                    speedo_color TEXT,
                    is_limited_lights INTEGER DEFAULT 0,
                    is_limited_speedo INTEGER DEFAULT 0,
                    tuning_cost INTEGER DEFAULT 0,
                    image_url TEXT,
                    contact_nick TEXT,
                    contact_phone TEXT,
                    notes TEXT,
                    status TEXT DEFAULT 'ACTIVE',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bumped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await self.bot.db.execute(
                """
                CREATE TABLE IF NOT EXISTS marketplace_offers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id INTEGER NOT NULL,
                    buyer_id INTEGER NOT NULL,
                    buyer_nick TEXT NOT NULL,
                    offer_amount TEXT NOT NULL,
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (listing_id) REFERENCES marketplace_listings (id) ON DELETE CASCADE
                );
                """
            )
        except Exception as e:
            logger.warning(f"Błąd podczas weryfikacji tabel marketplace: {e}")

        self.bot.add_view(ListingActiveView(self))
        logger.info("Zarejestrowano trwały widok ListingActiveView dla modułu Marketplace.")

    async def get_listing_by_message(self, message_id: int) -> dict | None:
        """Pobiera ogłoszenie z bazy po ID wiadomości."""
        row = await self.bot.db.fetchone(
            "SELECT * FROM marketplace_listings WHERE message_id = ?",
            (message_id,)
        )
        return dict(row) if row else None

    async def get_listing_by_id(self, listing_id: int) -> dict | None:
        """Pobiera ogłoszenie z bazy po jego unikalnym ID."""
        row = await self.bot.db.fetchone(
            "SELECT * FROM marketplace_listings WHERE id = ?",
            (listing_id,)
        )
        return dict(row) if row else None

    async def update_listing_status(self, listing_id: int, status: str) -> None:
        """Aktualizuje status ogłoszenia (ACTIVE, RESERVED, SOLD, CLOSED)."""
        await self.bot.db.execute(
            "UPDATE marketplace_listings SET status = ? WHERE id = ?",
            (status, listing_id)
        )

    async def bump_listing(self, listing: dict) -> None:
        """Odświeża timestamp podbicia i wysyła powiadomienie."""
        await self.bot.db.execute(
            "UPDATE marketplace_listings SET bumped_at = CURRENT_TIMESTAMP WHERE id = ?",
            (listing["id"],)
        )
        guild = self.bot.get_guild(listing["guild_id"])
        if not guild:
            return

        # Jeśli jest wątek, wyślij komunikat w wątku
        if listing.get("thread_id"):
            thread = guild.get_thread(listing["thread_id"])
            if thread:
                try:
                    await thread.send(
                        f"📢 **PODBICIE OGŁOSZENIA!** Pojazd **{listing['model']}** (VUID `#{listing['vuid']}`) nadal szuka nowego właściciela! Aktualna cena: **{_price_str(listing['price'])}**."
                    )
                except Exception:
                    pass

    async def delete_listing(self, listing_id: int) -> None:
        """Usuwa ogłoszenie z rynku i kasuje powiązaną wiadomość."""
        listing = await self.get_listing_by_id(listing_id)
        if not listing:
            return

        guild = self.bot.get_guild(listing["guild_id"])
        if guild:
            channel = guild.get_channel(listing["channel_id"])
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(listing["message_id"])
                    await msg.delete()
                except Exception:
                    pass

        await self.bot.db.execute(
            "DELETE FROM marketplace_listings WHERE id = ?",
            (listing_id,)
        )

    async def refresh_listing_message(self, listing_id: int) -> None:
        """Aktualizuje wiadomość na Discordzie po zmianie statusu ogłoszenia."""
        listing = await self.get_listing_by_id(listing_id)
        if not listing:
            return

        guild = self.bot.get_guild(listing["guild_id"])
        if not guild:
            return
        channel = guild.get_channel(listing["channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        try:
            msg = await channel.fetch_message(listing["message_id"])
        except Exception:
            return

        seller = self.bot.get_user(listing["seller_id"]) or await self.bot.fetch_user(listing["seller_id"])
        embed = self.build_final_listing_embed(seller, listing)
        await msg.edit(embed=embed)

    def build_final_listing_embed(self, author: discord.abc.User, data: dict) -> discord.Embed:
        """Buduje najwyższej klasy embed opublikowanego ogłoszenia."""
        status = data.get("status", "ACTIVE")
        listing_type = data.get("listing_type") or data.get("typ", "SPRZEDAM")
        theme = THEMES.get(status if status in ("SOLD", "RESERVED") else listing_type, THEMES["SPRZEDAM"])

        model = data["model"]
        vuid = data["vuid"]
        engine = data.get("engine") or data.get("silnik", "Nie podano")
        raw_price = data.get("price") or data.get("cena", "0")
        price_formatted = _price_str(raw_price)
        mileage = data.get("mileage") or data.get("przebieg") or "Nie podano"
        drive = data.get("drive") or data.get("naped") or "Seryjny"
        nego = data.get("negotiable") or data.get("negocjacja") or "Do negocjacji"

        # Tuning mechaniczny
        tuning_mech_data = data.get("tuning_mech") or data.get("mechaniczny", {})
        if isinstance(tuning_mech_data, str):
            try:
                tuning_mech_data = json.loads(tuning_mech_data)
            except Exception:
                tuning_mech_data = {}

        if tuning_mech_data:
            mech_lines = [f"• **{k}**: {v[0] if isinstance(v, (list, tuple)) else v}" for k, v in tuning_mech_data.items()]
            mech_str = "\n".join(mech_lines)
        else:
            mech_str = "*Brak tuningu mechanicznego (seria)*"

        # Tuning wizualny
        tuning_vis_data = data.get("tuning_vis") or data.get("wizualny", {})
        if isinstance(tuning_vis_data, str):
            try:
                tuning_vis_data = json.loads(tuning_vis_data)
            except Exception:
                tuning_vis_data = {}

        vis_items = []
        for k, v in tuning_vis_data.items():
            val = v[0] if isinstance(v, (list, tuple)) else v
            vis_items.append(f"{k} ({val})")

        extras = data.get("extras", {})
        if isinstance(extras, str):
            try:
                extras = json.loads(extras)
            except Exception:
                extras = {}
        if extras.get("felgi"):
            vis_items.append(f"Felgi: {extras['felgi']}")
        if extras.get("poszerzenia"):
            vis_items.append(f"Poszerzenia: {extras['poszerzenia']}")
        if extras.get("rozmiar"):
            vis_items.append(f"Rozmiar felg: {extras['rozmiar']}")
        if extras.get("szyby"):
            vis_items.append(f"Przyciemnienie: {extras['szyby']}")

        vis_str = ", ".join(vis_items) if vis_items else "*Brak tuningu wizualnego*"

        # Lampy i licznik
        lights_color = data.get("lights_color") or data.get("kolor_swiatel") or "Seryjne"
        is_lim_lights = data.get("is_limited_lights") or data.get("limit_swiatla")
        if lights_color != "Seryjne" and is_lim_lights:
            lights_color += " ✨ **(LIMITOWANE!)**"

        speedo_color = data.get("speedo_color") or data.get("kolor_licznika") or "Seryjny"
        is_lim_speedo = data.get("is_limited_speedo") or data.get("limit_licznik")
        if speedo_color != "Seryjny" and is_lim_speedo:
            speedo_color += " ✨ **(LIMITOWANE!)**"

        # Koszt tuningu
        tuning_cost = data.get("tuning_cost", 0)
        if not tuning_cost:
            # Policz jeśli nie było w data
            cost = 0
            for _, c in tuning_mech_data.values():
                cost += c if isinstance(c, int) else 0
            for _, c in tuning_vis_data.values():
                cost += c if isinstance(c, int) else 0
            tuning_cost = cost

        nick = data.get("contact_nick") or "Nie podano"
        notes = data.get("notes")

        # Tytuł
        badge = theme["badge"]
        title_prefix = f"[{badge}] " if status in ("SOLD", "RESERVED") else ""
        title = f"{title_prefix}{theme['title']} — {model} #{vuid}"

        seller_val = f"{author.mention} • Nick MTA: `{nick}`" if nick != "Nie podano" else f"{author.mention}"

        fields = [
            {"name": "🏷️ VUID", "value": f"`#{vuid}`", "inline": True},
            {"name": "🏎️ Model", "value": f"**{model}**", "inline": True},
            {"name": "⚡ Silnik", "value": f"`{engine}`", "inline": True},
            {"name": "💵 Cena", "value": f"**{price_formatted}**\n*{nego}*", "inline": True},
            {"name": "🛣️ Przebieg", "value": f"`{mileage}`", "inline": True},
            {"name": "🎮 Napęd", "value": f"`{drive}`", "inline": True},
            {"name": "⚙️ Tuning Mechaniczny", "value": mech_str, "inline": False},
            {"name": "🎨 Tuning Wizualny & Dodatki", "value": vis_str, "inline": False},
            {"name": "💡 Kolor Świateł", "value": lights_color, "inline": True},
            {"name": "💡 Kolor Licznika", "value": speedo_color, "inline": True},
            {"name": "💎 Wartość włożonego tuningu", "value": f"💰 **{_price_str(tuning_cost)}**", "inline": True},
            {
                "name": "👤 Sprzedający",
                "value": seller_val,
                "inline": False
            }
        ]

        if notes:
            fields.append({"name": "📝 Informacje od sprzedającego", "value": f"> {notes}", "inline": False})

        embed = create_embed(
            title=title,
            color=theme["color"],
            fields=fields
        )

        image_url = data.get("image_url")
        if image_url:
            embed.set_image(url=image_url)

        if hasattr(author, "display_avatar"):
            embed.set_thumbnail(url=author.display_avatar.url)

        embed.set_footer(text=f"Rynek Pojazdów • Team77 • Status: {theme['badge']}")
        return embed

    # -----------------------------------------------------------------------
    # Komendy Slash
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="ogloszenie",
        description="Otwórz interaktywne Studio Ogłoszeń i wystaw pojazd na rynku organizacji."
    )
    @app_commands.describe(typ="Rodzaj ogłoszenia (Sprzedam / Kupię / Zamienię / Wynajmę)")
    @app_commands.choices(typ=[
        app_commands.Choice(name="💰 SPRZEDAM", value="SPRZEDAM"),
        app_commands.Choice(name="🛒 KUPIĘ", value="KUPIE"),
        app_commands.Choice(name="🔄 ZAMIENIĘ", value="ZAMIENIE"),
        app_commands.Choice(name="🔑 WYNAJMĘ", value="WYNAJME"),
    ])
    async def ogloszenie(self, interaction: discord.Interaction, typ: Optional[app_commands.Choice[str]] = None) -> None:
        """Uruchamia nowoczesne Studio Ogłoszeń."""
        chosen_type = typ.value if typ else "SPRZEDAM"
        studio = StudioDashboardView(self, interaction.user, chosen_type)
        embed = studio.build_preview_embed()
        await interaction.response.send_message(embed=embed, view=studio, ephemeral=True)

    @app_commands.command(
        name="moje_ogloszenia",
        description="Wyświetl i zarządzaj swoimi aktywnymi ogłoszeniami pojazdów na rynku."
    )
    async def moje_ogloszenia(self, interaction: discord.Interaction) -> None:
        """Pokazuje listę własnych ogłoszeń z opcją szybkiego podbicia lub zakończenia."""
        listings = await self.bot.db.fetchall(
            """
            SELECT * FROM marketplace_listings
            WHERE seller_id = ? AND status != 'DELETED'
            ORDER BY created_at DESC LIMIT 10
            """,
            (interaction.user.id,)
        )

        if not listings:
            await interaction.response.send_message(
                embed=info_embed(
                    "Brak ogłoszeń",
                    "Nie posiadasz obecnie żadnych wystawionych ogłoszeń. Użyj `/ogloszenie`, aby wystawić pojazd!"
                ),
                ephemeral=True
            )
            return

        desc_lines = []
        for l in listings:
            status_badge = THEMES.get(l["status"], {}).get("badge", l["status"])
            jump_url = f"https://discord.com/channels/{l['guild_id']}/{l['channel_id']}/{l['message_id']}"
            desc_lines.append(
                f"• **{l['model']}** (VUID: `#{l['vuid']}`) — **{_price_str(l['price'])}**\n"
                f"  Status: {status_badge} | [Przejdź do ogłoszenia]({jump_url})"
            )

        embed = create_embed(
            title="🚗 Twoje Ogłoszenia na Rynku",
            description="\n\n".join(desc_lines),
            color=config.INFO_COLOR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="ogloszenia",
        description="Przeglądaj aktualne oferty rynkowe pojazdów z opcją wyszukiwania."
    )
    @app_commands.describe(
        szukaj_model="Filtruj po nazwie modelu (np. Sultan, Cheetah, Infernus)",
        max_cena="Maksymalna cena w PLN (np. 500000 lub 1.5kk)",
        typ="Filtruj rodzaj ogłoszenia"
    )
    @app_commands.choices(typ=[
        app_commands.Choice(name="Wszystkie", value="ALL"),
        app_commands.Choice(name="💰 SPRZEDAM", value="SPRZEDAM"),
        app_commands.Choice(name="🛒 KUPIĘ", value="KUPIE"),
        app_commands.Choice(name="🔄 ZAMIENIĘ", value="ZAMIENIE"),
        app_commands.Choice(name="🔑 WYNAJMĘ", value="WYNAJME"),
    ])
    async def ogloszenia(
        self,
        interaction: discord.Interaction,
        szukaj_model: Optional[str] = None,
        max_cena: Optional[str] = None,
        typ: Optional[app_commands.Choice[str]] = None
    ) -> None:
        """Wyszukiwarka ofert rynkowych."""
        query = "SELECT * FROM marketplace_listings WHERE status = 'ACTIVE'"
        params: list[Any] = []

        if szukaj_model:
            query += " AND LOWER(model) LIKE ?"
            params.append(f"%{szukaj_model.strip().lower()}%")

        if typ and typ.value != "ALL":
            query += " AND listing_type = ?"
            params.append(typ.value)

        query += " ORDER BY bumped_at DESC LIMIT 15"
        rows = await self.bot.db.fetchall(query, tuple(params))

        # Filtruj cenę jeśli podano
        max_cena_int = None
        if max_cena:
            try:
                max_cena_int = int(_clean_price_input(max_cena))
            except ValueError:
                pass

        results = []
        for r in rows:
            if max_cena_int is not None:
                try:
                    price_val = int(re.sub(r"[^\d]", "", str(r["price"])))
                    if price_val > max_cena_int:
                        continue
                except Exception:
                    pass
            results.append(r)

        if not results:
            await interaction.response.send_message(
                embed=info_embed(
                    "Brak wyników",
                    "Nie znaleziono aktywnych ogłoszeń spełniających podane kryteria."
                ),
                ephemeral=True
            )
            return

        desc_lines = []
        for l in results:
            type_badge = THEMES.get(l["listing_type"], {}).get("title", l["listing_type"])
            tuning_txt = f"💎 Tuning: {_price_str(l['tuning_cost'])}" if l["tuning_cost"] else "Stock"
            jump_url = f"https://discord.com/channels/{l['guild_id']}/{l['channel_id']}/{l['message_id']}"
            desc_lines.append(
                f"**{type_badge} — {l['model']}** (VUID: `#{l['vuid']}`)\n"
                f"💵 **{_price_str(l['price'])}** ({l['negotiable']}) | {tuning_txt}\n"
                f"👤 Sprzedający: <@{l['seller_id']}> • [Zobacz ogłoszenie 🔗]({jump_url})"
            )

        embed = create_embed(
            title=f"🛒 Rynek Pojazdów ({len(results)} ofert)",
            description="\n\n".join(desc_lines),
            color=config.INFO_COLOR
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="rynek_ustaw",
        description="[Admin] Skonfiguruj kanał publikacji ogłoszeń oraz automatyczne wątki."
    )
    @app_commands.describe(
        kanal="Dedykowany kanał tekstowy na ogłoszenia handlowe",
        auto_watek="Czy bot ma automatycznie zakładać wątek dyskusji pod ogłoszeniem?"
    )
    @app_commands.default_permissions(administrator=True)
    async def rynek_ustaw(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
        auto_watek: bool = True
    ) -> None:
        """Konfiguracja modułu rynku dla administracji."""
        if not interaction.guild:
            return

        # Sprawdź uprawnienia bota
        perms = kanal.permissions_for(interaction.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                embed=error_embed(
                    "Brak uprawnień bota",
                    f"Bot nie ma uprawnień do wysyłania wiadomości i embedów na kanale {kanal.mention}."
                ),
                ephemeral=True
            )
            return

        # Zaktualizuj guild_config
        auto_watek_val = 1 if auto_watek else 0
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, marketplace_channel_id, marketplace_auto_thread)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                marketplace_channel_id = excluded.marketplace_channel_id,
                marketplace_auto_thread = excluded.marketplace_auto_thread
            """,
            (interaction.guild.id, kanal.id, auto_watek_val)
        )

        await interaction.response.send_message(
            embed=success_embed(
                "Konfiguracja Rynku Zapisana! ✅",
                f"Kanał ogłoszeń: {kanal.mention}\n"
                f"Automatyczne wątki ofert i pytań: **{'Włączone' if auto_watek else 'Wyłączone'}**"
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketplaceCog(bot))