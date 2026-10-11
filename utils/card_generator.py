"""
Moduł generatora obrazów PNG w bibliotece Pillow.
Tworzy estetyczne karty rang XP, bannery powitalne oraz wyzwania graficzne CAPTCHA.
"""

import io
import math
import random
from datetime import datetime
from typing import Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def _create_gradient(width: int, height: int, start_color: Tuple[int, int, int], end_color: Tuple[int, int, int]) -> Image.Image:
    """Tworzy płynne tło gradientowe dwukolorowe."""
    base = Image.new("RGBA", (width, height), start_color + (255,))
    top = Image.new("RGBA", (width, height), end_color + (255,))
    mask = Image.new("L", (width, height))
    mask_data = []
    for y in range(height):
        for x in range(width):
            # Przekątny gradient
            ratio = (x / width + y / height) / 2
            mask_data.append(int(255 * ratio))
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)
    return base

def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Próbuje załadować czcionkę systemową lub domyślną."""
    font_names = [
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeui.ttf",
        "DejaVuSans.ttf",
        "Roboto-Regular.ttf"
    ]
    for name in font_names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()

def create_rank_card(
    username: str,
    avatar_bytes: bytes | None,
    level: int,
    current_xp: int,
    needed_xp: int,
    total_xp: int,
    rank_pos: int
) -> io.BytesIO:
    """
    Generuje estetyczną kartę rangi w stylu Dark Glassmorphism.
    Zwraca io.BytesIO zawierający plik PNG.
    """
    W, H = 850, 260
    # Tworzenie tła gradientowego (Blurple -> Deep Navy)
    img = _create_gradient(W, H, (15, 23, 42), (30, 41, 59))
    draw = ImageDraw.Draw(img)

    # Efekt szkła (Glassmorphism overlay container)
    glass = Image.new("RGBA", (W - 40, H - 40), (255, 255, 255, 12))
    img.paste(glass, (20, 20), glass)

    # Rysowanie zaokrąglonej ramki szkła
    draw.rounded_rectangle([20, 20, W - 20, H - 20], radius=16, outline=(88, 101, 242, 100), width=2)

    # Awatar
    avatar_size = 160
    av_x, av_y = 45, 50

    if avatar_bytes:
        try:
            av_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            av_img = av_img.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
        except Exception:
            av_img = Image.new("RGBA", (avatar_size, avatar_size), (88, 101, 242, 255))
    else:
        av_img = Image.new("RGBA", (avatar_size, avatar_size), (88, 101, 242, 255))

    # Maska okręgu dla awatara
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)
    img.paste(av_img, (av_x, av_y), mask)

    # Otoczka awatara
    draw.ellipse([av_x - 3, av_y - 3, av_x + avatar_size + 3, av_y + avatar_size + 3], outline=(88, 101, 242, 255), width=3)

    # Tekst: Nazwa użytkownika
    font_name = _get_font(32, bold=True)
    font_sub = _get_font(20)
    font_stats = _get_font(18, bold=True)

    # Przewiń nazwę jeśli za długa
    display_name = username[:18] + "..." if len(username) > 18 else username
    draw.text((230, 50), display_name, font=font_name, fill=(240, 244, 255))

    # Pozycja i Poziom
    rank_str = f"RANG #{rank_pos}"
    level_str = f"POZIOM {level}"

    draw.text((W - 250, 50), rank_str, font=font_stats, fill=(87, 242, 135))
    draw.text((W - 130, 48), level_str, font=_get_font(22, bold=True), fill=(88, 101, 242))

    # Wskaźnik XP
    percent = min(1.0, max(0.0, current_xp / needed_xp)) if needed_xp > 0 else 1.0
    xp_text = f"{current_xp:,} / {needed_xp:,} XP".replace(",", " ")
    draw.text((230, 115), xp_text, font=font_sub, fill=(148, 163, 184))

    # Pasek postępu
    bar_x1, bar_y1 = 230, 155
    bar_x2, bar_y2 = W - 50, 185
    bar_w = bar_x2 - bar_x1

    # Tło paska
    draw.rounded_rectangle([bar_x1, bar_y1, bar_x2, bar_y2], radius=15, fill=(30, 41, 59, 200), outline=(51, 65, 85, 255), width=1)

    # Wypełnienie paska
    fill_w = int(bar_w * percent)
    if fill_w > 20:
        draw.rounded_rectangle([bar_x1, bar_y1, bar_x1 + fill_w, bar_y2], radius=15, fill=(88, 101, 242, 255))
    elif fill_w > 0:
        draw.rectangle([bar_x1, bar_y1, bar_x1 + fill_w, bar_y2], fill=(88, 101, 242, 255))

    # Dodatkowe podsumowanie sumaryczne
    total_text = f"Łącznie: {total_xp:,} XP".replace(",", " ")
    draw.text((W - 220, 120), total_text, font=_get_font(15), fill=(100, 116, 139))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def _create_futuristic_background(
    width: int,
    height: int,
    start_color: Tuple[int, int, int],
    end_color: Tuple[int, int, int],
    glow_color: Tuple[int, int, int],
    glow_pos: Tuple[int, int]
) -> Image.Image:
    """Tworzy głębokie gradientowe tło z efektem radialnej poświaty i subtelnej siatki technologicznej."""
    base = Image.new("RGBA", (width, height), start_color + (255,))
    top = Image.new("RGBA", (width, height), end_color + (255,))
    mask = Image.new("L", (width, height))
    mask_data = []
    for y in range(height):
        for x in range(width):
            ratio = (x / width * 0.7 + y / height * 0.3)
            mask_data.append(int(255 * ratio))
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)

    # Efekt radialnego glow za awatarem
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    gx, gy = glow_pos
    max_radius = 260
    for r in range(max_radius, 20, -12):
        alpha = int(50 * (1.0 - (r / max_radius) ** 1.3))
        glow_draw.ellipse([gx - r, gy - r, gx + r, gy + r], fill=glow_color + (alpha,))

    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(24))
    base.alpha_composite(glow_layer)

    # Subtelna siatka techniczna w tle
    grid_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid_layer)
    for x in range(0, width, 36):
        grid_draw.line([(x, 0), (x, height)], fill=(255, 255, 255, 5), width=1)
    for y in range(0, height, 36):
        grid_draw.line([(0, y), (width, y)], fill=(255, 255, 255, 5), width=1)
    base.alpha_composite(grid_layer)

    return base

def _render_avatar_with_rings(
    avatar_bytes: Optional[bytes],
    size: int,
    ring_color_outer: Tuple[int, int, int],
    ring_color_inner: Tuple[int, int, int],
    default_bg: Tuple[int, int, int],
    default_initial: str = "?"
) -> Image.Image:
    """Tworzy wyrenderowany komponent awatara w supersamplingu z antyaliasingiem i podwójnym pierścieniem."""
    scale = 2
    full_w = (size + 24) * scale
    center = full_w // 2
    av_radius = (size // 2) * scale

    canvas = Image.new("RGBA", (full_w, full_w), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # 1. Zewnętrzny miękki pierścień
    outer_r = av_radius + 9 * scale
    draw.ellipse(
        [center - outer_r, center - outer_r, center + outer_r, center + outer_r],
        outline=ring_color_outer + (100,),
        width=3 * scale
    )

    # 2. Wewnętrzny jaskrawy pierścień
    inner_r = av_radius + 4 * scale
    draw.ellipse(
        [center - inner_r, center - inner_r, center + inner_r, center + inner_r],
        outline=ring_color_inner + (240,),
        width=3 * scale
    )

    # 3. Awatar użytkownika
    if avatar_bytes:
        try:
            raw_av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            raw_av = raw_av.resize((av_radius * 2, av_radius * 2), Image.Resampling.LANCZOS)
        except Exception:
            raw_av = None
    else:
        raw_av = None

    if raw_av is None:
        raw_av = Image.new("RGBA", (av_radius * 2, av_radius * 2), default_bg + (255,))
        d_raw = ImageDraw.Draw(raw_av)
        font_init = _get_font(int(av_radius * 0.95), bold=True)
        init_txt = default_initial.upper()[:1]
        bbox = d_raw.textbbox((0, 0), init_txt, font=font_init)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        d_raw.text(((av_radius * 2 - tw) // 2, (av_radius * 2 - th) // 2 - 8 * scale), init_txt, font=font_init, fill=(255, 255, 255, 250))

    mask = Image.new("L", (av_radius * 2, av_radius * 2), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.ellipse((0, 0, av_radius * 2 - 1, av_radius * 2 - 1), fill=255)

    av_clipped = Image.new("RGBA", (av_radius * 2, av_radius * 2), (0, 0, 0, 0))
    av_clipped.paste(raw_av, (0, 0), mask)

    canvas.paste(av_clipped, (center - av_radius, center - av_radius), av_clipped)

    return canvas.resize((size + 24, size + 24), Image.Resampling.LANCZOS)

def create_welcome_card(
    username: str,
    server_name: str,
    avatar_bytes: bytes | None,
    member_count: int
) -> io.BytesIO:
    """Generuje nowoczesną kartę powitalną (Dark Glassmorphism + Emerald/Cyan Glow)."""
    W, H = 880, 310

    start_c = (9, 14, 26)
    end_c = (15, 23, 42)
    glow_c = (16, 185, 129)
    accent_emerald = (52, 211, 153)
    accent_cyan = (56, 189, 248)

    img = _create_futuristic_background(W, H, start_c, end_c, glow_c, (140, 155))
    draw = ImageDraw.Draw(img)

    # Szklany kontener
    glass = Image.new("RGBA", (W - 36, H - 36), (255, 255, 255, 7))
    img.alpha_composite(glass, (18, 18))

    draw.rounded_rectangle([18, 18, W - 18, H - 18], radius=22, outline=(56, 189, 248, 60), width=2)
    draw.line([(36, 18), (86, 18)], fill=accent_emerald + (230,), width=3)
    draw.line([(18, 36), (18, 86)], fill=accent_emerald + (230,), width=3)

    # Awatar
    av_base_size = 160
    av_comp = _render_avatar_with_rings(
        avatar_bytes=avatar_bytes,
        size=av_base_size,
        ring_color_outer=accent_emerald,
        ring_color_inner=accent_cyan,
        default_bg=(37, 99, 235),
        default_initial=username[:1] if username else "?"
    )
    av_pos_x = 42
    av_pos_y = (H - (av_base_size + 24)) // 2
    img.alpha_composite(av_comp, (av_pos_x, av_pos_y))

    # Prawa strona
    text_x = av_pos_x + av_base_size + 42

    # 1. Górny Pill Badge: NOWY CZŁONEK
    pill_w = 215
    pill_h = 32
    pill_y = 44
    draw.rounded_rectangle(
        [text_x, pill_y, text_x + pill_w, pill_y + pill_h],
        radius=16,
        fill=(16, 185, 129, 30),
        outline=accent_emerald + (160,),
        width=1
    )
    diamond_cx, diamond_cy = text_x + 18, pill_y + 16
    draw.polygon([
        (diamond_cx, diamond_cy - 5),
        (diamond_cx + 5, diamond_cy),
        (diamond_cx, diamond_cy + 5),
        (diamond_cx - 5, diamond_cy)
    ], fill=accent_emerald)

    badge_font = _get_font(13, bold=True)
    draw.text((text_x + 30, pill_y + 7), "NOWY CZŁONEK SERWERA", font=badge_font, fill=accent_emerald)

    # 2. Nazwa użytkownika
    display_name = username
    if len(display_name) > 16:
        name_font = _get_font(28, bold=True)
        if len(display_name) > 22:
            display_name = display_name[:20] + "..."
    else:
        name_font = _get_font(34, bold=True)
    draw.text((text_x, 88), display_name, font=name_font, fill=(248, 250, 252))

    # 3. Podtytuł
    sub_font = _get_font(17)
    clean_server = server_name[:26] + ("..." if len(server_name) > 26 else "")
    draw.text((text_x, 140), f"Witaj w społeczności {clean_server}!", font=sub_font, fill=(148, 163, 184))

    # 4. Dolne statystyki
    stats_y = 194
    # Boks 1: Licznik członków
    m_pill_w = 175
    m_pill_h = 44
    draw.rounded_rectangle([text_x, stats_y, text_x + m_pill_w, stats_y + m_pill_h], radius=12, fill=(24, 34, 53, 220), outline=(51, 65, 85, 220), width=1)

    cnt_lbl_font = _get_font(11, bold=True)
    draw.text((text_x + 14, stats_y + 5), "POZYCJA CZŁONKA", font=cnt_lbl_font, fill=(148, 163, 184))
    draw.text((text_x + 14, stats_y + 20), f"#{member_count} użytkownik", font=_get_font(16, bold=True), fill=accent_cyan)

    # Boks 2: Czas dołączenia
    d_pill_x = text_x + m_pill_w + 14
    d_pill_w = 185
    draw.rounded_rectangle([d_pill_x, stats_y, d_pill_x + d_pill_w, stats_y + m_pill_h], radius=12, fill=(24, 34, 53, 220), outline=(51, 65, 85, 220), width=1)
    draw.text((d_pill_x + 14, stats_y + 5), "CZAS DOŁĄCZENIA", font=cnt_lbl_font, fill=(148, 163, 184))
    now_str = datetime.now().strftime("%d.%m.%Y • %H:%M")
    draw.text((d_pill_x + 14, stats_y + 20), now_str, font=_get_font(15, bold=True), fill=(241, 245, 249))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def create_goodbye_card(
    username: str,
    server_name: str,
    avatar_bytes: bytes | None,
    member_count: int,
    stay_days: int | None = None
) -> io.BytesIO:
    """Generuje nowoczesną kartę pożegnalną (Dark Glassmorphism + Rose/Coral Glow)."""
    W, H = 880, 310

    start_c = (20, 10, 16)
    end_c = (30, 17, 24)
    glow_c = (225, 29, 72)
    accent_rose = (251, 113, 133)
    accent_coral = (251, 146, 60)

    img = _create_futuristic_background(W, H, start_c, end_c, glow_c, (140, 155))
    draw = ImageDraw.Draw(img)

    # Szklany kontener
    glass = Image.new("RGBA", (W - 36, H - 36), (255, 255, 255, 6))
    img.alpha_composite(glass, (18, 18))

    draw.rounded_rectangle([18, 18, W - 18, H - 18], radius=22, outline=(244, 63, 94, 60), width=2)
    draw.line([(36, 18), (86, 18)], fill=accent_rose + (230,), width=3)
    draw.line([(18, 36), (18, 86)], fill=accent_rose + (230,), width=3)

    # Awatar
    av_base_size = 160
    av_comp = _render_avatar_with_rings(
        avatar_bytes=avatar_bytes,
        size=av_base_size,
        ring_color_outer=accent_rose,
        ring_color_inner=accent_coral,
        default_bg=(225, 29, 72),
        default_initial=username[:1] if username else "?"
    )
    av_pos_x = 42
    av_pos_y = (H - (av_base_size + 24)) // 2
    img.alpha_composite(av_comp, (av_pos_x, av_pos_y))

    # Prawa strona
    text_x = av_pos_x + av_base_size + 42

    # 1. Górny Pill Badge: DO ZOBACZENIA
    pill_w = 175
    pill_h = 32
    pill_y = 44
    draw.rounded_rectangle(
        [text_x, pill_y, text_x + pill_w, pill_y + pill_h],
        radius=16,
        fill=(239, 68, 68, 30),
        outline=accent_rose + (160,),
        width=1
    )
    diamond_cx, diamond_cy = text_x + 18, pill_y + 16
    draw.polygon([
        (diamond_cx, diamond_cy - 5),
        (diamond_cx + 5, diamond_cy),
        (diamond_cx, diamond_cy + 5),
        (diamond_cx - 5, diamond_cy)
    ], fill=accent_rose)

    badge_font = _get_font(13, bold=True)
    draw.text((text_x + 30, pill_y + 7), "DO ZOBACZENIA!", font=badge_font, fill=accent_rose)

    # 2. Nazwa użytkownika
    display_name = username
    if len(display_name) > 16:
        name_font = _get_font(28, bold=True)
        if len(display_name) > 22:
            display_name = display_name[:20] + "..."
    else:
        name_font = _get_font(34, bold=True)
    draw.text((text_x, 88), display_name, font=name_font, fill=(248, 250, 252))

    # 3. Podtytuł
    sub_font = _get_font(17)
    draw.text((text_x, 140), "Dziękujemy za wspólny czas na serwerze.", font=sub_font, fill=(148, 163, 184))

    # 4. Dolne statystyki
    stats_y = 194
    # Boks 1: Pozostało członków
    m_pill_w = 175
    m_pill_h = 44
    draw.rounded_rectangle([text_x, stats_y, text_x + m_pill_w, stats_y + m_pill_h], radius=12, fill=(45, 23, 32, 220), outline=(85, 35, 45, 220), width=1)

    cnt_lbl_font = _get_font(11, bold=True)
    draw.text((text_x + 14, stats_y + 5), "POZOSTAŁO CZŁONKÓW", font=cnt_lbl_font, fill=(148, 163, 184))
    draw.text((text_x + 14, stats_y + 20), f"{member_count} osób", font=_get_font(16, bold=True), fill=accent_rose)

    # Boks 2: Staż
    d_pill_x = text_x + m_pill_w + 14
    d_pill_w = 185
    draw.rounded_rectangle([d_pill_x, stats_y, d_pill_x + d_pill_w, stats_y + m_pill_h], radius=12, fill=(45, 23, 32, 220), outline=(85, 35, 45, 220), width=1)
    draw.text((d_pill_x + 14, stats_y + 5), "STAŻ NA SERWERZE", font=cnt_lbl_font, fill=(148, 163, 184))

    if stay_days is not None:
        if stay_days == 0:
            stay_text = "Mniej niż 1 dzień"
        elif stay_days == 1:
            stay_text = "1 dzień"
        else:
            stay_text = f"{stay_days} dni"
    else:
        stay_text = "Brak danych"

    draw.text((d_pill_x + 14, stats_y + 20), stay_text, font=_get_font(15, bold=True), fill=(241, 245, 249))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def create_captcha_image(code: str) -> io.BytesIO:
    """Generuje obrazkowe wyzwanie CAPTCHA ze zniekształceniami i liniami szumu."""
    W, H = 280, 90
    img = Image.new("RGB", (W, H), (24, 28, 40))
    draw = ImageDraw.Draw(img)

    # Linie szumu
    for _ in range(12):
        x1, y1 = random.randint(0, W), random.randint(0, H)
        x2, y2 = random.randint(0, W), random.randint(0, H)
        color = (random.randint(50, 180), random.randint(50, 180), random.randint(180, 255))
        draw.line([x1, y1, x2, y2], fill=color, width=random.randint(1, 2))

    # Punkty szumu
    for _ in range(300):
        draw.point((random.randint(0, W), random.randint(0, H)), fill=(random.randint(100, 255), random.randint(100, 255), random.randint(100, 255)))

    # Rysowanie znaków kodu ze zniekształceniami
    font = _get_font(38, bold=True)
    char_x = 25
    for char in code:
        # Tworzenie pojedynczego obrazka dla litery
        char_img = Image.new("RGBA", (50, 60), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((10, 5), char, font=font, fill=(random.randint(180, 255), random.randint(180, 255), random.randint(220, 255), 255))

        # Obrót litery o losowy kąt
        angle = random.randint(-25, 25)
        char_img = char_img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)

        img.paste(char_img, (char_x, 15), char_img)
        char_x += 40

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
