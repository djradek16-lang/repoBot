"""
Moduł renderowania graficznego kart floty pojazdów Team77 (Pillow).
Generuje dużą, wyraźną grafikę 2-kolumnową w wysokiej rozdzielczości (1200px)
z ciemnym motywem Discorda, w 100% zgodną ze studiem podglądu.
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
    """Zwraca krótki tag kategorii dla modelu."""
    m = model_name.lower()
    if any(x in m for x in ("nrg", "fcr", "pcj", "bf-400", "sanchez", "freeway", "wayfarer")):
        return "MOTO"
    if any(x in m for x in ("dinghy", "speeder", "marquis", "jetmax", "tropic", "coastguard")):
        return "ŁÓDŹ"
    if any(x in m for x in ("rancher", "huntley", "landstalker", "patriot", "mesa", "sandking", "yosemite")):
        return "4x4 / SUV"
    if any(x in m for x in ("benson", "mule", "yankee", "boxville", "rumpo", "burrito", "pony", "camper")):
        return "DOSTAWCZY"
    if any(x in m for x in ("sultan", "buffalo", "jester", "elegy", "euros", "cheetah", "bullet", "turismo", "infernus", "flash", "comet", "zr-350", "banshee", "sabre", "alpha", "zenith", "fortune")):
        return "SPORT"
    return "OSOBOWY"


def generate_fleet_cards_image(page_items: list[dict[str, Any]]) -> io.BytesIO:
    """
    Renderuje duży obrazek PNG o szerokości 1200px z dużymi, czytelnymi
    kartami w układzie 2 kolumn x 3 wiersze.
    """
    width = 1200
    num_rows = max(1, (len(page_items) + 1) // 2)
    row_h = 138
    gap_y = 16
    margin_y = 16
    height = margin_y * 2 + num_rows * row_h + (num_rows - 1) * gap_y

    # Tło Discord #2b2d31 (ciemne)
    img = Image.new("RGBA", (width, height), (43, 45, 49, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(21, bold=True)
    font_id = _load_font(16, bold=True)
    font_meta = _load_font(15, bold=False)
    font_bold = _load_font(15, bold=True)
    font_tag = _load_font(14, bold=True)
    font_cat = _load_font(11, bold=True)

    col_w = 572
    gap_x = 24
    margin_x = 16

    for idx, v in enumerate(page_items[:6]):
        col = idx % 2
        row = idx // 2
        x = margin_x + col * (col_w + gap_x)
        y = margin_y + row * (row_h + gap_y)

        # Ciemny kafelek pojazdu (#1e1f22) z zaokrąglonymi rogami
        draw.rounded_rectangle(
            [x, y, x + col_w, y + row_h],
            radius=12,
            fill=(30, 31, 34, 255),
            outline=(255, 255, 255, 22),
            width=2,
        )

        # Pomarańczowy pasek akcentowy Team77 z lewej strony
        draw.rounded_rectangle(
            [x, y + 6, x + 5, y + row_h - 6],
            radius=3,
            fill=(243, 156, 18, 255),
        )

        # Nazwa modelu
        model_name = str(v.get("model", "Pojazd"))[:24]
        draw.text((x + 20, y + 16), model_name, fill=(245, 246, 248), font=font_title)

        # Kategoria tag tuż za nazwą
        cat_tag = _get_category_tag(model_name)
        bbox = draw.textbbox((x + 20, y + 16), model_name, font=font_title)
        cat_x = bbox[2] + 12
        cat_y = y + 19
        cat_w = len(cat_tag) * 7 + 12
        draw.rounded_rectangle(
            [cat_x, cat_y, cat_x + cat_w, cat_y + 18],
            radius=3,
            fill=(255, 255, 255, 15),
            outline=(255, 255, 255, 30),
            width=1,
        )
        draw.text((cat_x + 6, cat_y + 2), cat_tag, fill=(185, 190, 198), font=font_cat)

        # Złoty Badge VUID po prawej stronie na górze
        vuid_str = f"#{v.get('id', '')}"
        badge_w = max(76, len(vuid_str) * 11 + 16)
        badge_h = 28
        badge_x = x + col_w - badge_w - 18
        badge_y = y + 14
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=6,
            fill=(243, 156, 18, 40),
            outline=(243, 156, 18, 120),
            width=2,
        )
        draw.text((badge_x + 10, badge_y + 4), vuid_str, fill=(243, 156, 18), font=font_id)

        # Właściciel (oczyszczanie ze znaków specjalnych niedostępnych w standardowych fontach)
        raw_owner = str(v.get("owner", "—")).replace("★", "*").replace("☆", "*")[:24]
        draw.text((x + 20, y + 58), "Właściciel:", fill=(155, 162, 172), font=font_meta)
        draw.text((x + 115, y + 58), raw_owner, fill=(230, 232, 236), font=font_bold)

        # Silnik i rodzaj paliwa
        draw.text((x + 20, y + 92), "Silnik:", fill=(155, 162, 172), font=font_meta)

        fuel = str(v.get("fuel", "Benzyna"))
        cap = str(v.get("capacity", "")).strip()
        fuel_str = f"{fuel} ({cap})" if cap else fuel

        is_gas = "benz" in fuel.lower()
        tag_bg = (243, 156, 18, 35) if is_gas else (52, 152, 219, 35)
        tag_border = (243, 156, 18, 90) if is_gas else (52, 152, 219, 90)
        tag_color = (243, 156, 18) if is_gas else (52, 152, 219)

        tag_w = max(140, len(fuel_str) * 9 + 20)
        tag_h = 28
        draw.rounded_rectangle(
            [x + 115, y + 88, x + 115 + tag_w, y + 88 + tag_h],
            radius=6,
            fill=tag_bg,
            outline=tag_border,
            width=2,
        )
        draw.text((x + 125, y + 92), fuel_str, fill=tag_color, font=font_tag)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
