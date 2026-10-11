"""
Dedykowany moduł serwera MTA: ProjectRPG (/prpg).
Zawiera status serwera live (ASE), wyszukiwarkę graczy online, listę graczy z paginacją, zaawansowany kalkulator zarobków z oficjalnymi stawkami z forum (zaktualizowany pod 2026 rok), cennik tuningu aut, taryfikator mandatów SAPD, wzory apelacji na forum oraz informacje o frakcjach.
"""

import socket
import asyncio
import logging
import time
import math
import re
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import success_embed, error_embed, info_embed, create_embed
from utils.helpers import format_number
import config

logger = logging.getLogger("AegisBot.Cogs.ProjectRPG")

SERVER_IP = "s.projectrpg.pl"
SERVER_PORT = 22003
SERVER_ASE_IP = "s.projectrpg.pl"
SERVER_ASE_PORT = 22126
FORUM_URL = "https://projectrpg.pl"

# Oficjalne, zweryfikowane dane prac i zarobków z forum ProjectRPG (Kompletny Poradnik do Prac Dorywczych 2026 by ★ Verfi)
# Uwaga: Stawki bazowe (bez Premium 1.0x). Z kontem Premium (+20%) oraz ulepszeniami przeliczane są dynamicznie.
JOBS_DATABASE = {
    "kurier": {
        "name": "🚚 Kurier RedEx / TransMax (Rework 2026)",
        "short_name": "Kurier RedEx",
        "location": "Las Venturas (Redsands East) / San Fierro (Garcia)",
        "req": "Prawo Jazdy kat. C • 800 RP",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Najpopularniejsza praca)",
        "actions_per_hour": "50 – 70 paczek / h",
        "exp_rp_gain": "30 – 60 EXP / h",
        "base_min": 3400,  # ~4100$ z Premium
        "base_max": 4200,  # ~5000$ z Premium
        "upg_tiers": {
            "tier1": {"label": "P1: Couriero (100% wartości, 90% tempo)", "mult": 1.00, "desc": "Pojazd Couriero, podstawowa ładownia, brak ulepszeń."},
            "tier2": {"label": "P2: Speedvan (110% wartości, 130% tempo)", "mult": 1.25, "desc": "Pojazd Speedvan, wartość 110%, tempo 130% (wymaga: 21k $, 4k RP, 2.1k XP)."},
            "tier3": {"label": "P3: Carrier (120% wartości, 160% tempo)", "mult": 1.50, "desc": "Pojazd Carrier, nowe rejony, sortownik paczek (wymaga: 31.5k $, 8k RP, 3.15k XP)."},
            "tier4": {"label": "P4: Carrier MAX (130% wartości, 200% tempo)", "mult": 1.85, "desc": "Wymaksowany Carrier, wartość 130%, tempo 200%, 100% wytrzymałości (zarobek 7,500 - 10,000 $)."}
        },
        "individual_trees": [
            "🚗 **Typ pojazdu:** Couriero ➔ **Speedvan** *(21,000 $, 4,000 RP, 2,100 XP)* ➔ **Carrier** *(31,500 $, 8,000 RP, 3,150 XP)*",
            "📦 **Wartość przesyłek:** 100% ➔ **110%** *(21,000 $, 4,000 RP, 2,100 XP)* ➔ 120% ➔ 130%",
            "⚡ **Tempo pracy:** 90% ➔ **130%** *(15,000 $, 1,800 RP, 1,200 XP)* ➔ 160% ➔ 200%",
            "🛡️ **Wytrzymałość pojazdu:** 50% ➔ **75%** *(5,000 $, 1,000 RP, 600 XP)* ➔ 100%",
            "📱 **Sortownik paczek:** Ulepszenie bagażnika & tabletu *(25,000 $, 3,000 RP, 1,500 XP)*"
        ],
        "tip": "Praca w CO-OP jest możliwa, lecz nie wpływa znacząco na stawkę. Najważniejsze to tempo 200% i Carrier!"
    },
    "kosiarki": {
        "name": "🌿 Koszenie Trawy / Kosiarki",
        "short_name": "Kosiarki",
        "location": "Las Venturas (Whitewood / Rockshore) / Angel Pine",
        "req": "Prawo Jazdy kat. B • 300 RP",
        "rating": "⭐⭐⭐⭐☆ (4/5 — Świetny zarobek na MAX LVL)",
        "actions_per_hour": "700 – 800 kg trawy / h",
        "exp_rp_gain": "50 – 120 EXP / h • 40 – 60 RP / h",
        "base_min": 3500,  # ~4200$ z Premium
        "base_max": 4200,
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Kosiarka standardowa, ~4,200 $ z Premium."},
            "tier2": {"label": "Kosz Powiększony (+25%)", "mult": 1.25, "desc": "Rzadsze zjazdy na opróżnianie kosza."},
            "tier3": {"label": "Silnik Sportowy (+55%)", "mult": 1.55, "desc": "Szybkie manewrowanie po trawnikach."},
            "tier4": {"label": "MAX LVL: Mulczer (+95%)", "mult": 1.95, "desc": "Podwójne noże, zbiór 800 kg/h (~8,200 $ z Premium)."}
        },
        "individual_trees": [
            "🧺 **Pojemność Kosza:** Kosz Standard ➔ Powiększony ➔ Zbiornik XXL",
            "🚜 **Silnik Kosiarki:** Silnik 1.0 ➔ Silnik 1.6 ➔ Silnik Sportowy",
            "✂️ **Noże Tnące:** Noże standardowe ➔ Podwójne ostrza ➔ Listwa mulczująca",
            "⭐ **Bonus Jakości:** Premia za 100% skoszonej posesji"
        ],
        "tip": "Na maksymalnym ulepszeniu kosiarki osiągają ~8,200 $/h, co czyni je jedną z najlepszych prac!"
    },
    "magazynier": {
        "name": "📦 Magazynier (Bayside)",
        "short_name": "Magazynier",
        "location": "Bayside Marina (Północ Mapy)",
        "req": "Brak wymagań (0 RP)",
        "rating": "⭐⭐⭐⭐☆ (4/5 — Bardzo dużo EXP i RP)",
        "actions_per_hour": "240 – 250 paczek / h",
        "exp_rp_gain": "550 – 600 EXP / h • 50 – 70 RP / h (Najwięcej!)",
        "base_min": 3350,  # 4000$ z Premium
        "base_max": 3750,  # 4500$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Noszenie ręczne, 4,000 - 4,500 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+15%)", "mult": 1.15, "desc": "Wózek paletowy, 4,600 - 5,200 $ z Premium."},
            "tier3": {"label": "4 LVL (+25%)", "mult": 1.25, "desc": "Wózek widłowy Forklift, 5,000 - 5,600 $ z Premium."},
            "tier4": {"label": "5 LVL MAX (+35%)", "mult": 1.35, "desc": "Wymaksowany poziom pracy, 5,100 - 6,000 $ z Premium."}
        },
        "individual_trees": [
            "📈 **Poziomy Pracy (1 – 5):** Stały serwerowy bonus +5% gotówki za każdy wbity poziom",
            "🛒 **Narzędzia:** Ręce ➔ Wózek paletowy ➔ Wózek widłowy Forklift",
            "⚡ **Kondycja:** Szybszy chód z paczką i mniejsze zmęczenie"
        ],
        "tip": "Najlepsza praca na start dla nowego gracza ze względu na rekordowy przyrost EXP (do 600/h) i RP!"
    },
    "grzybiarz": {
        "name": "🍄 Grzybiarz (Lasy Flint County)",
        "short_name": "Grzybiarz",
        "location": "Lasy Flint County / Back o Beyond",
        "req": "Brak wymagań (0 RP)",
        "rating": "⭐⭐⭐⭐☆ (4/5 — Stabilne 5,000 - 7,000 $)",
        "actions_per_hour": "~600 grzybów / h",
        "exp_rp_gain": "400 – 500 EXP / h • 10 – 30 RP / h",
        "base_min": 4150,  # 5000$ z Premium
        "base_max": 4600,  # 5500$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Zbieranie podstawowe: 5,000 - 5,500 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+12%)", "mult": 1.12, "desc": "Większy plecak na grzyby, szybszy zbiór."},
            "tier3": {"label": "4 LVL (+20%)", "mult": 1.20, "desc": "Atlas grzybów, wykrywanie borowików i trufli."},
            "tier4": {"label": "5 LVL MAX (+28%)", "mult": 1.28, "desc": "Wymaksowany poziom: 6,300 - 7,000 $ z Premium."}
        },
        "individual_trees": [
            "🧺 **Ekwipunek:** Koszyk wiklinowy ➔ Plecak ➔ Skrzynia próżniowa",
            "📖 **Ceny w Skupie:** Pieczarka (8.36-9.88$) • Borowik (9.12-10.64$) • Muchomor (6.08-7.60$)"
        ],
        "tip": "Zbieraj głównie borowiki (do 10.64 $ za sztukę) w gęstych lasach Flint County."
    },
    "kutry": {
        "name": "🛥️ Kutry Rybackie (Morze & Ocean)",
        "short_name": "Kutry Rybackie",
        "location": "Bayside Marina / Port San Fierro",
        "req": "1,500 RP • Licencja na łodzie (W)",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Ponad 8,000 $ na godzinę)",
        "actions_per_hour": "2,300 – 2,500 kg ryb / h (kurs 3.0$ – 3.3$ / kg)",
        "exp_rp_gain": "50 – 70 EXP / h",
        "base_min": 5400,  # 6500$ z Premium
        "base_max": 5850,  # 7000$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "~2,300 kg ryb/h: 6,500 - 7,000 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+10%)", "mult": 1.10, "desc": "Lepsze sieci, połów ~2,400 kg ryb/h."},
            "tier3": {"label": "4 LVL (+18%)", "mult": 1.18, "desc": "Echosonda, szybsze wyciąganie sieci z wody."},
            "tier4": {"label": "5 LVL MAX (+25%)", "mult": 1.25, "desc": "~2,500 kg ryb/h: 7,500 - 8,500 $ z Premium."}
        },
        "individual_trees": [
            "🎣 **Sieci:** Małe sieci ➔ Sieci głębinowe ➔ Włoki dalekomorskie",
            "🐟 **Cena w skupie:** Średnio 3.00 $ – 3.30 $ za 1 kg złowionych ryb"
        ],
        "tip": "Wypływaj daleko od brzegu i szukaj zagęszczonych ławic na radarze."
    },
    "drwal": {
        "name": "🌲 Drwal (Ścinanie & Pielęgnacja)",
        "short_name": "Drwal",
        "location": "Lasy Whetstone / Shady Creeks / Las Paysadas",
        "req": "700 RP (Pielęgnacja) • 1,300 RP (Ścinanie) • 800 RP (Magazyn)",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Nawet do 10,000 $ / h)",
        "actions_per_hour": "750 – 800 akcji pielęgnacji / h",
        "exp_rp_gain": "350 – 400 EXP / h • 15 – 30 RP / h",
        "base_min": 5000,  # 6000$ z Premium na ścinaniu
        "base_max": 6650,  # 8000$ z Premium na ścinaniu
        "upg_tiers": {
            "tier1": {"label": "💧 Pielęgnacja (0 LVL)", "mult": 0.75, "desc": "Pielęgnacja drzew: 4,500 - 5,000 $ z Premium (700 RP)."},
            "tier2": {"label": "💧 Pielęgnacja (5 LVL)", "mult": 1.00, "desc": "Pielęgnacja 5 LVL: 6,000 - 7,000 $ z Premium."},
            "tier3": {"label": "🪓 Ścinanie (0 LVL)", "mult": 1.15, "desc": "Ścinanie drzew: 6,000 - 8,000 $ z Premium (1,300 RP)."},
            "tier4": {"label": "🪓 Ścinanie (5 LVL MAX)", "mult": 1.45, "desc": "Ścinanie 5 LVL: 7,500 - 10,000 $ z Premium!"}
        },
        "individual_trees": [
            "💧 **Pielęgnacja (700 RP):** Podlewanie i okrzesywanie (4,500 - 7,000 $)",
            "🪓 **Ścinanie (1,300 RP):** Piła spalinowa Stihl (6,000 - 10,000 $)",
            "📦 **Magazynowanie (800 RP):** Wywóz Boxville do Las Paysadas (5,000 - 7,000 $)"
        ],
        "tip": "Moduł Ścinania (1300 RP) oferuje jedne z najwyższych stawek w całej grze (do 10k $/h)!"
    },
    "szambiarz": {
        "name": "💩 Szambiarz (Wywóz Nieczystości)",
        "short_name": "Szambiarz",
        "location": "Bone County / Wasteland",
        "req": "Prawo Jazdy kat. C • 800 RP",
        "rating": "⭐⭐⭐⭐☆ (4/5 — Bardzo stabilne 5,500 - 8,000 $)",
        "actions_per_hour": "20,000 – 30,000 L / h",
        "exp_rp_gain": "150 – 250 EXP / h • 0 – 10 RP / h",
        "base_min": 4600,  # 5500$ z Premium
        "base_max": 5400,  # 6500$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Zbiornik 20,000L: 5,500 - 6,500 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+10%)", "mult": 1.10, "desc": "Szybsza pompa ssąca, 6,000 - 7,200 $ z Premium."},
            "tier3": {"label": "4 LVL (+18%)", "mult": 1.18, "desc": "Zbiornik powiększony 25,000L."},
            "tier4": {"label": "5 LVL MAX (+25%)", "mult": 1.25, "desc": "Zbiór 30,000L/h: 7,000 - 8,000 $ z Premium."}
        },
        "individual_trees": [
            "🛢️ **Pojemność Cysterny:** 20,000 L ➔ 25,000 L ➔ 30,000 L",
            "💨 **Wydajność Pompy:** Skrócony czas opróżniania studzienek"
        ],
        "tip": "Wybieraj studzienki o maksymalnym zapełnieniu, aby skrócić czas postoju."
    },
    "lawety": {
        "name": "🚛 Lawety / Transport Kontenerów (Spinybed LV)",
        "short_name": "Lawety / Kontenery",
        "location": "Las Venturas (Spinybed — Port Towarowy)",
        "req": "Prawo Jazdy kat. C • 2,500 RP • Licencja Transportowa (8,000 $)",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Mediana ponad 10,000 $ na MAX LVL)",
        "actions_per_hour": "6 – 10 kursów / h (tryb Solo lub Konwój CO-OP)",
        "exp_rp_gain": "75 – 110 EXP na 1 kurs (wg poziomu silnika)",
        "base_min": 3350,  # 4000$ z Premium
        "base_max": 4600,  # 5500$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (1 LVL Silnika)", "mult": 1.00, "desc": "6 kursów/h: 4,000 - 5,500 $ z Premium (75-85 EXP/kurs)."},
            "tier2": {"label": "2 LVL Silnika (+25%)", "mult": 1.25, "desc": "Szybszy ciągnik: 5,500 - 7,000 $ z Premium (85-95 EXP/kurs)."},
            "tier3": {"label": "3 LVL Silnika (+45%)", "mult": 1.45, "desc": "8 kursów/h: 6,500 - 8,000 $ z Premium (100-110 EXP/kurs)."},
            "tier4": {"label": "MAX LVL: Przeładowane (+80%)", "mult": 1.80, "desc": "10 kursów/h + ładunki przeładowane: 8,000 - 10,100 $ z Premium!"}
        },
        "individual_trees": [
            "🚜 **Ulepszenie Silnika:** 1 LVL (75 EXP) ➔ 2 LVL (85 EXP) ➔ 3 LVL (100-110 EXP na kurs)",
            "🚛 **Typ Ładunku:** Zwykłe kontenery ➔ Kontenery ciężkie ➔ Ładunki przeładowane (+bonus)",
            "👥 **Tryb Konwoju (CO-OP):** Wspólna jazda z innym kierowcą po trasie",
            "📜 **Licencja Transportowa:** Wymagany jednorazowy zakup w Urzędzie za 8,000 $"
        ],
        "tip": "Mediana zarobków z forum wynosi aż ~10,100 $/h na MAX ulepszeniu i bonusie TOP 3."
    },
    "dodo": {
        "name": "🛩️ Dodo — Gaszenie Pożarów",
        "short_name": "Dodo Pożary",
        "location": "Lotnisko San Fierro / Las Venturas",
        "req": "1,000 RP • Licencja Lotnicza (L)",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Do 9,000 $ / h)",
        "actions_per_hour": "50 – 60 ugaszonych pożarów / h",
        "exp_rp_gain": "200 – 300 EXP / h • 10 – 20 RP / h",
        "base_min": 5000,  # 6000$ z Premium
        "base_max": 5750,  # 6900$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "50 pożarów/h: 6,000 - 6,900 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+12%)", "mult": 1.12, "desc": "Szybszy zrzut wody, 6,800 - 7,800 $ z Premium."},
            "tier3": {"label": "4 LVL (+20%)", "mult": 1.20, "desc": "Większy zbiornik na wodę gaśniczą."},
            "tier4": {"label": "5 LVL MAX (+30%)", "mult": 1.30, "desc": "60 pożarów/h: 8,000 - 9,000 $ z Premium!"}
        },
        "individual_trees": [
            "✈️ **Samolot:** Dodo gaśniczy ze zbiornikiem podkadłubowym",
            "🎯 **Precyzja:** Premia za bezpośrednie trafienie w ognisko pożaru"
        ],
        "tip": "Ćwicz niskie przeloty, aby gasić pożary za pierwszym podejściem bez nawrotów."
    },
    "gornik": {
        "name": "⛏️ Górnik / Kopalnia (Hunter Quarry)",
        "short_name": "Górnik Kopalnia",
        "location": "Las Venturas (Hunter Quarry)",
        "req": "4,000 RP • Licencja Górnicza",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Szansa na diamenty, do 10,000 $)",
        "actions_per_hour": "Wydobycie ciągłe + 4 – 6 diamentów / h",
        "exp_rp_gain": "Zależne od ilości przepalonej rudy",
        "base_min": 3750,  # 4500$ z Premium
        "base_max": 5000,  # 6000$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "Węgiel i miedź: 4,500 - 6,000 $ z Premium."},
            "tier2": {"label": "Wzmacniany Kilof (+25%)", "mult": 1.25, "desc": "Żelazo i srebro, 5,500 - 7,500 $ z Premium."},
            "tier3": {"label": "Piec Hutniczy (+45%)", "mult": 1.45, "desc": "Sztabki złota + 3-4 diamenty/h."},
            "tier4": {"label": "MAX LVL (+70%)", "mult": 1.70, "desc": "Czyste diamenty (4-6 sztuk/h): 7,000 - 10,000 $ z Premium!"}
        },
        "individual_trees": [
            "⛏️ **Narzędzia:** Kilof stalowy ➔ Tytanowy ➔ Diamentowy",
            "💎 **Diamenty:** 4 – 6 diamentów na godzinę przy pełnym ulepszeniu",
            "🔥 **Piec Hutniczy:** Wytapianie sztabek i sprzedaż na górce kursowej"
        ],
        "tip": "Zbieraj surowce i sprzedawaj w skupie dopiero, gdy kurs osiągnie maksymalny poziom!"
    },
    "sweepery": {
        "name": "🛣️ Sprzątanie Ulic / Sweepery",
        "short_name": "Sweepery Uliczne",
        "location": "El Quebrados / Las Venturas / San Fierro",
        "req": "Prawo Jazdy kat. B",
        "rating": "⭐⭐⭐☆☆ (3.5/5 — Bezpieczna praca)",
        "actions_per_hour": "800 – 850 kg śmieci / h",
        "exp_rp_gain": "350 – 400 EXP / h • 0 – 10 RP / h",
        "base_min": 3500,  # 4200$ z Premium
        "base_max": 4150,  # 5000$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Start)", "mult": 1.00, "desc": "800 kg/h: 4,200 - 5,000 $ z Premium."},
            "tier2": {"label": "2-3 LVL (+12%)", "mult": 1.12, "desc": "Szersze szczotki, 4,800 - 5,600 $ z Premium."},
            "tier3": {"label": "4 LVL (+20%)", "mult": 1.20, "desc": "Mocniejsza turbina, 5,000 - 5,900 $ z Premium."},
            "tier4": {"label": "5 LVL MAX (+28%)", "mult": 1.28, "desc": "850 kg/h: 5,400 - 6,300 $ z Premium."}
        },
        "individual_trees": [
            "🧹 **Szczotki:** Standard ➔ Poszerzone ➔ Podwójne szczotki boczne",
            "🌪️ **Turbina:** Wyższa dopuszczalna prędkość zamiatania"
        ],
        "tip": "Miasteczko El Quebrados jest uważane przez graczy za najszybszą trasę na sweeperach."
    },
    "taxi": {
        "name": "🚕 Aplikacja Transportowa (Bolt / Taxi)",
        "short_name": "Aplikacja Transportowa",
        "location": "Cała mapa San Andreas",
        "req": "Własny pojazd 4-osobowy • Moduł aplikacji",
        "rating": "⭐⭐⭐⭐⭐ (5/5 — Do 12,000 $ z APK PRO)",
        "actions_per_hour": "Kursy miejskie i międzymiastowe",
        "exp_rp_gain": "Zależne od przejechanych kilometrów",
        "base_min": 4150,  # 5000$ z Premium
        "base_max": 5000,  # 6000$ z Premium
        "upg_tiers": {
            "tier1": {"label": "0 LVL (Zwykła APK)", "mult": 1.00, "desc": "5,000 - 6,000 $ z Premium (bez kosztów paliwa)."},
            "tier2": {"label": "APK Średnia (+30%)", "mult": 1.30, "desc": "Lepsze zlecenia, 6,500 - 8,000 $ z Premium."},
            "tier3": {"label": "APK Zaawansowana (+60%)", "mult": 1.60, "desc": "Zlecenia VIP i międzymiastowe, 8,000 - 10,000 $ z Premium."},
            "tier4": {"label": "APK PRO MAX (+100%)", "mult": 2.00, "desc": "Aplikacja PRO: 9,000 - 12,000 $ z Premium!"}
        },
        "individual_trees": [
            "📱 **Wersja Aplikacji:** Zwykła APK (5-6k $) ➔ APK PRO (9-12k $)",
            "🏎️ **Pojazd:** Wymagany własny 4-drzwiowy samochód z modułem"
        ],
        "tip": "Używaj ekonomicznego pojazdu na LPG, aby zminimalizować koszty paliwa!"
    },
    "wedkarz": {
        "name": "🐟 Wędkarz (Wędkarstwo z Brzegu / Łodzi)",
        "short_name": "Wędkarz",
        "location": "Pomosty, rzeki i jeziora San Andreas",
        "req": "Wędka • Przynęty (Opcjonalnie: własna łódź)",
        "rating": "⭐⭐⭐⭐☆ (4/5 — Stała stawka 36 $ / kg ryb)",
        "actions_per_hour": "Połowy ciągłe ryb",
        "exp_rp_gain": "EXP za każdą wyciągniętą rybę",
        "base_min": 3750,
        "base_max": 5000,
        "upg_tiers": {
            "tier1": {"label": "Wędka Zwykła (36$/kg)", "mult": 1.00, "desc": "Podstawowe ryby brzegowe: 4,500 - 5,500 $ z Premium."},
            "tier2": {"label": "Wędka Węglowa (+20%)", "mult": 1.20, "desc": "Mniejsza szansa na zerwanie ryby, 5,500 - 6,500 $ z Premium."},
            "tier3": {"label": "Wędka Morska (+40%)", "mult": 1.40, "desc": "Połowy z własnej łodzi, 6,500 - 7,800 $ z Premium."},
            "tier4": {"label": "Wędka PRO MAX (+60%)", "mult": 1.60, "desc": "Ciężkie okazy morskie: 7,500 - 9,000 $ z Premium!"}
        },
        "individual_trees": [
            "🎣 **Stawka:** Stałe 36 $ za każdy 1 kg ryb",
            "⚠️ **Ryzyko:** Ulepszanie wędki u NPC może spalić poziom przy niepowodzeniu!"
        ],
        "tip": "Wędkarstwo z łodzi na pełnym morzu przynosi znacznie cięższe okazy."
    },
}


def _build_job_embed(job_key: str, tier_key: str = "tier1", premium: bool = False, server_mult: float = 1.0) -> discord.Embed:
    """Tworzy bogaty embed zarobków dla wybranej pracy z oficjalnymi zweryfikowanymi danymi z forum."""
    job = JOBS_DATABASE.get(job_key, JOBS_DATABASE["kurier"])
    tier_info = job["upg_tiers"].get(tier_key, job["upg_tiers"]["tier1"])

    mult_upg = tier_info["mult"]
    mult_prem = 1.20 if premium else 1.00
    total_multiplier = mult_upg * mult_prem * server_mult

    final_min = int(job["base_min"] * total_multiplier)
    final_max = int(job["base_max"] * total_multiplier)
    avg = int((final_min + final_max) / 2)

    avg_15m = int(avg / 4)
    avg_30m = int(avg / 2)

    embed = create_embed(
        title=f"💼 Zarobki Prac — {job['name']}",
        description=f"Oficjalne statystyki z serwera **ProjectRPG** (zaktualizowane z forum na **2026 rok**):\n\n"
                    f"💰 **Średni zarobek / godzina:** `~{format_number(avg)} $ / h`\n"
                    f"📊 **Przedział stawek:** `{format_number(final_min)}` — `{format_number(final_max)} $ / h`\n"
                    f"⏱️ **Na 30 minut:** `~{format_number(avg_30m)} $`  •  **Na 15 minut:** `~{format_number(avg_15m)} $`\n\n"
                    f"🔥 **Łączny Mnożnik:** `x{total_multiplier:.2f}` (Ulepszenie `{mult_upg}x` × Premium `{'1.20x' if premium else '1.00x'}` × Event `{server_mult}x`)",
        color=config.SUCCESS_COLOR
    )
    embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")

    embed.add_field(name="📍 Lokalizacja", value=f"`{job['location']}`", inline=True)
    embed.add_field(name="🎯 Wymagania", value=f"`{job['req']}`", inline=True)
    embed.add_field(name="⭐ Ocena", value=job['rating'], inline=True)

    embed.add_field(name="⚡ Wydajność / h", value=f"• **Tempo:** `{job.get('actions_per_hour', 'Brak danych')}`\n• **Przyrost EXP/RP:** `{job.get('exp_rp_gain', 'Brak danych')}`", inline=False)

    # Indywidualne ulepszenia tej pracy
    embed.add_field(
        name="🛠️ Drzewko Indywidualnych Ulepszeń tej Pracy (1:1)",
        value="\n".join(job["individual_trees"]),
        inline=False
    )

    # Tabela poziomów ulepszeń
    tier_lines = []
    for k, v in job["upg_tiers"].items():
        is_sel = "👉 " if k == tier_key else "• "
        t_min = int(job["base_min"] * v["mult"] * mult_prem * server_mult)
        t_max = int(job["base_max"] * v["mult"] * mult_prem * server_mult)
        tier_lines.append(f"{is_sel}**{v['label']}:** `~{format_number((t_min + t_max)//2)} $/h` — *{v['desc']}*")

    embed.add_field(name="📈 Porównanie Poziomów Ulepszeń", value="\n".join(tier_lines), inline=False)
    embed.add_field(name="💡 Wskazówka z Forum", value=f"*{job['tip']}*", inline=False)
    embed.set_footer(text="AegisBot • Zweryfikowane Poradniki ProjectRPG Forum (2026)")
    return embed


class JobDashboardView(discord.ui.View):
    """Interaktywny panel kalkulatora prac z menu wyboru pracy i przełącznikami ulepszeń."""

    def __init__(self, current_job: str = "kurier", current_tier: str = "tier1", premium: bool = True, server_mult: float = 1.0, user_id: int = 0) -> None:
        super().__init__(timeout=180)
        self.current_job = current_job
        self.current_tier = current_tier
        self.premium = premium
        self.server_mult = server_mult
        self.user_id = user_id

        # Zbuduj opcje menu wyboru pracy
        job_options = [
            discord.SelectOption(label=data["short_name"], value=key, default=(key == self.current_job), description=data["location"][:50])
            for key, data in JOBS_DATABASE.items()
        ]
        self.job_select.options = job_options[:25]
        self._update_button_labels()

    def _update_button_labels(self) -> None:
        self.prem_btn.label = f"Premium (+20%): {'WŁĄCZONE' if self.premium else 'WYŁĄCZONE'}"
        self.prem_btn.style = discord.ButtonStyle.success if self.premium else discord.ButtonStyle.secondary

        self.event_btn.label = f"Mnożnik: {self.server_mult}x"
        self.event_btn.style = discord.ButtonStyle.primary if self.server_mult > 1.0 else discord.ButtonStyle.secondary

        job = JOBS_DATABASE.get(self.current_job, JOBS_DATABASE["kurier"])
        tier_info = job["upg_tiers"].get(self.current_tier, job["upg_tiers"]["tier1"])
        self.tier_btn.label = f"Ulepszenie: {tier_info['label']}"

    @discord.ui.select(placeholder="Wybierz inną pracę z listy...", min_values=1, max_values=1, custom_id="job_select_menu")
    async def job_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.current_job = select.values[0]
        self.current_tier = "tier1"
        self._update_button_labels()
        for opt in self.job_select.options:
            opt.default = (opt.value == self.current_job)
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Ulepszenie: P1", style=discord.ButtonStyle.secondary, custom_id="job_tier_btn", row=1)
    async def tier_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        tiers = ["tier1", "tier2", "tier3", "tier4"]
        cur_idx = tiers.index(self.current_tier) if self.current_tier in tiers else 0
        self.current_tier = tiers[(cur_idx + 1) % len(tiers)]
        self._update_button_labels()
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Premium (+20%): WŁĄCZONE", style=discord.ButtonStyle.success, custom_id="job_prem_btn", row=1)
    async def prem_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.premium = not self.premium
        self._update_button_labels()
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Mnożnik: 1.0x", style=discord.ButtonStyle.secondary, custom_id="job_event_btn", row=1)
    async def event_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        mults = [1.0, 1.15, 1.30, 1.50, 2.00]
        cur_idx = mults.index(self.server_mult) if self.server_mult in mults else 0
        self.server_mult = mults[(cur_idx + 1) % len(mults)]
        self._update_button_labels()
        embed = _build_job_embed(self.current_job, self.current_tier, self.premium, self.server_mult)
        await interaction.response.edit_message(embed=embed, view=self)


def _query_ase_full() -> dict:
    """Odpytuje serwer MTA ProjectRPG przez protokół ASE (UDP) i zwraca pełne dane oraz listę graczy online."""
    result = {
        'online': False,
        'players': 0,
        'max_players': 900,
        'name': 'ProjectRPG',
        'gamemode': '▶ ProjectRPG',
        'version': '1.6',
        'ping_ms': 0,
        'player_list': []
    }
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.5)
    try:
        t_start = time.monotonic()
        s.sendto(b's', (SERVER_ASE_IP, SERVER_ASE_PORT))
        data, _ = s.recvfrom(65535)
        result['ping_ms'] = round((time.monotonic() - t_start) * 1000)
        result['online'] = True

        pos = 4  # Pomiń nagłówek 'EYE1'

        def read_field():
            nonlocal pos
            length = data[pos]
            pos += 1
            value = data[pos:pos + length - 1].decode('utf-8', errors='replace')
            pos += length - 1
            return value

        read_field()  # game ("mta")
        read_field()  # port ("22003")
        result['name'] = read_field()
        result['gamemode'] = read_field()
        read_field()  # map
        result['version'] = read_field()
        read_field()  # passworded ("0" / "1")
        players_str = read_field()
        max_players_str = read_field()

        result['players'] = int(players_str) if players_str.isdigit() else 0
        result['max_players'] = int(max_players_str) if max_players_str.isdigit() else 900

        # Wyciągnij listę nicków graczy z pozostałych bajtów odpowiedzi ASE
        remaining = data[pos:]
        player_names = []
        idx = 0
        while idx < len(remaining) - 2:
            if remaining[idx] == 0x01 and remaining[idx + 1] == 0x3f:
                idx += 2
                if idx >= len(remaining):
                    break
                name_len = remaining[idx]
                idx += 1
                if name_len > 1 and idx + name_len - 1 <= len(remaining):
                    raw_name = remaining[idx:idx + name_len - 1].decode('utf-8', errors='replace')
                    idx += name_len - 1
                    if raw_name:
                        clean = re.sub(r'#([a-fA-F0-9]{6})', '', raw_name)
                        clean = re.sub(r'[^\x20-\x7e\u0080-\uffff]', '', clean).strip()
                        if clean and clean not in player_names:
                            player_names.append(clean)
                else:
                    idx += 1
            else:
                idx += 1

        result['player_list'] = player_names
    except Exception as e:
        logger.debug(f"ASE query error: {e}")
    finally:
        s.close()
    return result


class PlayerListPaginator(discord.ui.View):
    """Paginator widoku listy graczy z interaktywnymi przyciskami."""

    def __init__(self, players: list[str], total_online: int, max_players: int, ping_ms: int, user_id: int) -> None:
        super().__init__(timeout=90)
        self.players = players
        self.total_online = total_online
        self.max_players = max_players
        self.ping_ms = ping_ms
        self.user_id = user_id
        self.page = 0
        self.per_page = 20
        self.max_pages = max(1, math.ceil(len(players) / self.per_page))
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= self.max_pages - 1)
        self.page_indicator.label = f"Strona {self.page + 1} / {self.max_pages}"

    def build_embed(self) -> discord.Embed:
        fill_pct = int((self.total_online / self.max_players * 100)) if self.max_players else 0
        start = self.page * self.per_page
        end = start + self.per_page
        page_players = self.players[start:end]

        embed = create_embed(
            title="👥 Gracze Online — ProjectRPG (MTA:SA)",
            description=f"Aktualny stan serwera pobrany na żywo przez protokół ASE:\n\n"
                        f"🟢 **Status:** `ONLINE`  •  👥 **Gracze:** `{self.total_online} / {self.max_players}` (`{fill_pct}%`)\n"
                        f"📍 **IP:** `{SERVER_IP}:{SERVER_PORT}`  •  📶 **Ping:** `{self.ping_ms} ms`\n",
            color=config.DEFAULT_EMBED_COLOR
        )

        if page_players:
            half = math.ceil(len(page_players) / 2)
            col1 = [f"`{start + i + 1:02d}.` **{p}**" for i, p in enumerate(page_players[:half])]
            col2 = [f"`{start + half + i + 1:02d}.` **{p}**" for i, p in enumerate(page_players[half:])]

            embed.add_field(name=f"📋 Lista Graczy (Część 1)", value="\n".join(col1) if col1 else "Brak", inline=True)
            embed.add_field(name=f"📋 Lista Graczy (Część 2)", value="\n".join(col2) if col2 else "—", inline=True)
        else:
            embed.add_field(name="📋 Lista Graczy", value="*Brak graczy na tej stronie.*", inline=False)

        embed.set_footer(text=f"Strona {self.page + 1} z {self.max_pages}  •  Użyj /prpg szukaj <nick>, aby sprawdzić gracza")
        return embed

    @discord.ui.button(label="◀ Poprzednia", style=discord.ButtonStyle.secondary, custom_id="prpg_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Strona 1 / 1", style=discord.ButtonStyle.primary, disabled=True, custom_id="prpg_page_ind")
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="Następna ▶", style=discord.ButtonStyle.secondary, custom_id="prpg_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ta paginacja należy do innego użytkownika.", ephemeral=True)
            return
        if self.page < self.max_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)


class ProjectRPGCog(commands.Cog):
    """Komendy i narzędzia dedykowane dla graczy serwera MTA: ProjectRPG."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._status_messages: dict[int, discord.Message] = {}

    prpg_group = app_commands.Group(
        name="prpg",
        description="Komendy i kalkulatory dla serwera MTA: ProjectRPG"
    )

    def _build_status_embed(self, info: dict) -> discord.Embed:
        """Buduje embed statusu serwera ProjectRPG z danymi live."""
        if info['online']:
            status_badge = "🟢 ONLINE"
            players = info['players']
            max_players = info['max_players']
            fill_pct = int((players / max_players * 10)) if max_players else 0
            bar = '█' * fill_pct + '░' * (10 - fill_pct)
            players_str = f"`{players}` / `{max_players}`  [{bar}]"
            version = info.get('version', '1.6')
            ping = info.get('ping_ms', 0)
        else:
            status_badge = "🔴 OFFLINE"
            players_str = "Niedostępne"
            version = "1.6"
            ping = 0

        embed = create_embed(
            title="🎮 Status Serwera — ProjectRPG (MTA:SA)",
            description="Największy i najbardziej zaawansowany polski serwer RPG na platformie Multi Theft Auto.",
            color=config.DEFAULT_EMBED_COLOR if info['online'] else config.ERROR_COLOR
        )
        embed.set_thumbnail(url="https://projectrpg.pl/uploads/monthly_2021_04/logo.png.31885b5e7d58fbbca6006429f45f956c.png")
        embed.add_field(name="📶 Status", value=f"**{status_badge}**", inline=True)
        embed.add_field(name="📍 Adres IP", value=f"`{SERVER_IP}:{SERVER_PORT}`", inline=True)
        embed.add_field(name="🌐 Wersja MTA", value=f"`{version}`", inline=True)
        embed.add_field(name="👥 Gracze Online", value=players_str, inline=False)
        embed.add_field(
            name="🔗 Szybkie Odnośniki",
            value=f"• [Oficjalne Forum]({FORUM_URL})  •  [Apelacje]({FORUM_URL}/forum/38-apelacje-od-kar/)  •  [Frakcje]({FORUM_URL}/forum/34-frakcje/)",
            inline=False
        )
        embed.set_footer(text=f"⏱️ Aktualizacja co 30 sekund  •  Ping: {ping} ms")
        return embed

    @tasks.loop(seconds=30)
    async def _refresh_status(self) -> None:
        """Co 30 sekund odpytuje serwer ASE i edytuje wiadomości statusu."""
        if not self._status_messages:
            return
        info = await asyncio.get_event_loop().run_in_executor(None, _query_ase_full)
        embed = self._build_status_embed(info)
        dead = []
        for gid, msg in self._status_messages.items():
            try:
                await msg.edit(embed=embed)
            except Exception:
                dead.append(gid)
        for gid in dead:
            del self._status_messages[gid]

    @_refresh_status.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    @prpg_group.command(name="status", description="Wyświetla aktualny status serwera MTA: ProjectRPG z liczbą graczy (odświeżanie co 30s).")
    async def prpg_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _query_ase_full)

        embed = self._build_status_embed(info)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Odwiedź Forum",
            style=discord.ButtonStyle.link,
            url=FORUM_URL,
            emoji="🌐"
        ))

        msg = await interaction.followup.send(embed=embed, view=view, wait=True)

        self._status_messages[interaction.guild_id] = msg
        if not self._refresh_status.is_running():
            self._refresh_status.start()

    @prpg_group.command(name="gracze", description="Wyświetla interaktywną listę wszystkich graczy aktualnie połączonych z serwerem ProjectRPG.")
    async def prpg_gracze(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _query_ase_full)

        if not info['online']:
            await interaction.followup.send(embed=error_embed("Serwer Niedostępny", "Nie udało się połączyć z serwerem ProjectRPG."))
            return

        players = info.get('player_list', [])
        if not players:
            embed = info_embed(
                "👥 Gracze Online — ProjectRPG",
                f"Serwer jest online (`{info['players']}` graczy), lecz lista nazwisk jest obecnie przetwarzana przez protokół ASE."
            )
            await interaction.followup.send(embed=embed)
            return

        paginator = PlayerListPaginator(
            players=players,
            total_online=info['players'],
            max_players=info['max_players'],
            ping_ms=info['ping_ms'],
            user_id=interaction.user.id
        )
        await interaction.followup.send(embed=paginator.build_embed(), view=paginator)

    @prpg_group.command(name="szukaj", description="Sprawdza w czasie rzeczywistym, czy dany gracz jest online na serwerze ProjectRPG.")
    @app_commands.describe(nick="Nick lub fragment nicku gracza, którego szukasz")
    async def prpg_szukaj(self, interaction: discord.Interaction, nick: str) -> None:
        await interaction.response.defer()

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _query_ase_full)

        if not info['online']:
            await interaction.followup.send(embed=error_embed("Błąd Połączenia", "Serwer ProjectRPG nie odpowiada na zapytania ASE."))
            return

        players = info.get('player_list', [])
        query = nick.lower().strip()
        matches = [p for p in players if query in p.lower()]

        if matches:
            embed = success_embed(
                f"🔎 Wyniki Wyszukiwania Gracza: `{nick}`",
                f"Znaleziono **{len(matches)}** pasujących graczy online na serwerze ProjectRPG!\n\n"
                f"📶 **Status:** `🟢 W GRZE (ONLINE)`  •  ⏱️ **Ping:** `{info['ping_ms']} ms`"
            )
            formatted = "\n".join([f"• **{m}**" for m in matches[:15]])
            if len(matches) > 15:
                formatted += f"\n*...oraz {len(matches) - 15} innych graczy.*"
            embed.add_field(name="👤 Dopasowane Nicki", value=formatted, inline=False)
            embed.add_field(name="📍 Serwer", value=f"`{SERVER_IP}:{SERVER_PORT}`", inline=True)
            embed.add_field(name="👥 Łącznie Graczy", value=f"`{info['players']} / {info['max_players']}`", inline=True)
        else:
            embed = error_embed(
                f"🔎 Wyniki Wyszukiwania: `{nick}`",
                f"Gracz o nicku zawierającym **'{nick}'** nie został odnaleziony na serwerze.\n\n"
                f"🔴 **Status:** `OFFLINE` lub błędny nick.\n"
                f"👥 Aktualnie online jest `{info['players']}` graczy. Użyj `/prpg gracze`, aby przejrzeć pełną listę."
            )
        embed.set_footer(text=f"ProjectRPG Live ASE Search • Czas zapytania: {info['ping_ms']} ms")
        await interaction.followup.send(embed=embed)

    @prpg_group.command(name="zarobki", description="Oficjalne zarobki prac ProjectRPG z forum (Zaktualizowane na 2026 rok).")
    @app_commands.describe(
        praca="Wybierz pracę dorywczą (lub zostaw puste dla interaktywnego panelu)",
        ulepszenie="Poziom ulepszenia pracy",
        konto_premium="Aktywne Konto Premium (+20% gotówki)",
        mnoznik="Aktywny mnożnik serwerowy (Weekend / Event / Happy Hours)"
    )
    @app_commands.choices(praca=[
        app_commands.Choice(name="Kurier RedEx / TransMax (Couriero/Speedvan/Carrier)", value="kurier"),
        app_commands.Choice(name="Koszenie Trawy / Kosiarki (300 RP • do 8,200 $)", value="kosiarki"),
        app_commands.Choice(name="Drwal (Ścinanie do 10,000 $ • Pielęgnacja 700 RP)", value="drwal"),
        app_commands.Choice(name="Kutry Rybackie (1,500 RP • do 8,500 $)", value="kutry"),
        app_commands.Choice(name="Lawety / Transport Kontenerów (Spinybed LV • do 10,100 $)", value="lawety"),
        app_commands.Choice(name="Dodo — Gaszenie Pożarów (1,000 RP • do 9,000 $)", value="dodo"),
        app_commands.Choice(name="Górnik / Kopalnia (4,000 RP • do 10,000 $ + Diamenty)", value="gornik"),
        app_commands.Choice(name="Aplikacja Transportowa (Taxi / Bolt • APK PRO do 12,000 $)", value="taxi"),
        app_commands.Choice(name="Szambiarz (800 RP • do 8,000 $)", value="szambiarz"),
        app_commands.Choice(name="Grzybiarz (0 RP • do 7,000 $)", value="grzybiarz"),
        app_commands.Choice(name="Magazynier (Bayside • 0 RP • Najwięcej EXP 600/h)", value="magazynier"),
        app_commands.Choice(name="Sprzątanie Ulic / Sweepery (0 RP • do 6,300 $)", value="sweepery"),
        app_commands.Choice(name="Wędkarz (36 $ za 1 kg ryb)", value="wedkarz"),
    ])
    @app_commands.choices(ulepszenie=[
        app_commands.Choice(name="0 LVL (Brak ulepszeń / Start — 1.00x)", value="tier1"),
        app_commands.Choice(name="Poziom 2 (Ulepszenie Podstawowe)", value="tier2"),
        app_commands.Choice(name="Poziom 3 (Ulepszenie Zaawansowane)", value="tier3"),
        app_commands.Choice(name="MAX LVL (Wymaksowany poziom pracy)", value="tier4"),
    ])
    @app_commands.choices(konto_premium=[
        app_commands.Choice(name="Konto Premium (+20% gotówki — Domyślnie)", value="tak"),
        app_commands.Choice(name="Brak (Konto Zwykłe — 1.00x)", value="nie"),
    ])
    @app_commands.choices(mnoznik=[
        app_commands.Choice(name="Standardowy (1.00x)", value="1.0"),
        app_commands.Choice(name="Happy Hours (+15% — 1.15x)", value="1.15"),
        app_commands.Choice(name="Weekend Bonus (+30% — 1.30x)", value="1.30"),
        app_commands.Choice(name="Event Specjalny (+50% — 1.50x)", value="1.50"),
        app_commands.Choice(name="Mega Event (2.00x Kasa)", value="2.00"),
    ])
    async def prpg_zarobki(
        self,
        interaction: discord.Interaction,
        praca: Optional[str] = "kurier",
        ulepszenie: Optional[str] = "tier1",
        konto_premium: Optional[str] = "tak",
        mnoznik: Optional[str] = "1.0"
    ) -> None:
        selected_job = praca or "kurier"
        selected_tier = ulepszenie or "tier1"
        is_premium = (konto_premium != "nie")
        server_mult = float(mnoznik or "1.0")

        embed = _build_job_embed(selected_job, selected_tier, is_premium, server_mult)
        view = JobDashboardView(
            current_job=selected_job,
            current_tier=selected_tier,
            premium=is_premium,
            server_mult=server_mult,
            user_id=interaction.user.id
        )
        await interaction.response.send_message(embed=embed, view=view)

    @prpg_group.command(name="tuning", description="Kalkulator kosztów i zestawów tuningu aut na ProjectRPG.")
    @app_commands.describe(kategoria="Wybierz kategorię komponentów tuningu")
    @app_commands.choices(kategoria=[
        app_commands.Choice(name="Zestawy Prowadzenia (Torowy / Wyścigowy / Drift)", value="zestawy"),
        app_commands.Choice(name="Mechanika (ECU V4, Turbo V5, Silnik)", value="mechanika"),
        app_commands.Choice(name="Zawieszenie i Napęd (GZ, Hydraulika, AWD)", value="zawieszenie"),
        app_commands.Choice(name="Wizualny & Optyczny (Szyby, Lampy, Licznik)", value="wizualny"),
    ])
    async def prpg_tuning(self, interaction: discord.Interaction, kategoria: str) -> None:
        if kategoria == "zestawy":
            embed = info_embed(
                "🏎️ Zestawy Komponentów Prowadzenia (Tuning)",
                "**Zestaw Torowy (T):**\n"
                "• Szacowany koszt: `~120,000 PLN`\n"
                "• Zastosowanie: Maksymalna przyczepność na zakrętach, idealny do wyścigów ulicznych i prób czasowych.\n\n"
                "**Zestaw Wyścigowy (W):**\n"
                "• Szacowany koszt: `~95,000 PLN`\n"
                "• Zastosowanie: Zwiększona prędkość maksymalna i przyspieszenie na prostych odcinkach.\n\n"
                "**Zestaw Drift (D):**\n"
                "• Szacowany koszt: `~80,000 PLN`\n"
                "• Zastosowanie: Zmniejszona przyczepność tylnej osi, perfekcyjne wchodzenie w poślizg kontrolowany."
            )
        elif kategoria == "mechanika":
            embed = info_embed(
                "⚙️ Tuning Mechaniczny Silnika (ProjectRPG)",
                "• **ECU V4:** `~45,000 PLN` — Zwiększa moc, obroty oraz umożliwia wgrywanie programów diagnostycznych.\n"
                "• **Turbo V5:** `~60,000 PLN` (do 2.0 Bar) — Potężny skok przyspieszenia i prędkości maksymalnej.\n"
                "• **Zestaw Nitro (Butla N5):** `~50,000 PLN` (Naprawialne / Napełniane).\n"
                "• **Ulepszenie pojemności silnika (np. 1.6 ➔ 2.2 dm³):** od `~30,000 PLN` do `~180,000 PLN` w zależności od bazy pojazdu."
            )
        elif kategoria == "zawieszenie":
            embed = info_embed(
                "🔩 Zawieszenie i Układ Napędowy",
                "• **Zawieszenie Gwintowane (GZ):** `~25,000 PLN` — Umożliwia regulację wysokości pojazdu (gleba).\n"
                "• **Hydraulika:** `~22,000 PLN` — Skaczące zawieszenie sterowane klawiszami.\n"
                "• **Moduł Zmiany Napędu (AWD / RWD / FWD):** `~35,000 PLN`.\n"
                "• **Kąt Skrętu Kół:** `~15,000 PLN` — Zwiększa promień skrętu (niezbędne do driftu).\n"
                "• **Poszerzenie Osi (Poziom 1/2):** `~12,000 - 24,000 PLN`."
            )
        else:
            embed = info_embed(
                "🎨 Tuning Wizualny i Dodatki",
                "• **Przyciemnienie szyb (100% / 80% / 60% / 20%):** `~18,000 - 35,000 PLN`\n"
                "• **Niestandardowe Lampy (Kolor):** `~40,000 - 75,000 PLN`\n"
                "• **Podświetlenie Licznika:** `~15,000 PLN`\n"
                "• **Aplikacja Audio (Radio / Tuba Basowa):** `~8,000 PLN`\n"
                "• **Wykrywacz Fotoradarów & Ogranicznik:** `~6,000 PLN`"
            )

        embed.set_footer(text="Ceny orientacyjne oparte na cenniku warsztatów ProjectRPG")
        await interaction.response.send_message(embed=embed)

    @prpg_group.command(name="taryfikator", description="Wyszukiwarka kar, mandatów i punktów karnych frakcji SAPD.")
    @app_commands.describe(wykroczenie="Wybierz kategorię wykroczenia")
    @app_commands.choices(wykroczenie=[
        app_commands.Choice(name="Przekroczenie prędkości", value="predkosc"),
        app_commands.Choice(name="Ucieczka przed policją (Pościg)", value="poscig"),
        app_commands.Choice(name="Jazda pod prąd / Taranowanie", value="taran"),
        app_commands.Choice(name="Posiadanie nielegalnych substancji", value="narkotyki"),
        app_commands.Choice(name="Odmowa przyjęcia mandatu", value="odmowa"),
    ])
    async def prpg_taryfikator(self, interaction: discord.Interaction, wykroczenie: str) -> None:
        if wykroczenie == "predkosc":
            embed = create_embed(
                title="🚓 Taryfikator SAPD: Przekroczenie Prędkości",
                description="**Teren Zabudowany (Limit 80 km/h):**\n"
                            "• Przekroczenie o 10-30 km/h: `500 PLN` | 2 pkt\n"
                            "• Przekroczenie o 31-50 km/h: `1,200 PLN` | 4 pkt\n"
                            "• Przekroczenie o 50+ km/h: `2,500 PLN` | 6 pkt + ewentualne zatrzymanie PJ\n\n"
                            "**Autostrada (Limit 120-140 km/h):**\n"
                            "• Przekroczenie o 20-40 km/h: `800 PLN` | 2 pkt\n"
                            "• Przekroczenie o 40+ km/h: `1,800 PLN` | 4 pkt",
                color=config.ERROR_COLOR
            )
        elif wykroczenie == "poscig":
            embed = create_embed(
                title="🚨 Taryfikator SAPD: Ucieczka przed Frakcją (Pościg)",
                description="• **Rozpoczęcie pościgu (Brak zatrzymania do kontroli):** `30-60 minut aresztu` lub mandat `5,000 - 15,000 PLN`\n"
                            "• **Taranowanie radiowozów podczas pościgu:** +`15 minut aresztu` za każdy taran\n"
                            "• **Wjazd do strefy DM podczas pościgu:** Natychmiastowe otwarcie ognia (KOD 3) + konfiskata pojazdu na Parking Policyjny (`5,000 PLN` wykup).",
                color=config.ERROR_COLOR
            )
        elif wykroczenie == "taran":
            embed = create_embed(
                title="🚗 Taryfikator SAPD: Kolizje i Jazda pod prąd",
                description="• **Jazda pod prąd autostradą / drogą szybkiego ruchu:** `1,500 PLN` | 4 pkt\n"
                            "• **Niezachowanie ostrożności i spowodowanie kolizji:** `1,000 PLN` + pokrycie szkód poszkodowanego\n"
                            "• **Celowy taran (DM pojazdem):** Zgłoszenie do administracji serwera (Zabranie Prawa Jazdy 1-3 dni).",
                color=config.ERROR_COLOR
            )
        elif wykroczenie == "narkotyki":
            embed = create_embed(
                title="🌿 Taryfikator SAPD: Nielegalne Substancje i Przedmioty",
                description="• **Posiadanie marihuany / kokainy:** `20-40 minut aresztu` (w zależności od gramatury) + konfiskata\n"
                            "• **Nielegalna broń palna bez licencji:** `45 minut aresztu` + konfiskata broni\n"
                            "• **Posiadanie wytrychów / narzędzi do włamań:** Mandat `2,500 PLN`.",
                color=config.ERROR_COLOR
            )
        else:
            embed = create_embed(
                title="⚖️ Odmowa Przyjęcia Mandatu",
                description="W przypadku odmowy uiszczenia nałożonej kary grzywny przez funkcjonariusza SAPD:\n\n"
                            "• **Przelicznik aresztu:** Każde `500 PLN` mandatu = `5 minut więzienia` (maksymalnie do 90 minut).\n"
                            "• **Pojazd kierowcy:** Zostaje odholowany na Parking Policyjny na koszt właściciela.",
                color=config.ERROR_COLOR
            )

        embed.set_footer(text="Oficjalny Taryfikator San Andreas Police Department (SAPD)")
        await interaction.response.send_message(embed=embed)

    @prpg_group.command(name="apelacja", description="Generuje gotowy wzór apelacji od kary na forum ProjectRPG.")
    @app_commands.describe(typ="Wybierz rodzaj otrzymanej kary")
    @app_commands.choices(typ=[
        app_commands.Choice(name="Prawo Jazdy (ZPJ)", value="prawko"),
        app_commands.Choice(name="Wyciszenie (Mute)", value="mute"),
        app_commands.Choice(name="Ban na serwer", value="ban"),
        app_commands.Choice(name="Blokada Broni (ZPB)", value="bron"),
    ])
    async def prpg_apelacja(self, interaction: discord.Interaction, typ: str) -> None:
        wzory = {
            "prawko": (
                "🚗 Wzór Apelacji: Zabranie Prawa Jazdy (ZPJ)",
                "```md\n"
                "Tytuł wątku: Apelacja od zabrania prawa jazdy - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Nick: <Twój aktualny nick>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <np. 2 dni>\n"
                "Licencja: <A / B / C>\n"
                "Powód: <Powód z kary>\n"
                "Wyjaśnienie: <Dokładne wyjaśnienie sytuacji>\n"
                "Coś od siebie: <Przeprosiny i obietnica poprawy>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
            "mute": (
                "🔇 Wzór Apelacji: Wyciszenie Czatu (Mute)",
                "```md\n"
                "Tytuł wątku: Apelacja od wyciszenia - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Nick: <Twój aktualny nick>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <np. 24h>\n"
                "Powód: <Powód z kary>\n"
                "Wyjaśnienie: <Dlaczego doszło do przewinienia>\n"
                "Coś od siebie: <Przeprosiny i deklaracja kultury wypowiedzi>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
            "ban": (
                "⛔ Wzór Apelacji: Blokada Konta (Ban)",
                "```md\n"
                "Tytuł wątku: Apelacja od bana - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Serial: <Twój Serial MTA z F8>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <Permanentna / Liczba dni>\n"
                "Powód: <Powód bana>\n"
                "Wyjaśnienie: <Szczegółowe wyjaśnienie sprawy>\n"
                "Coś od siebie: <Uzasadnienie dlaczego powinieneś otrzymać unbana>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
            "bron": (
                "🔫 Wzór Apelacji: Blokada Broni (ZPB)",
                "```md\n"
                "Tytuł wątku: Apelacja od blokady broni - <Twój_Nick> ukarany przez <Nick_Członka_Administracji>\n\n"
                "Treść wątku:\n"
                "UID: <Twój UID z gry>\n"
                "Login: <Twój login>\n"
                "Nick: <Twój aktualny nick>\n"
                "Data nadania kary: <Data i godzina>\n"
                "Długość kary: <np. 3 dni>\n"
                "Powód: <Powód kary>\n"
                "Wyjaśnienie: <Wyjaśnienie okoliczności użycia broni>\n"
                "Coś od siebie: <Przeprosiny>\n"
                "```\n\n"
                f"🔗 **Link do działu:** [Kliknij tutaj, aby złożyć apelację]({FORUM_URL}/forum/38-apelacje-od-kar/)"
            ),
        }

        title, content = wzory.get(typ, ("Wzór Apelacji", "Brak wzoru"))
        embed = info_embed(title, f"Skopiuj poniższy wzór, uzupełnij pola i wklej na oficjalnym forum:\n\n{content}")
        await interaction.response.send_message(embed=embed)

    @prpg_group.command(name="frakcje", description="Informacje o frakcjach serwerowych i zarobkach na ProjectRPG.")
    async def prpg_frakcje(self, interaction: discord.Interaction) -> None:
        embed = info_embed(
            "🏢 Frakcje Serwerowe na ProjectRPG",
            "Przegląd głównych służb mundurowych i korporacji na serwerze:\n\n"
            "🚓 **San Andreas Police Department (SAPD):**\n"
            "• Rola: Ochrona porządku, pościgi, kontrole drogowe, obsługa stref DM.\n"
            "• Wymagania: Wiek 15+, sprawny mikrofon, brak kar w ostatnim czasie, znajomość KRD.\n\n"
            "🚑 **San Andreas Medical Department (SAMD):**\n"
            "• Rola: Ratownictwo medyczne, reanimacje, obsługa wypadków (RP medyczne).\n"
            "• Wymagania: Wysoka kultura osobista, podstawowa wiedza z pierwszej pomocy.\n\n"
            "🚒 **San Andreas Fire Department (SAFD):**\n"
            "• Rola: Gaszenie pożarów, ratownictwo techniczne, usuwanie plam oleju i wiatrołomów.\n\n"
            "🚕 **San Andreas Transport Corporation (SATC):**\n"
            "• Rola: Przewóz osób taksówkami, autobusami i limuzynami po całym stanie.\n\n"
            "🔧 **San Andreas Road Assistance (SARA):**\n"
            "• Rola: Naprawa infrastruktury drogowej, odholowywanie porzuconych aut."
        )
        embed.set_footer(text="Rekrutacje ogłaszane są cyklicznie w dziale Frakcji na forum")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectRPGCog(bot))
