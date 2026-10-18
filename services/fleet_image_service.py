"""
Moduł renderowania graficznego kart floty pojazdów Team77 (Pillow).
Generuje wielkie, szerokie banery (4 auta na stronę, wysokość 664px),
z czcionkami 28px i dużymi badge'ami, idealnie czytelnymi w aplikacji Discord.
"""

from __future__ import annotations

import io
import os
from typing import Any
from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Ładuje najlepszy dostępny font systemowy z fallbackiem do domyślnego."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled_font = os.path.join(base_dir, "assets", "fonts", "arialbd.ttf" if bold else "arial.ttf")

    candidates = [
        # Dedykowane fonty w projekcie (zawsze dostępne w repo)
        bundled_font,
        "assets/fonts/arialbd.ttf" if bold else "assets/fonts/arial.ttf",
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
    Renderuje wielkie, pełnoekranowe banery kart pojazdów (4 auta na stronę).
    Wymiary: 800x664 px, kafelki 760x145 px, czcionka tytułu 28px.
    """
    width = 800
    card_w = 760
    card_h = 145
    gap_y = 16
    margin_x = 20
    margin_y = 18

    num_items = max(1, len(page_items[:4]))
    height = margin_y * 2 + num_items * card_h + max(0, num_items - 1) * gap_y

    # Ciemne tło Discord #26282c
    img = Image.new("RGBA", (width, height), (38, 40, 44, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(28, bold=True)
    font_id = _load_font(22, bold=True)
    font_owner = _load_font(20, bold=True)
    font_meta = _load_font(19, bold=False)
    font_fuel = _load_font(18, bold=True)

    for idx, v in enumerate(page_items[:4]):
        x = margin_x
        y = margin_y + idx * (card_h + gap_y)

        # Ciemny kafelek z zaokrąglonymi rogami i wyraźną ramką
        draw.rounded_rectangle(
            [x, y, x + card_w, y + card_h],
            radius=14,
            fill=(26, 27, 30, 255),
            outline=(255, 255, 255, 35),
            width=2,
        )

        # Złoty pasek akcentowy Team77 z lewej strony
        draw.rounded_rectangle([x, y + 8, x + 9, y + card_h - 8], radius=4, fill=(243, 156, 18, 255))

        # Wielka nazwa modelu (28px)
        model = str(v.get("model", "Pojazd"))[:26]
        draw.text((x + 28, y + 22), model, fill=(255, 255, 255), font=font_title)

        # Wielki złoty badge VUID (40px wysokości)
        vuid = f"#{v.get('id', '')}"
        badge_w = max(115, len(vuid) * 16 + 24)
        badge_h = 40
        badge_x = x + card_w - badge_w - 24
        badge_y = y + 20
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=8,
            fill=(243, 156, 18, 50),
            outline=(243, 156, 18, 170),
            width=2,
        )
        draw.text((badge_x + 14, badge_y + 6), vuid, fill=(243, 156, 18), font=font_id)

        # Linia podziału
        draw.line([x + 28, y + 76, x + card_w - 28, y + 76], fill=(255, 255, 255, 20), width=1)

        # Właściciel
        owner = str(v.get("owner", "—")).replace("★", "*").replace("☆", "*")[:24]
        draw.text((x + 28, y + 94), "Właściciel:", fill=(155, 162, 172), font=font_meta)
        draw.text((x + 140, y + 94), owner, fill=(240, 242, 246), font=font_owner)

        # Plakietka paliwa i silnika
        fuel = str(v.get("fuel", "Benzyna"))
        cap = str(v.get("capacity", "")).strip()
        fuel_str = f"{fuel} ({cap})" if cap else fuel
        is_gas = "benz" in fuel.lower()

        tag_bg = (243, 156, 18, 45) if is_gas else (52, 152, 219, 45)
        tag_border = (243, 156, 18, 130) if is_gas else (52, 152, 219, 130)
        tag_color = (243, 156, 18) if is_gas else (52, 152, 219)

        tag_w = max(170, len(fuel_str) * 12 + 24)
        tag_h = 36
        tag_x = x + card_w - tag_w - 24
        tag_y = y + 88

        draw.rounded_rectangle(
            [tag_x, tag_y, tag_x + tag_w, tag_y + tag_h],
            radius=8,
            fill=tag_bg,
            outline=tag_border,
            width=2,
        )
        draw.text((tag_x + 14, tag_y + 6), fuel_str, fill=tag_color, font=font_fuel)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
