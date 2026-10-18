"""
Moduł renderowania graficznego kart floty pojazdów Team77 (Pillow).
Generuje wielkie, szerokie banery 1-kolumnowe (900x600 px, 5 aut na stronę),
dzięki czemu w Discordzie karty są OGROMNE, a tekst maksymalnie czytelny.
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


def generate_fleet_cards_image(page_items: list[dict[str, Any]]) -> io.BytesIO:
    """
    Renderuje wielkie, pełnoekranowe banery kart pojazdów (1 kolumna).
    Wymiary: 900x(wysokość zależna od liczby aut, do 600px).
    """
    width = 900
    card_w = 860
    card_h = 104
    gap_y = 12
    margin_x = 20
    margin_y = 14

    num_items = max(1, len(page_items[:5]))
    height = margin_y * 2 + num_items * card_h + max(0, num_items - 1) * gap_y

    # Ciemne tło Discord #26282c
    img = Image.new("RGBA", (width, height), (38, 40, 44, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(25, bold=True)
    font_id = _load_font(20, bold=True)
    font_owner = _load_font(17, bold=True)
    font_meta = _load_font(17, bold=False)
    font_fuel = _load_font(16, bold=True)

    for idx, v in enumerate(page_items[:5]):
        x = margin_x
        y = margin_y + idx * (card_h + gap_y)

        # Ciemny kafelek z zaokrąglonymi rogami i wyraźną ramką
        draw.rounded_rectangle(
            [x, y, x + card_w, y + card_h],
            radius=12,
            fill=(26, 27, 30, 255),
            outline=(255, 255, 255, 30),
            width=2,
        )

        # Złoty/pomarańczowy pasek akcentowy z lewej strony
        draw.rounded_rectangle([x, y + 6, x + 8, y + card_h - 6], radius=4, fill=(243, 156, 18, 255))

        # Wielka nazwa modelu
        model = str(v.get("model", "Pojazd"))[:32]
        draw.text((x + 24, y + 16), model, fill=(255, 255, 255), font=font_title)

        # Wielki złoty badge VUID na górze po prawej
        vuid = f"#{v.get('id', '')}"
        badge_w = max(100, len(vuid) * 14 + 20)
        badge_h = 34
        draw.rounded_rectangle(
            [x + card_w - badge_w - 20, y + 15, x + card_w - 20, y + 15 + badge_h],
            radius=7,
            fill=(243, 156, 18, 45),
            outline=(243, 156, 18, 150),
            width=2,
        )
        draw.text((x + card_w - badge_w - 8, y + 20), vuid, fill=(243, 156, 18), font=font_id)

        # Właściciel
        owner = str(v.get("owner", "—")).replace("★", "*").replace("☆", "*")[:24]
        draw.text((x + 24, y + 60), "Właściciel:", fill=(155, 162, 172), font=font_meta)
        draw.text((x + 125, y + 60), owner, fill=(235, 238, 242), font=font_owner)

        # Plakietka paliwa i silnika po prawej stronie na dole
        fuel = str(v.get("fuel", "Benzyna"))
        cap = str(v.get("capacity", "")).strip()
        fuel_str = f"{fuel} ({cap})" if cap else fuel
        is_gas = "benz" in fuel.lower()

        tag_bg = (243, 156, 18, 40) if is_gas else (52, 152, 219, 40)
        tag_border = (243, 156, 18, 110) if is_gas else (52, 152, 219, 110)
        tag_color = (243, 156, 18) if is_gas else (52, 152, 219)

        tag_w = max(150, len(fuel_str) * 10 + 22)
        tag_h = 30
        tag_x = x + card_w - tag_w - 20
        tag_y = y + 58

        draw.rounded_rectangle(
            [tag_x, tag_y, tag_x + tag_w, tag_y + tag_h],
            radius=6,
            fill=tag_bg,
            outline=tag_border,
            width=2,
        )
        draw.text((tag_x + 12, tag_y + 4), fuel_str, fill=tag_color, font=font_fuel)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
