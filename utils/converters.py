"""
Konwertery typów danych dla komend slash i przetwarzania danych z bazy.
"""

import re
import discord

COLOR_HEX_REGEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

def parse_color(color_str: str) -> discord.Color | None:
    """
    Konwertuje kod szesnastkowy koloru (np. '#5865F2' lub '5865F2') na obiekt discord.Color.
    """
    match = COLOR_HEX_REGEX.match(color_str.strip())
    if not match:
        return None
    hex_val = match.group(1)
    return discord.Color(int(hex_val, 16))

def clean_mentions(text: str) -> str:
    """
    Usuwa niepożądane wzmianki @everyone oraz @here zapobiegając nadużyciom.
    """
    return text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
