"""
Moduł renderowania graficznego kart floty pojazdów Team77 (Pillow).
Generuje wysokiej jakości grafikę 2-kolumnową z ciemnym motywem Discorda,
identyczną jak w podglądzie HTML/CSS.
"""

from __future__ import annotations

import io
import os
from typing import Any
from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Ładuje najlepszy dostępny font systemowy z fallbackiem do domyślnego."""
    candidates = [
        # Linux (Debian / Ubuntu / Docker / Pterodactyl)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        # Relative / fallback
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


def generate_fleet_cards_image(page_items: list[dict[str, Any]]) -> io.BytesIO:
    """
    Renderuje obrazek PNG o wymiarach 860x360 zawierający do 6 pojazdów
    w estetycznym układzie 2 kolumn x 3 wiersze z zaokrąglonymi kafelkami.
    """
    width = 860
    # Oblicz wysokość w zależności od liczby aut (zwykle 6 = 3 wiersze)
    num_rows = max(1, (len(page_items) + 1) // 2)
    row_h = 96
    gap_y = 12
    margin_y = 12
    height = margin_y * 2 + num_rows * row_h + (num_rows - 1) * gap_y

    # Tło Discord #2b2d31 (ciemne, pasujące do embeda)
    img = Image.new("RGBA", (width, height), (43, 45, 49, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(15, bold=True)
    font_id = _load_font(12, bold=True)
    font_meta = _load_font(12, bold=False)
    font_bold = _load_font(12, bold=True)
    font_tag = _load_font(11, bold=True)

    col_w = 406
    gap_x = 16
    margin_x = 16

    for idx, v in enumerate(page_items[:6]):
        col = idx % 2
        row = idx // 2
        x = margin_x + col * (col_w + gap_x)
        y = margin_y + row * (row_h + gap_y)

        # Ciemny kafelek pojazdu (#1e1f22)
        draw.rounded_rectangle(
            [x, y, x + col_w, y + row_h],
            radius=8,
            fill=(30, 31, 34, 255),
            outline=(255, 255, 255, 18),
            width=1,
        )

        # Pomarańczowy pasek akcentowy Team77 z lewej strony
        draw.rounded_rectangle(
            [x, y + 4, x + 3, y + row_h - 4],
            radius=2,
            fill=(243, 156, 18, 255),
        )

        # Nazwa modelu
        model_name = str(v.get("model", "Pojazd"))[:26]
        draw.text((x + 16, y + 12), model_name, fill=(242, 243, 245), font=font_title)

        # Badge VUID po prawej stronie na górze
        vuid_str = f"#{v.get('id', '')}"
        badge_w = max(56, len(vuid_str) * 9 + 12)
        badge_x = x + col_w - badge_w - 14
        badge_y = y + 10
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + 20],
            radius=4,
            fill=(243, 156, 18, 35),
            outline=(243, 156, 18, 90),
            width=1,
        )
        draw.text((badge_x + 8, badge_y + 3), vuid_str, fill=(243, 156, 18), font=font_id)

        # Właściciel
        owner = str(v.get("owner", "—"))[:22]
        draw.text((x + 16, y + 42), "Wlasciciel:", fill=(148, 155, 164), font=font_meta)
        draw.text((x + 92, y + 42), owner, fill=(220, 222, 225), font=font_bold)

        # Silnik i rodzaj paliwa
        draw.text((x + 16, y + 66), "Silnik:", fill=(148, 155, 164), font=font_meta)

        fuel = str(v.get("fuel", "Benzyna"))
        cap = str(v.get("capacity", "")).strip()
        fuel_str = f"{fuel} ({cap})" if cap else fuel

        is_gas = "benz" in fuel.lower()
        tag_bg = (243, 156, 18, 30) if is_gas else (52, 152, 219, 30)
        tag_border = (243, 156, 18, 70) if is_gas else (52, 152, 219, 70)
        tag_color = (243, 156, 18) if is_gas else (52, 152, 219)

        tag_w = max(110, len(fuel_str) * 7 + 16)
        draw.rounded_rectangle(
            [x + 92, y + 63, x + 92 + tag_w, y + 83],
            radius=4,
            fill=tag_bg,
            outline=tag_border,
            width=1,
        )
        draw.text((x + 98, y + 66), fuel_str, fill=tag_color, font=font_tag)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
