"""
Pomocnicze fabryki embedów zapewniające spójny wygląd i motyw wizualny bota.
"""

from typing import Any
import discord
from datetime import datetime
import config

def create_embed(
    title: str,
    description: str | None = None,
    color: int = config.DEFAULT_EMBED_COLOR,
    fields: list[dict[str, Any]] | None = None,
    footer_text: str | None = config.FOOTER_TEXT,
    footer_icon_url: str | None = None,
    thumbnail_url: str | None = None,
    image_url: str | None = None,
    url: str | None = None,
    timestamp: bool = True,
    author_name: str | None = None,
    author_icon_url: str | None = None,
    author_url: str | None = None
) -> discord.Embed:
    """Tworzy bazowy obiekt discord.Embed z zachowaniem domyślnych standardów motywu."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        url=url,
        timestamp=datetime.now() if timestamp else None
    )

    if author_name:
        embed.set_author(
            name=author_name,
            icon_url=author_icon_url or None,
            url=author_url or None
        )

    if fields:
        for field in fields:
            embed.add_field(
                name=field.get("name", "\u200b"),
                value=field.get("value", "\u200b"),
                inline=field.get("inline", False)
            )

    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_icon_url or config.FOOTER_ICON_URL or None)

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    if image_url:
        embed.set_image(url=image_url)

    return embed

def success_embed(title: str, description: str) -> discord.Embed:
    """Zwraca embed sygnalizujący sukces operacji (kolor zielony)."""
    return create_embed(
        title=f"✅ {title}",
        description=description,
        color=config.SUCCESS_COLOR
    )

def error_embed(title: str, description: str) -> discord.Embed:
    """Zwraca embed sygnalizujący błąd (kolor czerwony)."""
    return create_embed(
        title=f"❌ {title}",
        description=description,
        color=config.ERROR_COLOR
    )

def warning_embed(title: str, description: str) -> discord.Embed:
    """Zwraca embed ostrzegawczy (kolor żółty)."""
    return create_embed(
        title=f"⚠️ {title}",
        description=description,
        color=config.WARNING_COLOR
    )

def info_embed(title: str, description: str) -> discord.Embed:
    """Zwraca embed informacyjny (kolor niebieski)."""
    return create_embed(
        title=f"ℹ️ {title}",
        description=description,
        color=config.INFO_COLOR
    )

def moderation_embed(
    action: str,
    target: discord.User | discord.Member,
    moderator: discord.User | discord.Member,
    reason: str,
    case_id: int | None = None,
    duration: str | None = None
) -> discord.Embed:
    """Zwraca dedykowany embed akcji moderacyjnej."""
    fields = [
        {"name": "Użytkownik", "value": f"{target.mention} (`{target.id}`)", "inline": True},
        {"name": "Moderator", "value": f"{moderator.mention} (`{moderator.id}`)", "inline": True},
        {"name": "Powód", "value": reason, "inline": False}
    ]

    if duration:
        fields.append({"name": "Czas trwania", "value": duration, "inline": True})

    title = f"🛡️ Moderacja: {action}"
    if case_id:
        title += f" [Sprawa #{case_id}]"

    return create_embed(
        title=title,
        color=config.MODERATION_COLOR,
        fields=fields,
        thumbnail_url=target.display_avatar.url if hasattr(target, "display_avatar") else None
    )

def log_embed(
    title: str,
    description: str,
    fields: list[dict[str, Any]] | None = None
) -> discord.Embed:
    """Zwraca embed przeznaczony do publikacji na kanale logów."""
    return create_embed(
        title=f"📋 Log: {title}",
        description=description,
        color=config.LOG_COLOR,
        fields=fields
    )