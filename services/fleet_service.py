"""
Serwis integracji z bazą pojazdów forum ProjectRPG (https://projectrpg.pl/vehicles/).
Pobiera flotę pojazdów organizacji Team77 bezpośrednio ze strony serwera,
obsługuje sesję użytkownika, inteligentne cache'owanie oraz szczegółowe informacje o autach.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

import cloudscraper

import config

logger = logging.getLogger("AegisBot.FleetService")

CACHE_TTL_SECONDS = 15 * 60  # 15 minut pamięci podręcznej


def _clean_text(html_text: str) -> str:
    """Oczyszcza fragment HTML ze znaczników i encji."""
    text = (
        html_text.replace("&#179;", "³")
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


class FleetService:
    """Serwis asynchroniczny do pobierania i przetwarzania floty Team77."""

    def __init__(self) -> None:
        self._cached_fleet: list[dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._vehicle_details_cache: dict[int, tuple[dict[str, Any], float]] = {}

    def get_cookies(self) -> dict[str, str]:
        """Zwraca słownik ciasteczek sesyjnych do autoryzacji na forum."""
        cookies = {}
        if config.PRPG_FORUM_MEMBER_ID:
            cookies["ips4_member_id"] = config.PRPG_FORUM_MEMBER_ID
        if config.PRPG_FORUM_LOGIN_KEY:
            cookies["ips4_login_key"] = config.PRPG_FORUM_LOGIN_KEY
        if config.PRPG_FORUM_DEVICE_KEY:
            cookies["ips4_device_key"] = config.PRPG_FORUM_DEVICE_KEY
        if config.PRPG_FORUM_SESSION:
            cookies["ips4_IPSSessionFront"] = config.PRPG_FORUM_SESSION
        return cookies

    def _sync_fetch_fleet(self, organization: str = "Team77") -> list[dict[str, Any]]:
        """
        Synchroniczne zapytanie pobierające wszystkie strony floty danej organizacji.
        Wywoływane w osobnym wątku (loop.run_in_executor).
        """
        cookies = self.get_cookies()
        if not cookies.get("ips4_member_id") or not cookies.get("ips4_login_key"):
            logger.warning("Brak skonfigurowanych ciasteczek forum PRPG_FORUM_* w .env!")
            return []

        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )

        # 1. Pobierz formularz zaawansowany, aby wyciągnąć świeży csrfKey
        adv_url = "https://projectrpg.pl/vehicles/?sortby=id&sortdirection=asc&advancedSearchForm=1"
        try:
            r_adv = scraper.get(adv_url, cookies=cookies, timeout=20)
        except Exception as e:
            logger.exception("Błąd podczas pobierania formularza /vehicles/: %s", e)
            return []

        if r_adv.status_code != 200:
            logger.warning("Nie udało się pobrać formularza pojazdów (status=%s)", r_adv.status_code)
            return []

        csrf_m = re.search(r'name=[\'"]csrfKey[\'"]\s+value=[\'"]([^\'"]+)[\'"]', r_adv.text)
        csrf_val = csrf_m.group(1) if csrf_m else ""

        # 2. Inicjalizuj filtr POST dla organizacji
        post_data = {
            "advanced_search_submitted": "1",
            "csrfKey": csrf_val,
            "sortby": "id",
            "sortdirection": "asc",
            "projectrpg_vehicles_membership": organization,
        }

        try:
            r_post = scraper.post(
                "https://projectrpg.pl/vehicles/?sortby=id&sortdirection=asc",
                data=post_data,
                cookies=cookies,
                timeout=20,
            )
        except Exception as e:
            logger.exception("Błąd zapytania POST /vehicles/: %s", e)
            return []

        all_vehicles: list[dict[str, Any]] = []

        def _parse_page(html: str) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
            for r in rows:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.DOTALL | re.IGNORECASE)
                cleaned = [_clean_text(c) for c in cells]
                if len(cleaned) >= 4 and cleaned[0].isdigit():
                    vuid = int(cleaned[0])
                    raw_name = cleaned[1]
                    owner = cleaned[2]
                    membership = cleaned[3]

                    # Rozdzielenie silnika np. "Moonbeam High Top (418) Diesel (1.6dm³)"
                    engine_m = re.search(r"(Diesel|Benzyna)\s*\(([^)]+)\)", raw_name, re.IGNORECASE)
                    fuel = engine_m.group(1) if engine_m else "Nieznane"
                    capacity = engine_m.group(2) if engine_m else ""

                    # Czysta nazwa modelu i ID modelu MTA
                    model_part = re.sub(r"(Diesel|Benzyna)\s*\([^)]+\)", "", raw_name).strip()
                    mta_id_m = re.search(r"\((\d{3})\)", model_part)
                    mta_model_id = int(mta_id_m.group(1)) if mta_id_m else None
                    model_name = re.sub(r"\(\d{3}\)", "", model_part).strip()

                    link_m = re.search(r'href=[\'"]([^\'"]*vehicles[^\'"]*\d+[^\'"]*)[\'"]', r)
                    detail_url = link_m.group(1) if link_m else f"https://projectrpg.pl/vehicles/{vuid}/"

                    items.append({
                        "id": vuid,
                        "model": model_name,
                        "raw_name": raw_name,
                        "mta_id": mta_model_id,
                        "fuel": fuel,
                        "capacity": capacity,
                        "owner": owner,
                        "membership": membership,
                        "detail_url": detail_url,
                    })
            return items

        first_page_items = _parse_page(r_post.text)
        all_vehicles.extend(first_page_items)

        # 3. Pobierz kolejne podstrony (page=2, 3...)
        page = 2
        while page <= 15:
            paged_url = (
                f"https://projectrpg.pl/vehicles/?sortby=id&sortdirection=asc"
                f"&advanced_search_submitted=1&csrfKey={csrf_val}"
                f"&projectrpg_vehicles_model=&projectrpg_vehicles_nick="
                f"&projectrpg_vehicles_membership={organization}&page={page}"
            )
            try:
                r_page = scraper.get(paged_url, cookies=cookies, timeout=20)
                if r_page.status_code != 200:
                    break
                p_items = _parse_page(r_page.text)
                if not p_items:
                    break
                # Zabezpieczenie przed zapętleniem
                if any(x["id"] in [v["id"] for v in all_vehicles] for x in p_items):
                    break
                all_vehicles.extend(p_items)
                if len(p_items) < 15:
                    break
                page += 1
            except Exception as e:
                logger.warning("Błąd podczas pobierania strony %s floty: %s", page, e)
                break

        logger.info("Pomyślnie zescrapowano flotę %s: %s pojazdów.", organization, len(all_vehicles))
        return all_vehicles

    async def get_fleet(self, organization: str = "Team77", force_refresh: bool = False) -> list[dict[str, Any]]:
        """Pobiera listę floty z cache lub bezpośrednio ze strony forum."""
        now = time.monotonic()
        if not force_refresh and self._cached_fleet and (now - self._last_fetch_time < CACHE_TTL_SECONDS):
            return self._cached_fleet

        loop = asyncio.get_running_loop()
        vehicles = await loop.run_in_executor(None, self._sync_fetch_fleet, organization)
        if vehicles:
            self._cached_fleet = vehicles
            self._last_fetch_time = now
        return self._cached_fleet or vehicles

    def _sync_fetch_vehicle_detail(self, vuid: int) -> dict[str, Any] | None:
        """Pobiera synchronicznie stronę szczegółów danego pojazdu."""
        cookies = self.get_cookies()
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        url = f"https://projectrpg.pl/vehicles/{vuid}/"
        try:
            resp = scraper.get(url, cookies=cookies, timeout=15)
            if resp.status_code != 200:
                return None
            html = resp.text
        except Exception:
            logger.exception("Błąd pobierania szczegółów pojazdu #%s", vuid)
            return None

        data: dict[str, Any] = {
            "id": vuid,
            "url": url,
            "model": "Nieznany",
            "server_model_id": None,
            "engine": "—",
            "mileage": "—",
            "tech_review": "—",
            "plate": "—",
            "last_driver": "—",
            "membership": "—",
            "visual_tuning": "Brak",
            "mechanical_tuning": "Brak",
            "lamp_color": None,
            "recent_drivers": [],
        }

        # Wyciąganie wartości z par strong -> div.ipsDataItem_generic
        pairs = re.findall(
            r"<strong[^>]*>(.*?)</strong>\s*</span>\s*<div class=[\'\"]ipsDataItem_generic[\'\"]>(.*?)</div>\s*</li>",
            html,
            re.DOTALL | re.IGNORECASE,
        )
        fields = {}
        for lbl, val in pairs:
            fields[_clean_text(lbl).lower()] = _clean_text(val)

        data["model"] = fields.get("model") or data["model"]
        data["server_model_id"] = fields.get("model na serwerze")
        data["engine"] = fields.get("silnik") or data["engine"]
        data["mileage"] = fields.get("przebieg") or data["mileage"]
        data["tech_review"] = fields.get("przegląd techniczny") or fields.get("przeglad techniczny") or data["tech_review"]
        data["plate"] = fields.get("tablica") or data["plate"]
        data["last_driver"] = fields.get("ostatni kierowca") or data["last_driver"]
        data["membership"] = fields.get("przynależność") or fields.get("przynaleznosc") or data["membership"]
        data["visual_tuning"] = fields.get("tuning wizualny") or "Brak"
        data["mechanical_tuning"] = fields.get("tuning mechaniczny") or "Brak"
        data["owner"] = fields.get("właściciel") or fields.get("wlasciciel") or "—"

        # Kolor świateł (z tooltipu)
        lamp_m = re.search(r'title=[\'"]([^\'"]+)[\'"][^>]*data-ipsTooltip[^>]*style=[\'"][^\'"]*background:', html)
        if lamp_m:
            data["lamp_color"] = lamp_m.group(1)

        # Ostatni kierowcy (z datą i godziną z zakładki kierowców)
        recent: list[dict[str, str]] = []
        lists = re.findall(r"<ul class=[\'\"]ipsDataList ipsDataList_reducedSpacing[\'\"][^>]*>(.*?)</ul>", html, re.DOTALL)
        driver_ul = lists[1] if len(lists) >= 2 else (lists[0] if lists else "")
        if driver_ul:
            li_items = re.findall(r"<li class=[\'\"]ipsDataItem ipsType_break[\'\"]>(.*?)</li>", driver_ul, re.DOTALL)
            for li in li_items[:5]:
                strong_m = re.search(r"<strong>(.*?)</strong>", li, re.DOTALL)
                raw_nick = strong_m.group(1) if strong_m else ""
                nick = re.sub(r"<[^>]+>", "", raw_nick).strip().replace("\u2605", "*").replace("\u2606", "*")

                time_m = re.search(r"<time datetime=[\'\"][^\'\"]+[\'\"] title=[\'\"]([^\'\"]+)[\'\"]", li)
                t_clean = time_m.group(1).strip() if time_m else ""
                m_dt = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}:\d{2})", t_clean)
                if m_dt:
                    y, mo, d, hm = m_dt.groups()
                    date_formatted = f"{d}.{mo}.{y} {hm}"
                else:
                    date_formatted = t_clean

                if nick and nick.lower() not in ("vuid", "model", "silnik", "przebieg", "tablica"):
                    recent.append({
                        "nick": nick,
                        "time": date_formatted,
                    })

        data["recent_drivers"] = recent
        return data

    async def get_vehicle_detail(self, vuid: int, force: bool = False, owner: str | None = None) -> dict[str, Any] | None:
        """Pobiera szczegóły pojazdu (z pamięci podręcznej lub z forum)."""
        now = time.monotonic()
        if not force and vuid in self._vehicle_details_cache:
            entry, ts = self._vehicle_details_cache[vuid]
            if now - ts < CACHE_TTL_SECONDS:
                if owner and entry.get("owner") in (None, "", "—"):
                    entry["owner"] = owner
                return entry

        loop = asyncio.get_running_loop()
        detail = await loop.run_in_executor(None, self._sync_fetch_vehicle_detail, vuid)
        if detail:
            if owner:
                detail["owner"] = owner
            elif detail.get("owner") in (None, "", "—") and self._cached_fleet:
                for v in self._cached_fleet:
                    if v.get("id") == vuid and v.get("owner"):
                        detail["owner"] = v["owner"]
                        break
            self._vehicle_details_cache[vuid] = (detail, now)
        return detail
