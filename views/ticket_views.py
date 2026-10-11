"""
Trwałe widoki (Persistent Views), formularze Modal oraz przyciski obsługi systemu Ticketów.
"""

import io
import asyncio
import logging
import discord
from discord.ext import commands

from utils.embeds import success_embed, error_embed, info_embed, warning_embed
from services.transcript_service import TranscriptService

logger = logging.getLogger("AegisBot.TicketViews")

class TicketCategorySelect(discord.ui.Select):
    """Rozwijana lista wyboru kategorii zgłoszenia."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="Pomoc ogólna", value="pomoc", emoji="❓", description="Pytania i problemy techniczne"),
            discord.SelectOption(label="Zgłoszenie gracza", value="zgloszenie", emoji="🚨", description="Zgłoś naruszenie regulaminu"),
            discord.SelectOption(label="Zakupy / Sklep", value="zakup", emoji="🛒", description="Pytania o rangi i transakcje"),
            discord.SelectOption(label="Odwołanie od kary", value="odwolanie", emoji="⚖️", description="Odwołanie od bana lub timeoutu"),
            discord.SelectOption(label="Współpraca", value="wspolpraca", emoji="🤝", description="Propozycje współpracy i partnerstwa"),
            discord.SelectOption(label="Inne", value="inne", emoji="📁", description="Inne sprawy niewymienione wyżej")
        ]
        super().__init__(
            placeholder="Wybierz kategorię zgłoszenia...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_category_select"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        category = self.values[0]
        modal = TicketReasonModal(category)
        await interaction.response.send_modal(modal)


class TicketLauncherView(discord.ui.View):
    """Trwały widok panelu otwierania ticketów na kanale głównym."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())


class TicketReasonModal(discord.ui.Modal):
    """Formularz zgłoszeniowy uzupełniany przez użytkownika przy otwieraniu ticketu."""

    def __init__(self, category: str) -> None:
        super().__init__(title=f"Nowe Zgłoszenie — Kategoria: {category.capitalize()}")
        self.category = category

        self.subject = discord.ui.TextInput(
            label="Temat zgłoszenia",
            placeholder="Zwięzły opis sprawy...",
            max_length=100,
            required=True
        )
        self.description = discord.ui.TextInput(
            label="Szczegółowy opis",
            style=discord.TextStyle.paragraph,
            placeholder="Opisz dokładnie swój problem lub zgłoszenie...",
            max_length=1000,
            required=True
        )

        self.add_item(self.subject)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        user = interaction.user
        if not guild:
            return

        bot = interaction.client
        db = bot.db

        existing = await db.fetchone(
            "SELECT ticket_id, channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND category = ? AND status = 'OPEN'",
            (guild.id, user.id, self.category)
        )
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            ch_mention = ch.mention if ch else f"ID: {existing['channel_id']}"
            await interaction.response.send_message(
                embed=error_embed("Limit Zgłoszeń", f"Posiadasz już aktywne zgłoszenie w tej kategorii: {ch_mention}"),
                ephemeral=True
            )
            return

        cfg = await db.fetchone("SELECT ticket_category_id, mod_role_id FROM guild_config WHERE guild_id = ?", (guild.id,))
        category_channel = guild.get_channel(cfg["ticket_category_id"]) if cfg and cfg["ticket_category_id"] else None

        count_row = await db.fetchone("SELECT COUNT(*) as total FROM tickets WHERE guild_id = ?", (guild.id,))
        ticket_num = (count_row["total"] if count_row else 0) + 1
        channel_name = f"ticket-{ticket_num:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        if cfg and cfg["mod_role_id"]:
            mod_role = guild.get_role(cfg["mod_role_id"])
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category_channel if isinstance(category_channel, discord.CategoryChannel) else None,
                overwrites=overwrites,
                reason=f"Tworzenie zgłoszenia przez {user}"
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Błąd Tworzenia Kanału", f"Nie udało się utworzyć kanału ticketu: {e}"),
                ephemeral=True
            )
            return

        cursor = await db.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, category, status) VALUES (?, ?, ?, ?, 'OPEN')",
            (guild.id, ticket_channel.id, user.id, self.category)
        )
        ticket_id = cursor.lastrowid

        await db.execute("INSERT INTO ticket_members (ticket_id, user_id) VALUES (?, ?)", (ticket_id, user.id))

        welcome_emb = info_embed(
            title=f"🎫 Zgłoszenie #{ticket_num:04d} — {self.subject.value}",
            description=f"Witaj {user.mention}!\nTwój ticket został otwarty. Opisz dokładnie sprawę i poczekaj na odpowiedź administracji.\n\n**Kategoria:** `{self.category}`\n**Opis:** {self.description.value}"
        )
        welcome_emb.set_footer(text=f"Ticket ID: {ticket_id} • Utworzono przez {user}")

        await ticket_channel.send(content=f"{user.mention}", embed=welcome_emb, view=TicketControlView())

        await interaction.response.send_message(
            embed=success_embed("Utworzono Zgłoszenie", f"Twój ticket został utworzony: {ticket_channel.mention}"),
            ephemeral=True
        )


class AddUserModal(discord.ui.Modal, title="Dodaj użytkownika do ticketu"):
    user_id_input = discord.ui.TextInput(label="ID Użytkownika", placeholder="Wpisz ID użytkownika...", required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            target_id = int(self.user_id_input.value.strip())
            member = await interaction.guild.fetch_member(target_id)
        except Exception:
            await interaction.response.send_message(embed=error_embed("Błąd", "Nie odnaleziono użytkownika o podanym ID."), ephemeral=True)
            return

        ch = interaction.channel
        if isinstance(ch, discord.TextChannel):
            await ch.set_permissions(member, read_messages=True, send_messages=True)
            await interaction.response.send_message(embed=success_embed("Dodano Użytkownika", f"Dodano {member.mention} do ticketu."))


class ConfirmDeleteView(discord.ui.View):
    """Potwierdzenie usunięcia kanału ticketu."""

    def __init__(self) -> None:
        super().__init__(timeout=60)

    @discord.ui.button(label="Potwierdź usunięcie 🗑️", style=discord.ButtonStyle.danger, custom_id="confirm_delete_ticket")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        await interaction.response.send_message(embed=error_embed("Usuwanie Zgłoszenia", "Generowanie transkryptu i usuwanie kanału za 3 sekundy..."))
        
        # Generuj i zapisz transkrypt w logach przed usunięciem
        try:
            messages = [msg async for msg in channel.history(limit=500, oldest_first=True)]
            html_code = await TranscriptService.generate_html(channel, messages)
            file = discord.File(io.BytesIO(html_code.encode("utf-8")), filename=f"transcript-{channel.name}.html")

            cfg = await interaction.client.db.fetchone("SELECT log_channel_id FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
            if cfg and cfg["log_channel_id"]:
                log_ch = interaction.guild.get_channel(cfg["log_channel_id"])
                if isinstance(log_ch, discord.TextChannel):
                    await log_ch.send(content=f"📄 **Transkrypt ze skasowanego zgłoszenia #{channel.name}:**", file=file)
        except Exception as e:
            logger.error(f"Błąd podczas auto-transkryptu przy kasowaniu: {e}")

        await asyncio.sleep(3)
        await channel.delete(reason=f"Usunięcie ticketu przez {interaction.user}")

    @discord.ui.button(label="Anuluj ❌", style=discord.ButtonStyle.secondary, custom_id="cancel_delete_ticket")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Anulowano usunięcie ticketu.", embed=None, view=None)


class TicketControlView(discord.ui.View):
    """Trwały panel kontrolny widoczny wewnątrz kanału zgłoszenia."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Przejmij ticket", style=discord.ButtonStyle.primary, emoji="🖐️", custom_id="ticket_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = interaction.client.db
        row = await db.fetchone("SELECT ticket_id, claimed_by FROM tickets WHERE channel_id = ?", (interaction.channel_id,))
        if not row:
            await interaction.response.send_message(embed=error_embed("Błąd", "Ten kanał nie jest aktywnym ticketem."), ephemeral=True)
            return

        if row["claimed_by"]:
            claimed_user = interaction.guild.get_member(row["claimed_by"])
            name = claimed_user.mention if claimed_user else f"ID: {row['claimed_by']}"
            await interaction.response.send_message(embed=warning_embed("Ticket Przejęty", f"Ten ticket został już przejęty przez {name}."), ephemeral=True)
            return

        await db.execute("UPDATE tickets SET claimed_by = ? WHERE ticket_id = ?", (interaction.user.id, row["ticket_id"]))
        button.disabled = True
        button.label = f"Przejęte przez {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=success_embed("Przejęto Ticket", f"Moderator {interaction.user.mention} przejął obsługę tego zgłoszenia."))

    @discord.ui.button(label="Dodaj użytkownika", style=discord.ButtonStyle.secondary, emoji="👤", custom_id="ticket_add_user")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddUserModal())

    @discord.ui.button(label="Wygeneruj transkrypt", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="ticket_transcript")
    async def generate_transcript(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        messages = [msg async for msg in channel.history(limit=500, oldest_first=True)]
        html_code = await TranscriptService.generate_html(channel, messages)

        file = discord.File(io.BytesIO(html_code.encode("utf-8")), filename=f"transcript-{channel.name}.html")
        await interaction.followup.send(embed=success_embed("Wygenerowano Transkrypt", "Pobierz plik HTML poniżej:"), file=file, ephemeral=True)

    @discord.ui.button(label="Zamknij ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = interaction.client.db
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        await db.execute("UPDATE tickets SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP WHERE channel_id = ?", (channel.id,))

        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and not target.guild_permissions.administrator:
                await channel.set_permissions(target, send_messages=False, read_messages=True)

        await interaction.response.send_message(embed=warning_embed("Ticket Zamknięty", "Zgłoszenie zostało zamknięte przez moderatora. Użyj przycisku poniżej, aby usunąć kanał."))

    @discord.ui.button(label="Usuń ticket", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="ticket_delete")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=warning_embed("Potwierdzenie Usunięcia", "Czy na pewno chcesz nieodwracalnie usunąć ten kanał zgłoszenia?"),
            view=ConfirmDeleteView(),
            ephemeral=True
        )
