"""
Moduł renderowania graficznego kart floty pojazdów Team77 (Pillow).
Generuje dużą, wyraźną grafikę w proporcjach (800x630), która w Discordzie
wypełnia cały embed na wysokość i szerokość, zapewniając maksymalną czytelność.
"""

from __future__ import annotations

import io
import os
from typing import Any
from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Ładuje najlepszy dostępny font systemowy z fallbackiem do domyślnego."""
    candidates = [
        # Linux (Debian / Ubuntu / Docker / GameHost)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        # Fallbacks
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arial.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _get_category_tag(model_name: str) -> str:
    """Zwraca czytelny tag kategorii dla danego modelu."""
    m = model_name.lower()
    if any(x in m for x in ("nrg", "fcr", "pcj", "bf-400", "sanchez", "freeway", "wayfarer")):
        return "JEDNOŚLAD / MOTO"
    if any(x in m for x in ("dinghy", "speeder", "marquis", "jetmax", "tropic", "coastguard")):
        return "ŁÓDŹ MOTOROWA"
    if any(x in m for x in ("rancher", "huntley", "landstalker", "patriot", "mesa", "sandking", "yosemite")):
        return "TERENOWY / 4x4"
    if any(x in m for x in ("benson", "mule", "yankee", "boxville", "rumpo", "burrito", "pony", "camper")):
        return "DOSTAWCZY / UTILITY"
    if any(x in m for x in ("sultan", "buffalo", "jester", "elegy", "euros", "cheetah", "bullet", "turismo", "infernus", "flash", "comet", "zr-350", "banshee", "sabre", "alpha", "zenith", "fortune")):
        return "SPORTOWY / COUPE"
    return "OSOBOWY / SEDAN"


def generate_fleet_cards_image(page_items: list[dict[str, Any]]) -> io.BytesIO:
    """
    Renderuje duży obrazek PNG o proporcjach 800x630 (aspect ratio 1.27:1),
    dzięki czemu w Discordzie nie jest ściśnięty w wąski pasek, lecz
    wyświetla się w pełnym, ogromnym i czytelnym rozmiarze.
    """
    width = 800
    col_w = 380
    row_h = 190
    gap_x = 16
    gap_y = 16
    margin_x = 12
    margin_y = 14

    num_rows = max(1, (len(page_items) + 1) // 2)
    height = margin_y * 2 + num_rows * row_h + max(0, num_rows - 1) * gap_y

    # Tło Discord #2b2d31
    img = Image.new("RGBA", (width, height), (38, 40, 44, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(21, bold=True)
    font_id = _load_font(18, bold=True)
    font_cat = _load_font(12, bold=False)
    font_label = _load_font(15, bold=False)
    font_val = _load_font(15, bold=True)
    font_badge = _load_font(14, bold=True)

    for idx, v in enumerate(page_items[:6]):
        col = idx % 2
        row = idx // 2
        x = margin_x + col * (col_w + gap_x)
        y = margin_y + row * (row_h + gap_y)

        # Ciemny, duży kafelek pojazdu
        draw.rounded_rectangle(
            [x, y, x + col_w, y + row_h],
            radius=12,
            fill=(26, 27, 30, 255),
            outline=(255, 255, 255, 28),
            width=2,
        )

        # Pomarańczowy pasek akcentowy Team77 z lewej strony
        draw.rounded_rectangle(
            [x, y + 6, x + 6, y + row_h - 6],
            radius=3,
            fill=(243, 156, 18, 255),
        )

        # Nazwa modelu pojazdu
        model_name = str(v.get("model", "Pojazd"))[:22]
        draw.text((x + 18, y + 16), model_name, fill=(255, 255, 255), font=font_title)

        # Podtytuł z kategorią
        cat = _get_category_tag(model_name)
        draw.text((x + 18, y + 46), f"Kategoria: {cat}", fill=(130, 136, 145), font=font_cat)

        # Złoty badge VUID na górze po prawej
        vuid = f"#{v.get('id', '')}"
        badge_w = max(80, len(vuid) * 12 + 18)
        draw.rounded_rectangle(
            [x + col_w - badge_w - 16, y + 14, x + col_w - 16, y + 46],
            radius=6,
            fill=(243, 156, 18, 45),
            outline=(243, 156, 18, 140),
            width=2,
        )
        draw.text((x + col_w - badge_w - 6, y + 19), vuid, fill=(243, 156, 18), font=font_id)

        # Linia podziału wewnątrz kafelka
        draw.line([x + 18, y + 74, x + col_w - 18, y + 74], fill=(255, 255, 255, 18), width=1)

        # Właściciel
        owner = str(v.get("owner", "—")).replace("★", "*").replace("☆", "*")[:20]
        draw.text((x + 18, y + 90), "Właściciel:", fill=(155, 162, 172), font=font_label)
        draw.text((x + 115, y + 90), owner, fill=(240, 242, 245), font=font_val)

        # Silnik i rodzaj paliwa
        fuel = str(v.get("fuel", "Benzyna"))
        cap = str(v.get("capacity", "")).strip()
        fuel_str = f"{fuel} ({cap})" if cap else fuel
        is_gas = "benz" in fuel.lower()

        draw.text((x + 18, y + 126), "Silnik:", fill=(155, 162, 172), font=font_label)

        tag_bg = (243, 156, 18, 40) if is_gas else (52, 152, 219, 40)
        tag_border = (243, 156, 18, 110) if is_gas else (52, 152, 219, 110)
        tag_color = (243, 156, 18) if is_gas else (52, 152, 219)
        tag_w = max(140, len(fuel_str) * 9 + 22)

        draw.rounded_rectangle(
            [x + 115, y + 122, x + 115 + tag_w, y + 154],
            radius=6,
            fill=tag_bg,
            outline=tag_border,
            width=2,
        )
        draw.text((x + 126, y + 128), fuel_str, fill=tag_color, font=font_badge)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
