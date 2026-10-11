"""
Moduł zaawansowanego kreatora embedów (/embed).

Pozwala budować całe wiadomości (treść + do 10 embedów) w interaktywnym
kreatorze z podglądem na żywo, edytować embedy w istniejących wiadomościach
bota (komenda + menu kontekstowe), importować/eksportować surowy JSON
kompatybilny z popularnymi narzędziami (Discohook itp.), dołączać własny
plik jako obraz (attachment://) oraz zapisywać/wczytywać całe sesje jako
szablony per-serwer.
"""

import io
import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from utils.embeds import error_embed, success_embed, info_embed
import config

logger = logging.getLogger("AegisBot.Cogs.EmbedBuilder")

MESSAGE_LINK_RE = re.compile(
    r"(?:https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+))"
)
MAX_FIELDS = 25
MAX_EMBEDS = 10
MAX_CONTENT_CHARS = 2000
MAX_TOTAL_EMBED_CHARS = 6000


# --------------------------------------------------------------------------- #
#  Model danych
# --------------------------------------------------------------------------- #

class EmbedData:
    """Przechowuje stan pojedynczego budowanego embeda."""

    def __init__(self) -> None:
        self.title: str | None = None
        self.description: str | None = None
        self.url: str | None = None
        self.color: int = config.DEFAULT_EMBED_COLOR
        self.use_timestamp: bool = False
        self.author_name: str | None = None
        self.author_url: str | None = None
        self.author_icon: str | None = None
        self.footer_text: str | None = None
        self.footer_icon: str | None = None
        self.thumbnail_url: str | None = None
        self.image_url: str | None = None
        self.fields: list[dict[str, Any]] = []

    def clone(self) -> "EmbedData":
        return deepcopy(self)

    def is_empty(self) -> bool:
        return not any([
            self.title, self.description, self.author_name,
            self.footer_text, self.thumbnail_url, self.image_url, self.fields
        ])

    def char_count(self) -> int:
        total = len(self.title or "") + len(self.description or "") + len(self.footer_text or "") + len(self.author_name or "")
        for f in self.fields:
            total += len(f.get("name", "")) + len(f.get("value", ""))
        return total

    def short_title(self) -> str:
        return (self.title or self.description or "Bez tytułu").strip()[:80] or "Bez tytułu"

    def to_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title or None,
            description=self.description or None,
            url=self.url or None,
            color=self.color,
            timestamp=datetime.now(timezone.utc) if self.use_timestamp else None
        )

        if self.author_name:
            embed.set_author(name=self.author_name, url=self.author_url or None, icon_url=self.author_icon or None)
        if self.footer_text:
            embed.set_footer(text=self.footer_text, icon_url=self.footer_icon or None)
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        if self.image_url:
            embed.set_image(url=self.image_url)

        for field in self.fields[:MAX_FIELDS]:
            embed.add_field(
                name=field.get("name") or "\u200b",
                value=field.get("value") or "\u200b",
                inline=bool(field.get("inline", True))
            )

        if (
            embed.title is None and embed.description is None and not embed.fields
            and embed.author.name is None and embed.image.url is None and embed.thumbnail.url is None
        ):
            embed.description = "*Pusty embed — użyj przycisków poniżej, aby dodać treść.*"

        return embed

    def to_json_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"color": self.color}
        if self.title:
            data["title"] = self.title
        if self.description:
            data["description"] = self.description
        if self.url:
            data["url"] = self.url
        if self.use_timestamp:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        if self.author_name:
            data["author"] = {"name": self.author_name}
            if self.author_url:
                data["author"]["url"] = self.author_url
            if self.author_icon:
                data["author"]["icon_url"] = self.author_icon
        if self.footer_text:
            data["footer"] = {"text": self.footer_text}
            if self.footer_icon:
                data["footer"]["icon_url"] = self.footer_icon
        if self.thumbnail_url:
            data["thumbnail"] = {"url": self.thumbnail_url}
        if self.image_url:
            data["image"] = {"url": self.image_url}
        if self.fields:
            data["fields"] = self.fields
        return data

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "EmbedData":
        obj = cls()
        obj.title = str(data.get("title")) if data.get("title") else None
        obj.description = str(data.get("description")) if data.get("description") else None
        obj.url = data.get("url") or None

        color = data.get("color")
        if isinstance(color, int):
            obj.color = color
        elif isinstance(color, str):
            try:
                obj.color = parse_color(color)
            except ValueError:
                pass

        obj.use_timestamp = bool(data.get("timestamp"))

        author = data.get("author") or {}
        if isinstance(author, dict):
            obj.author_name = author.get("name") or None
            obj.author_url = author.get("url") or None
            obj.author_icon = author.get("icon_url") or None

        footer = data.get("footer") or {}
        if isinstance(footer, dict):
            obj.footer_text = footer.get("text") or None
            obj.footer_icon = footer.get("icon_url") or None

        thumbnail = data.get("thumbnail") or {}
        if isinstance(thumbnail, dict):
            obj.thumbnail_url = thumbnail.get("url") or None

        image = data.get("image") or {}
        if isinstance(image, dict):
            obj.image_url = image.get("url") or None

        fields = data.get("fields") or []
        if isinstance(fields, list):
            for f in fields[:MAX_FIELDS]:
                if not isinstance(f, dict):
                    continue
                name = str(f.get("name", "\u200b"))[:256]
                value = str(f.get("value", "\u200b"))[:1024]
                obj.fields.append({"name": name, "value": value, "inline": bool(f.get("inline", True))})

        return obj

    @classmethod
    def from_embed(cls, embed: discord.Embed) -> "EmbedData":
        return cls.from_json_dict(embed.to_dict())


class MessageSession:
    """Przechowuje stan całej budowanej wiadomości: treść tekstową + listę embedów."""

    def __init__(self) -> None:
        self.content: str | None = None
        self.embeds: list[EmbedData] = [EmbedData()]
        self.attachment_filename: str | None = None
        self.attachment_bytes: bytes | None = None

    def total_char_count(self) -> int:
        return sum(e.char_count() for e in self.embeds)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "embeds": [e.to_json_dict() for e in self.embeds]
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "MessageSession":
        session = cls()
        session.content = data.get("content") or None
        raw_embeds = data.get("embeds")
        if isinstance(raw_embeds, list) and raw_embeds:
            session.embeds = [EmbedData.from_json_dict(e) for e in raw_embeds[:MAX_EMBEDS] if isinstance(e, dict)]
        if not session.embeds:
            session.embeds = [EmbedData()]
        return session

    def build_file(self) -> discord.File | None:
        if self.attachment_bytes is None or self.attachment_filename is None:
            return None
        return discord.File(io.BytesIO(self.attachment_bytes), filename=self.attachment_filename)


def parse_color(raw: str) -> int:
    """Parsuje kolor podany jako #RRGGBB / RRGGBB / 0xRRGGBB / 'random' / nazwa z palety."""
    raw = raw.strip().lower()
    if raw in ("random", "losowy"):
        import random
        return random.randint(0, 0xFFFFFF)

    palette = {
        "blurple": 0x5865F2, "zielony": 0x57F287, "czerwony": 0xED4245,
        "zolty": 0xFEE75C, "żółty": 0xFEE75C, "niebieski": 0x3498DB,
        "pomaranczowy": 0xE67E22, "pomarańczowy": 0xE67E22, "fioletowy": 0x9B59B6,
        "czarny": 0x23272A, "bialy": 0xFFFFFF, "biały": 0xFFFFFF, "rozowy": 0xEB459E, "różowy": 0xEB459E,
    }
    if raw in palette:
        return palette[raw]

    raw = raw.removeprefix("#").removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{6}", raw):
        raise ValueError("Nieprawidłowy format koloru. Użyj np. `#5865F2`, nazwy (`fioletowy`) lub `random`.")
    return int(raw, 16)


def check_is_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if user.id == config.OWNER_ID:
        return True
    if isinstance(user, discord.Member):
        if user.guild_permissions.administrator:
            return True
        if interaction.guild and user.id == interaction.guild.owner_id:
            return True
    return False


# --------------------------------------------------------------------------- #
#  Modale
# --------------------------------------------------------------------------- #

class ContentModal(discord.ui.Modal, title="Treść główna embeda"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        embed = view.active_embed
        self.title_input = discord.ui.TextInput(
            label="Tytuł", required=False, max_length=256, default=embed.title or "",
            placeholder="Tytuł embeda (opcjonalnie)"
        )
        self.description_input = discord.ui.TextInput(
            label="Opis", required=False, max_length=4000, style=discord.TextStyle.paragraph,
            default=embed.description or "", placeholder="Główna treść embeda (opcjonalnie)"
        )
        self.url_input = discord.ui.TextInput(
            label="URL tytułu", required=False, max_length=512, default=embed.url or "",
            placeholder="https://... (link pod tytułem)"
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = self.view_ref.active_embed
        embed.title = self.title_input.value.strip() or None
        embed.description = self.description_input.value.strip() or None
        embed.url = self.url_input.value.strip() or None
        await self.view_ref.refresh(interaction)


class MessageContentModal(discord.ui.Modal, title="Treść wiadomości (poza embedem)"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        self.content_input = discord.ui.TextInput(
            label="Tekst nad embedem", required=False, max_length=MAX_CONTENT_CHARS,
            style=discord.TextStyle.paragraph, default=view.session.content or "",
            placeholder="Zwykły tekst wiadomości wyświetlany nad embedem (opcjonalnie)"
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.view_ref.session.content = self.content_input.value.strip() or None
        await self.view_ref.refresh(interaction)


class ColorModal(discord.ui.Modal, title="Kolor embeda"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        self.color_input = discord.ui.TextInput(
            label="Kolor (HEX, nazwa lub 'random')", required=True, max_length=20,
            default=f"#{view.active_embed.color:06X}", placeholder="#5865F2 / fioletowy / random"
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.view_ref.active_embed.color = parse_color(self.color_input.value)
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed("Nieprawidłowy kolor", str(e)), ephemeral=True)
            return
        await self.view_ref.refresh(interaction)


class AuthorModal(discord.ui.Modal, title="Autor embeda"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        embed = view.active_embed
        self.name_input = discord.ui.TextInput(label="Nazwa autora", required=False, max_length=256, default=embed.author_name or "")
        self.icon_input = discord.ui.TextInput(label="URL ikony autora", required=False, max_length=512, default=embed.author_icon or "")
        self.url_input = discord.ui.TextInput(label="URL autora (link po kliknięciu)", required=False, max_length=512, default=embed.author_url or "")
        self.add_item(self.name_input)
        self.add_item(self.icon_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = self.view_ref.active_embed
        embed.author_name = self.name_input.value.strip() or None
        embed.author_icon = self.icon_input.value.strip() or None
        embed.author_url = self.url_input.value.strip() or None
        await self.view_ref.refresh(interaction)


class FooterModal(discord.ui.Modal, title="Stopka embeda"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        embed = view.active_embed
        self.text_input = discord.ui.TextInput(label="Tekst stopki", required=False, max_length=2048, default=embed.footer_text or "")
        self.icon_input = discord.ui.TextInput(label="URL ikony stopki", required=False, max_length=512, default=embed.footer_icon or "")
        self.add_item(self.text_input)
        self.add_item(self.icon_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = self.view_ref.active_embed
        embed.footer_text = self.text_input.value.strip() or None
        embed.footer_icon = self.icon_input.value.strip() or None
        await self.view_ref.refresh(interaction)


class ImagesModal(discord.ui.Modal, title="Grafiki embeda"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        embed = view.active_embed
        hint = f"attachment://{view.session.attachment_filename}" if view.session.attachment_filename else "https://..."
        self.thumb_input = discord.ui.TextInput(
            label="URL miniatury (thumbnail)", required=False, max_length=512,
            default=embed.thumbnail_url or "", placeholder=hint
        )
        self.image_input = discord.ui.TextInput(
            label="URL dużego obrazu (image)", required=False, max_length=512,
            default=embed.image_url or "", placeholder=hint
        )
        self.add_item(self.thumb_input)
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = self.view_ref.active_embed
        embed.thumbnail_url = self.thumb_input.value.strip() or None
        embed.image_url = self.image_input.value.strip() or None
        await self.view_ref.refresh(interaction)


class AddFieldModal(discord.ui.Modal, title="Dodaj pole"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        self.name_input = discord.ui.TextInput(label="Nazwa pola", required=False, max_length=256)
        self.value_input = discord.ui.TextInput(label="Wartość pola", required=False, max_length=1024, style=discord.TextStyle.paragraph)
        self.inline_input = discord.ui.TextInput(label="W linii? (tak/nie)", required=False, max_length=3, default="tak")
        self.add_item(self.name_input)
        self.add_item(self.value_input)
        self.add_item(self.inline_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = self.view_ref.active_embed
        if len(embed.fields) >= MAX_FIELDS:
            await interaction.response.send_message(embed=error_embed("Limit pól", f"Embed może zawierać maksymalnie {MAX_FIELDS} pól."), ephemeral=True)
            return
        inline = self.inline_input.value.strip().lower() not in ("nie", "no", "false", "0")
        embed.fields.append({
            "name": self.name_input.value.strip() or "\u200b",
            "value": self.value_input.value.strip() or "\u200b",
            "inline": inline
        })
        await self.view_ref.refresh(interaction)


class EditFieldModal(discord.ui.Modal, title="Edytuj pole"):
    def __init__(self, manage_view: "FieldsManageView", index: int) -> None:
        super().__init__()
        self.manage_view = manage_view
        self.index = index
        field = manage_view.embed.fields[index]
        self.name_input = discord.ui.TextInput(label="Nazwa pola", required=False, max_length=256, default=field["name"])
        self.value_input = discord.ui.TextInput(label="Wartość pola", required=False, max_length=1024, style=discord.TextStyle.paragraph, default=field["value"])
        self.inline_input = discord.ui.TextInput(label="W linii? (tak/nie)", required=False, max_length=3, default="tak" if field.get("inline", True) else "nie")
        self.add_item(self.name_input)
        self.add_item(self.value_input)
        self.add_item(self.inline_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        inline = self.inline_input.value.strip().lower() not in ("nie", "no", "false", "0")
        self.manage_view.embed.fields[self.index] = {
            "name": self.name_input.value.strip() or "\u200b",
            "value": self.value_input.value.strip() or "\u200b",
            "inline": inline
        }
        await self.manage_view.refresh_all(interaction)


class JSONImportModal(discord.ui.Modal, title="Importuj JSON"):
    def __init__(self, view: "EmbedBuilderView") -> None:
        super().__init__()
        self.view_ref = view
        self.json_input = discord.ui.TextInput(
            label="Surowy JSON (cała wiadomość lub jeden embed)",
            required=True, max_length=4000, style=discord.TextStyle.paragraph,
            placeholder='{"content": "...", "embeds": [{"title": "Przykład", "color": 5793266}]}'
        )
        self.add_item(self.json_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            parsed = json.loads(self.json_input.value)
            if not isinstance(parsed, dict):
                raise ValueError("Najwyższy poziom JSON musi być obiektem.")
        except json.JSONDecodeError as e:
            await interaction.response.send_message(embed=error_embed("Błąd parsowania JSON", f"`{e}`"), ephemeral=True)
            return
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed("Błąd danych", f"`{e}`"), ephemeral=True)
            return

        try:
            if "embeds" in parsed or "content" in parsed:
                new_session = MessageSession.from_json_dict(parsed)
                self.view_ref.session.content = new_session.content
                self.view_ref.session.embeds = new_session.embeds
            else:
                self.view_ref.session.embeds[self.view_ref.active_index] = EmbedData.from_json_dict(parsed)
            self.view_ref.active_index = min(self.view_ref.active_index, len(self.view_ref.session.embeds) - 1)
        except Exception as e:
            await interaction.response.send_message(embed=error_embed("Nie udało się wczytać JSON", f"`{e}`"), ephemeral=True)
            return

        await self.view_ref.refresh(interaction)


class TemplateSaveModal(discord.ui.Modal, title="Zapisz jako szablon"):
    def __init__(self, cog: "EmbedBuilderCog", view: "EmbedBuilderView") -> None:
        super().__init__()
        self.cog = cog
        self.view_ref = view
        self.name_input = discord.ui.TextInput(label="Nazwa szablonu", required=True, max_length=64)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.name_input.value.strip()
        await self.cog.save_template(interaction.guild_id, name, interaction.user.id, self.view_ref.session)
        await interaction.response.send_message(embed=success_embed("Szablon zapisany", f"Zapisano bieżącą wiadomość jako szablon `{name}` ({len(self.view_ref.session.embeds)} embed(ów))."), ephemeral=True)


# --------------------------------------------------------------------------- #
#  Panel zarządzania polami aktywnego embeda
# --------------------------------------------------------------------------- #

class FieldsManageView(discord.ui.View):
    """Pozwala edytować, przesuwać i usuwać pola aktywnego embeda."""

    def __init__(self, builder_view: "EmbedBuilderView", origin_message: discord.Message) -> None:
        super().__init__(timeout=180)
        self.builder_view = builder_view
        self.origin_message = origin_message
        self.embed = builder_view.active_embed
        self.selected_index: int | None = None
        self._rebuild_select()

    def _rebuild_select(self) -> None:
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        options = [
            discord.SelectOption(label=f"{i + 1}. {f['name'][:90]}", value=str(i), default=(i == self.selected_index))
            for i, f in enumerate(self.embed.fields)
        ][:25]
        select = discord.ui.Select(placeholder="Wybierz pole...", options=options or [discord.SelectOption(label="Brak pól", value="-1")])
        select.disabled = not options
        select.callback = self.on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.builder_view.author_id

    async def on_select(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.values:
                self.selected_index = int(item.values[0])
        self._rebuild_select()
        await interaction.response.edit_message(view=self)

    async def refresh_all(self, interaction: discord.Interaction) -> None:
        """Odświeża zarówno panel pól, jak i główną wiadomość kreatora."""
        self._rebuild_select()
        await interaction.response.edit_message(view=self)
        try:
            await self.origin_message.edit(**self.builder_view.render_kwargs())
        except Exception:
            pass

    @discord.ui.button(label="Edytuj", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.selected_index is None:
            await interaction.response.send_message("Najpierw wybierz pole z listy.", ephemeral=True)
            return
        await interaction.response.send_modal(EditFieldModal(self, self.selected_index))

    @discord.ui.button(label="W górę", style=discord.ButtonStyle.secondary, emoji="⬆️", row=1)
    async def move_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        i = self.selected_index
        if i is None or i <= 0:
            await interaction.response.send_message("Nie można przesunąć wyżej.", ephemeral=True)
            return
        self.embed.fields[i - 1], self.embed.fields[i] = self.embed.fields[i], self.embed.fields[i - 1]
        self.selected_index = i - 1
        await self.refresh_all(interaction)

    @discord.ui.button(label="W dół", style=discord.ButtonStyle.secondary, emoji="⬇️", row=1)
    async def move_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        i = self.selected_index
        if i is None or i >= len(self.embed.fields) - 1:
            await interaction.response.send_message("Nie można przesunąć niżej.", ephemeral=True)
            return
        self.embed.fields[i + 1], self.embed.fields[i] = self.embed.fields[i], self.embed.fields[i + 1]
        self.selected_index = i + 1
        await self.refresh_all(interaction)

    @discord.ui.button(label="Usuń", style=discord.ButtonStyle.danger, emoji="🗑️", row=2)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.selected_index is None:
            await interaction.response.send_message("Najpierw wybierz pole z listy.", ephemeral=True)
            return
        self.embed.fields.pop(self.selected_index)
        self.selected_index = None
        await self.refresh_all(interaction)

    @discord.ui.button(label="Zamknij", style=discord.ButtonStyle.secondary, emoji="✖️", row=2)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Panel pól zamknięty.", view=None)


# --------------------------------------------------------------------------- #
#  Wybór kanału / wysyłka finalnej wiadomości
# --------------------------------------------------------------------------- #

class SendTargetView(discord.ui.View):
    def __init__(self, builder_view: "EmbedBuilderView") -> None:
        super().__init__(timeout=120)
        self.builder_view = builder_view

        select = discord.ui.ChannelSelect(
            placeholder="Wybierz kanał docelowy...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.public_thread, discord.ChannelType.private_thread]
        )
        select.callback = self.on_select
        self.add_item(select)
        self.select = select

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.builder_view.author_id:
            await interaction.response.send_message("Ta interakcja należy do innej osoby.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Wyślij na tym kanale", style=discord.ButtonStyle.primary, emoji="📨", row=1)
    async def send_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_to(interaction, interaction.channel)

    @discord.ui.button(label="Anuluj", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Wysyłka anulowana.", embed=None, view=None)

    async def on_select(self, interaction: discord.Interaction) -> None:
        raw_channel = self.select.values[0]
        channel = raw_channel.resolve() or await raw_channel.fetch()
        await self._send_to(interaction, channel)

    async def _send_to(self, interaction: discord.Interaction, channel: discord.abc.Messageable) -> None:
        session = self.builder_view.session

        if interaction.guild and interaction.guild.me and hasattr(channel, "permissions_for"):
            perms = channel.permissions_for(interaction.guild.me)
            needed = perms.send_messages and perms.embed_links and (perms.attach_files or session.attachment_bytes is None)
            if not needed:
                await interaction.response.send_message(
                    embed=error_embed("Brak uprawnień", f"Bot nie ma wystarczających uprawnień na kanale {channel.mention}."),
                    ephemeral=True
                )
                return

        kwargs = {"content": session.content or None, "embeds": [e.to_embed() for e in session.embeds]}
        file = session.build_file()
        if file is not None:
            kwargs["file"] = file

        try:
            await channel.send(**kwargs)
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd wysyłki", f"Discord odrzucił wiadomość: `{e}`"), ephemeral=True)
            return

        await interaction.response.edit_message(content=f"✅ Wiadomość wysłana na {getattr(channel, 'mention', channel)}.", embed=None, view=None)
        self.builder_view.stop()


# --------------------------------------------------------------------------- #
#  Główny widok kreatora
# --------------------------------------------------------------------------- #

class EmbedBuilderView(discord.ui.View):
    """Interaktywny kreator wiadomości z wieloma embedami i podglądem na żywo."""

    def __init__(
        self,
        cog: "EmbedBuilderCog",
        author_id: int,
        session: MessageSession | None = None,
        edit_target: discord.Message | None = None
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.author_id = author_id
        self.session = session or MessageSession()
        self.active_index = 0
        self.edit_target = edit_target

        self._embed_select: discord.ui.Select | None = None
        self._build_static_items()
        self._rebuild_embed_select()

    @property
    def active_embed(self) -> EmbedData:
        return self.session.embeds[self.active_index]

    def render_kwargs(self) -> dict[str, Any]:
        return {"content": self.session.content or None, "embeds": [e.to_embed() for e in self.session.embeds], "view": self}

    async def refresh(self, interaction: discord.Interaction) -> None:
        self._sync_timestamp_button()
        self._rebuild_embed_select()
        await interaction.response.edit_message(**self.render_kwargs())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ten kreator należy do innej osoby.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    # --- budowanie stałych przycisków (row 1-4) ---

    def _build_static_items(self) -> None:
        specs = [
            ("Treść", discord.ButtonStyle.primary, "📝", 1, self._on_content),
            ("Kolor", discord.ButtonStyle.primary, "🎨", 1, self._on_color),
            ("Autor", discord.ButtonStyle.primary, "👤", 1, self._on_author),
            ("Stopka", discord.ButtonStyle.primary, "🦶", 1, self._on_footer),
            ("Grafiki", discord.ButtonStyle.primary, "🖼️", 1, self._on_images),

            ("Dodaj pole", discord.ButtonStyle.secondary, "➕", 2, self._on_add_field),
            ("Zarządzaj polami", discord.ButtonStyle.secondary, "🧩", 2, self._on_manage_fields),
            ("Wyczyść pola", discord.ButtonStyle.secondary, "🧹", 2, self._on_clear_fields),
            (None, discord.ButtonStyle.secondary, "🕒", 2, self._on_timestamp),  # etykieta ustawiana dynamicznie
            ("Statystyki", discord.ButtonStyle.secondary, "📊", 2, self._on_stats),

            ("Nowy embed", discord.ButtonStyle.secondary, "🆕", 3, self._on_new_embed),
            ("Duplikuj embed", discord.ButtonStyle.secondary, "📑", 3, self._on_duplicate_embed),
            ("Usuń embed", discord.ButtonStyle.secondary, "🗑️", 3, self._on_delete_embed),
            ("Treść wiadomości", discord.ButtonStyle.secondary, "💬", 3, self._on_message_content),
            ("Autor: Ja", discord.ButtonStyle.secondary, "🙋", 3, self._on_author_me),

            ("Eksportuj JSON", discord.ButtonStyle.secondary, "📋", 4, self._on_export_json),
            ("Importuj JSON", discord.ButtonStyle.secondary, "📥", 4, self._on_import_json),
            ("Zapisz szablon", discord.ButtonStyle.secondary, "🗂️", 4, self._on_save_template),
            ("Anuluj", discord.ButtonStyle.danger, "❌", 4, self._on_cancel),
        ]

        self.timestamp_button: discord.ui.Button | None = None

        for label, style, emoji, row, callback in specs:
            btn = discord.ui.Button(label=label or "Znacznik czasu: WYŁ.", style=style, emoji=emoji, row=row)
            btn.callback = callback
            self.add_item(btn)
            if callback is self._on_timestamp:
                self.timestamp_button = btn

        if self.edit_target is not None:
            save_btn = discord.ui.Button(label="Zapisz zmiany", style=discord.ButtonStyle.success, emoji="💾", row=4)
            save_btn.callback = self._on_save_edit
            self.add_item(save_btn)
        else:
            send_btn = discord.ui.Button(label="Wyślij", style=discord.ButtonStyle.success, emoji="📤", row=4)
            send_btn.callback = self._on_open_send
            self.add_item(send_btn)

        self._sync_timestamp_button()

    def _sync_timestamp_button(self) -> None:
        if self.timestamp_button is None:
            return
        active = self.active_embed.use_timestamp
        self.timestamp_button.label = f"Znacznik czasu: {'WŁ.' if active else 'WYŁ.'}"
        self.timestamp_button.style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary

    def _rebuild_embed_select(self) -> None:
        if self._embed_select is not None:
            self.remove_item(self._embed_select)

        options = [
            discord.SelectOption(
                label=f"{i + 1}. {e.short_title()}",
                value=str(i),
                default=(i == self.active_index),
                emoji="▶️" if i == self.active_index else None
            )
            for i, e in enumerate(self.session.embeds)
        ]
        select = discord.ui.Select(placeholder=f"Aktywny embed ({self.active_index + 1}/{len(self.session.embeds)}) — kliknij, aby przełączyć", options=options, row=0)
        select.callback = self._on_switch_embed
        self.add_item(select)
        self._embed_select = select

    # --- callbacki ---

    async def _on_switch_embed(self, interaction: discord.Interaction) -> None:
        self.active_index = int(self._embed_select.values[0])
        await self.refresh(interaction)

    async def _on_content(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ContentModal(self))

    async def _on_message_content(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MessageContentModal(self))

    async def _on_color(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ColorModal(self))

    async def _on_author(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AuthorModal(self))

    async def _on_author_me(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        embed = self.active_embed
        embed.author_name = user.display_name
        embed.author_icon = user.display_avatar.url
        embed.author_url = None
        await self.refresh(interaction)

    async def _on_footer(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FooterModal(self))

    async def _on_images(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ImagesModal(self))

    async def _on_add_field(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddFieldModal(self))

    async def _on_manage_fields(self, interaction: discord.Interaction) -> None:
        if not self.active_embed.fields:
            await interaction.response.send_message(embed=error_embed("Brak pól", "Aktywny embed nie posiada jeszcze żadnych pól."), ephemeral=True)
            return
        await interaction.response.send_message(view=FieldsManageView(self, interaction.message), ephemeral=True)

    async def _on_clear_fields(self, interaction: discord.Interaction) -> None:
        self.active_embed.fields.clear()
        await self.refresh(interaction)

    async def _on_timestamp(self, interaction: discord.Interaction) -> None:
        self.active_embed.use_timestamp = not self.active_embed.use_timestamp
        await self.refresh(interaction)

    async def _on_new_embed(self, interaction: discord.Interaction) -> None:
        if len(self.session.embeds) >= MAX_EMBEDS:
            await interaction.response.send_message(embed=error_embed("Limit embedów", f"Wiadomość może zawierać maksymalnie {MAX_EMBEDS} embedów."), ephemeral=True)
            return
        self.session.embeds.append(EmbedData())
        self.active_index = len(self.session.embeds) - 1
        await self.refresh(interaction)

    async def _on_duplicate_embed(self, interaction: discord.Interaction) -> None:
        if len(self.session.embeds) >= MAX_EMBEDS:
            await interaction.response.send_message(embed=error_embed("Limit embedów", f"Wiadomość może zawierać maksymalnie {MAX_EMBEDS} embedów."), ephemeral=True)
            return
        clone = self.active_embed.clone()
        self.session.embeds.insert(self.active_index + 1, clone)
        self.active_index += 1
        await self.refresh(interaction)

    async def _on_delete_embed(self, interaction: discord.Interaction) -> None:
        if len(self.session.embeds) <= 1:
            self.session.embeds[0] = EmbedData()
        else:
            self.session.embeds.pop(self.active_index)
            self.active_index = min(self.active_index, len(self.session.embeds) - 1)
        await self.refresh(interaction)

    async def _on_export_json(self, interaction: discord.Interaction) -> None:
        payload = json.dumps(self.session.to_json_dict(), indent=2, ensure_ascii=False)
        if len(payload) > 1900:
            file = discord.File(io.BytesIO(payload.encode("utf-8")), filename="wiadomosc.json")
            await interaction.response.send_message("Eksport JSON (plik, bo treść przekracza limit wiadomości):", file=file, ephemeral=True)
        else:
            await interaction.response.send_message(f"```json\n{payload}\n```", ephemeral=True)

    async def _on_import_json(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(JSONImportModal(self))

    async def _on_save_template(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TemplateSaveModal(self.cog, self))

    async def _on_stats(self, interaction: discord.Interaction) -> None:
        total_embed_chars = self.session.total_char_count()
        content_chars = len(self.session.content or "")
        lines = [f"**Treść wiadomości:** {content_chars}/{MAX_CONTENT_CHARS} znaków"]
        for i, e in enumerate(self.session.embeds):
            marker = "▶️ " if i == self.active_index else ""
            lines.append(f"{marker}Embed {i + 1}: {e.char_count()} znaków, {len(e.fields)}/{MAX_FIELDS} pól")
        lines.append(f"\n**Łącznie znaków we embedach:** {total_embed_chars}/{MAX_TOTAL_EMBED_CHARS}")
        if total_embed_chars > MAX_TOTAL_EMBED_CHARS:
            lines.append("⚠️ Przekroczono łączny limit Discorda — wysyłka zostanie odrzucona.")
        lines.append(f"**Liczba embedów:** {len(self.session.embeds)}/{MAX_EMBEDS}")
        await interaction.response.send_message(embed=info_embed("📊 Statystyki wiadomości", "\n".join(lines)), ephemeral=True)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Kreator anulowany.", embed=None, view=None)
        self.stop()

    async def _on_open_send(self, interaction: discord.Interaction) -> None:
        if all(e.is_empty() for e in self.session.embeds) and not self.session.content:
            await interaction.response.send_message(embed=error_embed("Pusta wiadomość", "Dodaj przynajmniej trochę treści przed wysłaniem."), ephemeral=True)
            return
        if self.session.total_char_count() > MAX_TOTAL_EMBED_CHARS:
            await interaction.response.send_message(embed=error_embed("Za dużo znaków", f"Łączna liczba znaków we embedach przekracza limit {MAX_TOTAL_EMBED_CHARS}. Sprawdź przycisk „Statystyki”."), ephemeral=True)
            return
        await interaction.response.send_message(content="Gdzie mam wysłać tę wiadomość?", view=SendTargetView(self), ephemeral=True)

    async def _on_save_edit(self, interaction: discord.Interaction) -> None:
        assert self.edit_target is not None
        try:
            await self.edit_target.edit(content=self.session.content or None, embeds=[e.to_embed() for e in self.session.embeds])
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd edycji", f"Nie udało się zaktualizować wiadomości: `{e}`"), ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Zaktualizowano wiadomość: {self.edit_target.jump_url}", embed=None, view=None)
        self.stop()


# --------------------------------------------------------------------------- #
#  Cog
# --------------------------------------------------------------------------- #

class EmbedBuilderCog(commands.Cog):
    """Zaawansowany kreator, edytor i menadżer szablonów wiadomości/embedów."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ctx_menu_edit: app_commands.ContextMenu | None = None

    async def cog_load(self) -> None:
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS embed_templates (
                template_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                author_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(guild_id, name)
            );
            """
        )
        self.ctx_menu_edit = app_commands.ContextMenu(name="Edytuj embed bota", callback=self.edit_context_menu)
        self.bot.tree.add_command(self.ctx_menu_edit)

    async def cog_unload(self) -> None:
        if self.ctx_menu_edit is not None:
            self.bot.tree.remove_command(self.ctx_menu_edit.name, type=self.ctx_menu_edit.type)

    async def save_template(self, guild_id: int, name: str, author_id: int, session: MessageSession) -> None:
        payload = json.dumps(session.to_json_dict(), ensure_ascii=False)
        await self.bot.db.execute(
            """
            INSERT INTO embed_templates (guild_id, name, author_id, data, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, name) DO UPDATE SET
                data = excluded.data, author_id = excluded.author_id, created_at = excluded.created_at
            """,
            (guild_id, name, author_id, payload, datetime.now(timezone.utc).isoformat())
        )

    embed_group = app_commands.Group(
        name="embed",
        description="Zaawansowany kreator embedów",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True
    )

    template_group = app_commands.Group(
        name="szablon",
        description="Zarządzanie zapisanymi szablonami wiadomości/embedów",
        parent=embed_group
    )

    # --- /embed nowy ---

    @embed_group.command(name="nowy", description="Otwiera interaktywny kreator nowej wiadomości / embeda.")
    @app_commands.describe(
        tresc="Opcjonalny tekst wiadomości wyświetlany nad embedem.",
        plik="Opcjonalny plik graficzny — użyj `attachment://nazwa_pliku` jako URL obrazu/miniatury w kreatorze."
    )
    @is_administrator()
    async def embed_new(self, interaction: discord.Interaction, tresc: str | None = None, plik: discord.Attachment | None = None) -> None:
        session = MessageSession()
        session.content = tresc

        if plik is not None:
            session.attachment_bytes = await plik.read()
            session.attachment_filename = plik.filename

        view = EmbedBuilderView(self, interaction.user.id, session=session)
        await interaction.response.send_message(**view.render_kwargs(), ephemeral=True)

    # --- /embed edytuj ---

    @embed_group.command(name="edytuj", description="Edytuje treść i embedy istniejącej wiadomości bota (przez link lub ID).")
    @app_commands.describe(
        wiadomosc="Link do wiadomości lub jej ID.",
        kanal="Kanał, na którym znajduje się wiadomość (wymagane, jeśli podajesz samo ID)."
    )
    @is_administrator()
    async def embed_edit(self, interaction: discord.Interaction, wiadomosc: str, kanal: discord.TextChannel | discord.Thread | None = None) -> None:
        message = await self._resolve_message(interaction, wiadomosc, kanal)
        if message is None:
            return
        await self._start_edit(interaction, message)

    async def edit_context_menu(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if not check_is_admin(interaction):
            await interaction.response.send_message(embed=error_embed("Brak uprawnień", "Ta akcja wymaga uprawnień Administratora."), ephemeral=True)
            return
        await self._start_edit(interaction, message)

    async def _start_edit(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if message.author.id != self.bot.user.id:
            await interaction.response.send_message(embed=error_embed("Nie można edytować", "Mogę edytować wyłącznie wiadomości wysłane przeze mnie."), ephemeral=True)
            return
        if not message.embeds and not message.content:
            await interaction.response.send_message(embed=error_embed("Pusta wiadomość", "Ta wiadomość nie zawiera ani treści, ani embedów do edycji."), ephemeral=True)
            return

        session = MessageSession()
        session.content = message.content or None
        session.embeds = [EmbedData.from_embed(e) for e in message.embeds[:MAX_EMBEDS]] or [EmbedData()]

        view = EmbedBuilderView(self, interaction.user.id, session=session, edit_target=message)
        await interaction.response.send_message(
            content=f"Edytujesz wiadomość: {message.jump_url}\n" + (session.content or ""),
            embeds=[e.to_embed() for e in session.embeds], view=view, ephemeral=True
        )

    async def _resolve_message(self, interaction: discord.Interaction, raw: str, channel_hint: discord.TextChannel | discord.Thread | None) -> discord.Message | None:
        match = MESSAGE_LINK_RE.search(raw.strip())
        if match:
            guild_id, channel_id, message_id = (int(x) for x in match.groups())
            if guild_id != interaction.guild_id:
                await interaction.response.send_message(embed=error_embed("Zła wiadomość", "Ten link wskazuje na wiadomość z innego serwera."), ephemeral=True)
                return None
            channel = interaction.guild.get_channel_or_thread(channel_id) or await self.bot.fetch_channel(channel_id)
        elif raw.strip().isdigit():
            message_id = int(raw.strip())
            channel = channel_hint or interaction.channel
        else:
            await interaction.response.send_message(embed=error_embed("Nieprawidłowy format", "Podaj link do wiadomości lub jej ID (wraz z kanałem)."), ephemeral=True)
            return None

        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, AttributeError):
            await interaction.response.send_message(embed=error_embed("Nie znaleziono", "Nie udało się odnaleźć tej wiadomości na wskazanym kanale."), ephemeral=True)
            return None

    # --- /embed json ---

    @embed_group.command(name="json", description="Wysyła wiadomość zbudowaną z surowego kodu JSON (content + embeds).")
    @app_commands.describe(dane="Treść JSON, np. {\"embeds\": [{\"title\": \"Cześć\", \"color\": 5793266}]}", kanal="Kanał docelowy (domyślnie bieżący).")
    @is_administrator()
    async def embed_json(self, interaction: discord.Interaction, dane: str, kanal: discord.TextChannel | discord.Thread | None = None) -> None:
        try:
            parsed = json.loads(dane)
            if not isinstance(parsed, dict):
                raise ValueError("Najwyższy poziom JSON musi być obiektem.")
            session = MessageSession.from_json_dict(parsed) if ("embeds" in parsed or "content" in parsed) else MessageSession()
            if "embeds" not in parsed and "content" not in parsed:
                session.embeds = [EmbedData.from_json_dict(parsed)]
        except (json.JSONDecodeError, ValueError) as e:
            await interaction.response.send_message(embed=error_embed("Błąd parsowania JSON", f"`{e}`"), ephemeral=True)
            return

        target = kanal or interaction.channel
        try:
            await target.send(content=session.content or None, embeds=[e.to_embed() for e in session.embeds])
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Discord odrzucił wiadomość", f"`{e}`"), ephemeral=True)
            return

        await interaction.response.send_message(embed=success_embed("Wysłano", f"Wiadomość została wysłana na {target.mention}."), ephemeral=True)

    # --- /embed szablon ... ---

    @template_group.command(name="lista", description="Wyświetla listę zapisanych szablonów na tym serwerze.")
    @is_administrator()
    async def template_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall("SELECT name, author_id FROM embed_templates WHERE guild_id = ? ORDER BY name", (interaction.guild_id,))
        if not rows:
            await interaction.response.send_message(embed=info_embed("Brak szablonów", "Ten serwer nie ma jeszcze zapisanych szablonów."), ephemeral=True)
            return
        lines = [f"• **{r['name']}** — dodał <@{r['author_id']}>" for r in rows]
        await interaction.response.send_message(embed=info_embed("🗂️ Szablony wiadomości", "\n".join(lines)), ephemeral=True)

    @template_group.command(name="wczytaj", description="Otwiera kreator wypełniony zapisanym szablonem.")
    @app_commands.describe(nazwa="Nazwa zapisanego szablonu.")
    @is_administrator()
    async def template_load(self, interaction: discord.Interaction, nazwa: str) -> None:
        row = await self.bot.db.fetchone("SELECT data FROM embed_templates WHERE guild_id = ? AND name = ?", (interaction.guild_id, nazwa))
        if not row:
            await interaction.response.send_message(embed=error_embed("Nie znaleziono", f"Brak szablonu o nazwie `{nazwa}`."), ephemeral=True)
            return
        session = MessageSession.from_json_dict(json.loads(row["data"]))
        view = EmbedBuilderView(self, interaction.user.id, session=session)
        await interaction.response.send_message(**view.render_kwargs(), ephemeral=True)

    @template_load.autocomplete("nazwa")
    async def template_name_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        rows = await self.bot.db.fetchall(
            "SELECT name FROM embed_templates WHERE guild_id = ? AND name LIKE ? ORDER BY name LIMIT 25",
            (interaction.guild_id, f"%{current}%")
        )
        return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows]

    @template_group.command(name="usun", description="Usuwa zapisany szablon.")
    @app_commands.describe(nazwa="Nazwa szablonu do usunięcia.")
    @is_administrator()
    async def template_delete(self, interaction: discord.Interaction, nazwa: str) -> None:
        row = await self.bot.db.fetchone("SELECT template_id FROM embed_templates WHERE guild_id = ? AND name = ?", (interaction.guild_id, nazwa))
        if not row:
            await interaction.response.send_message(embed=error_embed("Nie znaleziono", f"Brak szablonu o nazwie `{nazwa}`."), ephemeral=True)
            return
        await self.bot.db.execute("DELETE FROM embed_templates WHERE template_id = ?", (row["template_id"],))
        await interaction.response.send_message(embed=success_embed("Usunięto", f"Szablon `{nazwa}` został usunięty."), ephemeral=True)

    @template_delete.autocomplete("nazwa")
    async def template_delete_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.template_name_autocomplete(interaction, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmbedBuilderCog(bot))