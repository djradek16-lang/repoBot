"""
Trwałe widoki (Persistent Views) i nowoczesne szablony embedów do systemu wpłat na konto organizacji.
"""

import logging
import discord

from utils.embeds import create_embed, success_embed, error_embed, info_embed
from utils.helpers import format_number

logger = logging.getLogger("AegisBot.DonationViews")

DONATION_TYPE_LABELS = {
    "dobrowolne": "Dobrowolna",
    "wpisowe": "Wpisowe",
    "za szkody": "Za szkody",
    "wykupienie minusa": "Wykupienie minusa",
}

MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}


def build_review_embed(
    donation_id: int,
    user_id: int,
    amount: int,
    donation_type: str,
    nick: str,
    login: str,
    proof_url: str | None,
    status: str = "pending",
    reviewer: discord.abc.User | None = None,
) -> discord.Embed:
    """Buduje elegancki embed weryfikacji wpłaty dla administracji (panel zarządu)."""
    type_label = DONATION_TYPE_LABELS.get(donation_type, donation_type)

    if status == "approved":
        color = 0x2ECC71  # Szmaragdowa zieleń
        status_line = f"🟢 **Status:** `ZATWIERDZONO` przez {reviewer.mention if reviewer else 'Zarząd'}"
        title = f"💰 Zgłoszenie Wpłaty #{donation_id} — Zatwierdzone"
    elif status == "rejected":
        color = 0xE74C3C  # Czerwień
        status_line = f"🔴 **Status:** `ODRZUCONO` przez {reviewer.mention if reviewer else 'Zarząd'}"
        title = f"💰 Zgłoszenie Wpłaty #{donation_id} — Odrzucone"
    else:
        color = 0xF1C40F  # Złoto
        status_line = "🟡 **Status:** `Oczekuje na weryfikację przez Zarząd`"
        title = f"💰 Nowe Zgłoszenie Wpłaty #{donation_id}"

    description = (
        f"### 💵 Kwota: **{format_number(amount)}$**\n\n"
        f"> 👤 **Wpłacający:** <@{user_id}>\n"
        f"> 🏷️ **Typ wpłaty:** `{type_label}`\n"
        f"> 🎮 **Nick w grze:** `{nick}`\n"
        f"> 🔑 **Login w grze:** `{login}`\n\n"
        f"{status_line}"
    )

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if proof_url:
        embed.set_image(url=proof_url)
    embed.set_footer(text=f"ID zgłoszenia: #{donation_id} • Team77 Weryfikacja")
    return embed


def build_announcement_embed(
    user_id: int,
    amount: int,
    donation_type: str,
    nick: str,
    login: str,
    proof_url: str | None,
    reviewer: discord.abc.User | None,
    guild_name: str = "Team77",
) -> discord.Embed:
    """Buduje nowoczesny, prestiżowy embed publicznego ogłoszenia wpłaty na kanale organizacji."""
    type_label = DONATION_TYPE_LABELS.get(donation_type, donation_type)

    description = (
        f"### 💸 +{format_number(amount)}$ na konto organizacji!\n\n"
        f"Wpłata członka **{nick}** została pomyślnie zweryfikowana.\n\n"
        f"> 👤 **Wpłacający:** <@{user_id}>\n"
        f"> 🏷️ **Rodzaj wpłaty:** `{type_label}`\n"
        f"> 🎮 **Konto w grze:** `{nick}` *(login: `{login}`)*\n\n"
        f"-# 🛡️ Zweryfikowano przez **{reviewer.display_name if reviewer else 'Zarząd'}** • Dziękujemy za wsparcie!"
    )

    embed = discord.Embed(
        title="🎉 Wpłata na Konto Organizacji",
        description=description,
        color=0x2ECC71,
        timestamp=discord.utils.utcnow(),
    )
    if proof_url:
        embed.set_image(url=proof_url)
    embed.set_footer(text=f"{guild_name} • System Wpłat")
    return embed


class DonationReviewView(discord.ui.View):
    """Panel Zatwierdź/Odrzuć pod zgłoszeniem wpłaty. custom_id koduje ID zgłoszenia,
    dzięki czemu widok da się odtworzyć po restarcie bota (patrz cog_load w DonationsCog)."""

    def __init__(self, donation_id: int) -> None:
        super().__init__(timeout=None)
        self.donation_id = donation_id

        self.approve_button = discord.ui.Button(
            label="Zatwierdź",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"donation_approve:{donation_id}",
            row=0,
        )
        self.approve_button.callback = self._make_callback(approve=True)

        self.reject_button = discord.ui.Button(
            label="Odrzuć",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"donation_reject:{donation_id}",
            row=0,
        )
        self.reject_button.callback = self._make_callback(approve=False)

        self.add_item(self.approve_button)
        self.add_item(self.reject_button)

    def _make_callback(self, approve: bool):
        async def callback(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not (
                interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id
            ):
                await interaction.response.send_message(
                    embed=error_embed("Brak Uprawnień", "Tylko administracja może zatwierdzać lub odrzucać wpłaty."),
                    ephemeral=True
                )
                return

            bot = interaction.client
            service = bot.donation_service

            success, message, donation = await service.decide(self.donation_id, interaction.user.id, approve)
            if not success:
                await interaction.response.send_message(embed=error_embed("Nie można wykonać akcji", message), ephemeral=True)
                return

            # Dezaktywacja przycisków i zmiana etykiet na czytelny status
            for item in self.children:
                item.disabled = True
            if approve:
                self.approve_button.label = f"Zatwierdzono ({interaction.user.display_name})"
                self.remove_item(self.reject_button)
            else:
                self.reject_button.label = f"Odrzucono ({interaction.user.display_name})"
                self.remove_item(self.approve_button)

            # Przebuduj embed z jednym, poprawnym statusem (bez powielania pola Status!)
            new_embed = build_review_embed(
                donation_id=self.donation_id,
                user_id=donation["user_id"],
                amount=donation["amount"],
                donation_type=donation["donation_type"],
                nick=donation.get("nick") or "-",
                login=donation.get("login") or "-",
                proof_url=donation.get("proof_url"),
                status="approved" if approve else "rejected",
                reviewer=interaction.user,
            )

            await interaction.response.edit_message(embed=new_embed, view=self)

            donor = interaction.guild.get_member(donation["user_id"])
            donor_mention = donor.mention if donor else f"<@{donation['user_id']}>"

            if approve:
                # Publikacja nowego, prestiżowego ogłoszenia na kanale wpłat
                announcement = build_announcement_embed(
                    user_id=donation["user_id"],
                    amount=donation["amount"],
                    donation_type=donation["donation_type"],
                    nick=donation.get("nick") or "-",
                    login=donation.get("login") or "-",
                    proof_url=donation.get("proof_url"),
                    reviewer=interaction.user,
                    guild_name=interaction.guild.name if interaction.guild else "Team77",
                )

                bot_db = interaction.client.db
                announce_row = await bot_db.fetchone(
                    "SELECT donation_announce_channel_id FROM guild_config WHERE guild_id = ?",
                    (interaction.guild_id,)
                )
                announce_channel = None
                if announce_row and announce_row["donation_announce_channel_id"]:
                    announce_channel = interaction.guild.get_channel(announce_row["donation_announce_channel_id"])
                if not isinstance(announce_channel, discord.TextChannel):
                    announce_channel = interaction.channel

                await announce_channel.send(embed=announcement)

                # Odświeżenie przyklejonej instrukcji na dole kanału
                donations_cog = interaction.client.get_cog("DonationsCog")
                if donations_cog:
                    await donations_cog.refresh_donation_reminder(interaction.guild, announce_channel)

                await interaction.followup.send(
                    embed=success_embed(
                        "Wpłata Zatwierdzona ✅",
                        f"Wpłata {donor_mention} na kwotę **{format_number(donation['amount'])}$** została zatwierdzona i wliczona do rankingu."
                    ),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    embed=error_embed(
                        "Wpłata Odrzucona ❌",
                        f"Zgłoszenie {donor_mention} na kwotę **{format_number(donation['amount'])}$** zostało odrzucone."
                    ),
                    ephemeral=True
                )

        return callback


class DonationReminderView(discord.ui.View):
    """Trwały widok z przyciskami szybkiego dostępu umieszczony pod przyklejoną instrukcją wpłat."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ranking Wpłat",
        style=discord.ButtonStyle.primary,
        emoji="📊",
        custom_id="donation_reminder:ranking",
        row=0,
    )
    async def ranking_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        service = interaction.client.donation_service
        leaderboard = await service.get_leaderboard(interaction.guild_id, donation_type="dobrowolne", limit=10)
        if not leaderboard:
            await interaction.response.send_message(
                embed=info_embed("🏆 Ranking Wpłat Dobrowolnych", "Nikt jeszcze nie posiada zatwierdzonej wpłaty dobrowolnej."),
                ephemeral=True
            )
            return

        lines = []
        for i, entry in enumerate(leaderboard):
            prefix = MEDALS.get(i, f"`#{i + 1}`")
            lines.append(f"{prefix} <@{entry['user_id']}> — **{format_number(entry['total'])}$**")

        embed = create_embed(
            title="🏆 Ranking Dobrowolnych Wplat • TOP 10",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text="Liczą się wyłącznie zatwierdzone wpłaty dobrowolne na konto organizacji.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Moje Wpłaty",
        style=discord.ButtonStyle.secondary,
        emoji="💰",
        custom_id="donation_reminder:my_donations",
        row=0,
    )
    async def my_donations_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        service = interaction.client.donation_service
        total = await service.get_user_total(interaction.guild_id, interaction.user.id, "dobrowolne")
        await interaction.response.send_message(
            embed=info_embed(
                "💰 Twoje Wpłaty Dobrowolne",
                f"Łączna suma Twoich zatwierdzonych wpłat dobrowolnych wynosi:\n### 💵 **{format_number(total)}$**"
            ),
            ephemeral=True
        )