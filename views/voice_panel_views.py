"""
Trwały widok panelu zarządzania kanałem, wysyłany na czacie tekstowym
każdego kanału utworzonego przez system Join to Create.

Panel jest jeden i ten sam dla wszystkich kanałów tymczasowych (custom_id
przycisków jest stały) — kontekst (o który kanał chodzi) bot ustala zawsze
na podstawie kanału, na którym doszło do interakcji (interaction.channel),
dzięki czemu widok działa poprawnie nawet po restarcie bota.
"""

import discord

from utils.embeds import success_embed, error_embed, info_embed
from views.confirmation import ConfirmView

# Uprawnienia nadawane właścicielowi kanału - zdefiniowane też w cogs/join_to_create.py.
# Trzymane tutaj osobno, żeby uniknąć zależności cyklicznej cog <-> view.
OWNER_PERMISSION_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    connect=True,
    speak=True,
    stream=True,
    use_voice_activation=True,
    priority_speaker=True,
    move_members=True,
    mute_members=True,
    deafen_members=True,
    manage_channels=True,
    manage_permissions=True,
    send_messages=True,
    manage_messages=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    add_reactions=True,
)


async def _get_channel_record(interaction: discord.Interaction) -> dict | None:
    """Pobiera wpis Join to Create dla kanału, na którym doszło do interakcji."""
    return await interaction.client.db.fetchone(
        "SELECT * FROM jtc_channels WHERE channel_id = ?", (interaction.channel_id,)
    )


class RenameChannelModal(discord.ui.Modal, title="Zmień nazwę kanału"):
    """Formularz zmiany nazwy tymczasowego kanału głosowego."""

    nowa_nazwa = discord.ui.TextInput(
        label="Nowa nazwa kanału",
        style=discord.TextStyle.short,
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        try:
            await channel.edit(name=str(self.nowa_nazwa), reason=f"Join to Create: zmiana nazwy przez {interaction.user}.")
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie udało się zmienić nazwy kanału: {e}"), ephemeral=True)
            return

        await interaction.response.send_message(embed=success_embed("Zmieniono Nazwę", f"Nowa nazwa kanału: **{self.nowa_nazwa}**."), ephemeral=True)


class ChannelLimitModal(discord.ui.Modal, title="Limit użytkowników"):
    """Formularz ustawiania limitu osób na tymczasowym kanale głosowym."""

    limit = discord.ui.TextInput(
        label="Limit osób (0 = brak limitu)",
        style=discord.TextStyle.short,
        placeholder="np. 5",
        required=True,
        max_length=2
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.limit).strip()
        if not raw.isdigit() or not (0 <= int(raw) <= 99):
            await interaction.response.send_message(embed=error_embed("Błędna Wartość", "Podaj liczbę od 0 do 99."), ephemeral=True)
            return

        new_limit = int(raw)
        channel = interaction.channel
        try:
            await channel.edit(user_limit=new_limit, reason=f"Join to Create: zmiana limitu przez {interaction.user}.")
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie udało się ustawić limitu: {e}"), ephemeral=True)
            return

        opis = f"Nowy limit: **{new_limit}** osób." if new_limit else "Limit osób został usunięty (brak limitu)."
        await interaction.response.send_message(embed=success_embed("Zaktualizowano Limit", opis), ephemeral=True)


class TransferOwnerSelectView(discord.ui.View):
    """Krótkotrwały widok z selektorem użytkownika do przekazania własności kanału."""

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Błąd", "Ta interakcja należy do innej osoby."), ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Wybierz nowego właściciela...")
    async def select_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        channel = interaction.channel
        new_owner = select.values[0]

        if new_owner.bot:
            await interaction.response.send_message(embed=error_embed("Błąd", "Nie możesz przekazać własności kanału botowi."), ephemeral=True)
            return

        member_in_channel = channel.guild.get_member(new_owner.id)
        if member_in_channel is None or member_in_channel not in channel.members:
            await interaction.response.send_message(embed=error_embed("Błąd", "Ta osoba musi znajdować się na tym kanale, aby przejąć nad nim własność."), ephemeral=True)
            return

        row = await _get_channel_record(interaction)
        if row is None:
            await interaction.response.send_message(embed=error_embed("Błąd", "Nie znaleziono danych tego kanału."), ephemeral=True)
            return

        old_owner = channel.guild.get_member(row["owner_id"])

        try:
            if old_owner is not None:
                await channel.set_permissions(old_owner, overwrite=None, reason="Join to Create: przekazanie własności (reset uprawnień poprzedniego właściciela).")
            await channel.set_permissions(member_in_channel, overwrite=OWNER_PERMISSION_OVERWRITE, reason="Join to Create: przekazanie własności.")
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie udało się zaktualizować uprawnień: {e}"), ephemeral=True)
            return

        await interaction.client.db.execute(
            "UPDATE jtc_channels SET owner_id = ? WHERE channel_id = ?", (new_owner.id, channel.id)
        )

        self.stop()
        await interaction.response.edit_message(
            embed=success_embed("Przekazano Własność", f"{new_owner.mention} jest teraz właścicielem tego kanału."),
            view=None
        )
        await channel.send(embed=info_embed("Zmiana Właściciela", f"{new_owner.mention} otrzymał(a) pełne uprawnienia zarządzania tym kanałem."))


class KickMemberSelectView(discord.ui.View):
    """Krótkotrwały widok z selektorem użytkownika do wyrzucenia z kanału."""

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Błąd", "Ta interakcja należy do innej osoby."), ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Wybierz osobę do wyrzucenia...")
    async def select_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        channel = interaction.channel
        target = select.values[0]
        member = channel.guild.get_member(target.id)

        if member is None or member not in channel.members:
            await interaction.response.send_message(embed=error_embed("Błąd", "Ta osoba nie znajduje się na tym kanale."), ephemeral=True)
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message(embed=error_embed("Błąd", "Nie możesz wyrzucić samego siebie."), ephemeral=True)
            return

        try:
            await member.move_to(None, reason=f"Join to Create: wyrzucony przez właściciela kanału ({interaction.user}).")
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie udało się rozłączyć użytkownika: {e}"), ephemeral=True)
            return

        self.stop()
        await interaction.response.edit_message(
            embed=success_embed("Wyrzucono Użytkownika", f"{member.mention} został rozłączony z kanału."),
            view=None
        )


class VoicePanelView(discord.ui.View):
    """Trwały panel zarządzania tymczasowym kanałem głosowym Join to Create."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = await _get_channel_record(interaction)
        if row is None:
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Ten panel nie jest już powiązany z żadnym aktywnym kanałem."), ephemeral=True
            )
            return False

        is_owner = interaction.user.id == row["owner_id"]
        is_admin = isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.administrator
            or interaction.user.id == interaction.guild.owner_id
        )

        if not (is_owner or is_admin):
            await interaction.response.send_message(
                embed=error_embed("Brak Uprawnień", "Tylko właściciel tego kanału (lub administrator serwera) może z niego korzystać."),
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(label="Zablokuj / Odblokuj", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="jtc_panel:lock", row=0)
    async def toggle_lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel: discord.VoiceChannel = interaction.channel
        everyone = interaction.guild.default_role
        current = channel.overwrites_for(everyone)

        try:
            if current.connect is False:
                await channel.set_permissions(everyone, connect=None, reason=f"Join to Create: odblokowano ({interaction.user}).")
                await interaction.response.send_message(embed=success_embed("Odblokowano Kanał", "Każdy może teraz dołączyć do kanału."), ephemeral=True)
            else:
                await channel.set_permissions(everyone, connect=False, reason=f"Join to Create: zablokowano ({interaction.user}).")
                await interaction.response.send_message(embed=success_embed("Zablokowano Kanał", "Tylko zaproszone osoby (i Ty) mogą teraz dołączyć."), ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie udało się zmienić dostępu do kanału: {e}"), ephemeral=True)

    @discord.ui.button(label="Zmień nazwę", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="jtc_panel:rename", row=0)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(RenameChannelModal())

    @discord.ui.button(label="Limit osób", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="jtc_panel:limit", row=0)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ChannelLimitModal())

    @discord.ui.button(label="Przekaż własność", emoji="👑", style=discord.ButtonStyle.secondary, custom_id="jtc_panel:transfer", row=1)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=info_embed("Przekazanie Własności", "Wybierz z listy poniżej osobę, która ma zostać nowym właścicielem kanału."),
            view=TransferOwnerSelectView(author_id=interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Wyrzuć użytkownika", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="jtc_panel:kick", row=1)
    async def kick(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=info_embed("Wyrzucenie Użytkownika", "Wybierz z listy poniżej osobę, którą chcesz rozłączyć z kanału."),
            view=KickMemberSelectView(author_id=interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Usuń kanał", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="jtc_panel:delete", row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        confirm_view = ConfirmView(author_id=interaction.user.id, timeout=30.0)
        await interaction.response.send_message(
            embed=info_embed("Potwierdzenie", "Czy na pewno chcesz usunąć ten kanał? Wszystkie osoby zostaną rozłączone."),
            view=confirm_view,
            ephemeral=True
        )
        await confirm_view.wait()

        if not confirm_view.value:
            await interaction.edit_original_response(embed=info_embed("Anulowano", "Kanał nie został usunięty."), view=None)
            return

        channel = interaction.channel
        try:
            await channel.delete(reason=f"Join to Create: usunięty ręcznie przez {interaction.user}.")
        except discord.HTTPException as e:
            await interaction.edit_original_response(embed=error_embed("Błąd", f"Nie udało się usunąć kanału: {e}"), view=None)
            return

        await interaction.client.db.execute("DELETE FROM jtc_channels WHERE channel_id = ?", (channel.id,))
        await interaction.edit_original_response(embed=success_embed("Usunięto Kanał", "Kanał został usunięty."), view=None)
