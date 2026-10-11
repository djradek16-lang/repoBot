"""
Moduł wpłat członków na konto organizacji (w grze) i rankingu honorowych dawców.
Zgłoszenie od gracza (z dowodem w postaci screena) trafia na kanał weryfikacji,
gdzie admin zatwierdza/odrzuca je jednym kliknięciem - dopiero wtedy liczy się
do rankingu. Do rankingu wliczane są WYŁĄCZNIE zatwierdzone wpłaty typu "dobrowolne".
Dodatkowo administracja ma komendy do ręcznej korekty (dodania/usunięcia) na wypadek
pomyłek, sporów albo fałszywych zgłoszeń.
"""

import logging
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from services.donation_service import DonationService
from views.donation_views import DonationReviewView, DonationReminderView, build_review_embed
from utils.embeds import create_embed, success_embed, error_embed, info_embed
from utils.helpers import format_number

logger = logging.getLogger("AegisBot.Cogs.Donations")

DONATION_TYPE_CHOICES = [
    app_commands.Choice(name="Dobrowolna", value="dobrowolne"),
    app_commands.Choice(name="wpisowe", value="wpisowe"),
    app_commands.Choice(name="za szkody", value="za szkody"),
    app_commands.Choice(name="wykupienie minusa", value="wykupienie minusa"),
]

DONATION_TYPE_LABELS = {
    "dobrowolne": "Dobrowolna",
    "wpisowe": "Wpisowe",
    "za szkody": "Za szkody",
    "wykupienie minusa": "Wykupienie minusa",
}

MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}


class DonationsCog(commands.Cog):
    """Zgłaszanie, weryfikacja i ranking wpłat na konto organizacji."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.donation_service = DonationService(bot.db)
        # Widok w donation_views.py sięga po serwis przez bot.donation_service,
        # bo widoki trwałe (persistent views) nie mają bezpośredniego dostępu do Coga.
        self.bot.donation_service = self.donation_service
        # Blokada per-serwer, żeby usuwanie starego przypomnienia + wysyłanie nowego
        # nie nakładało się z on_message wywołanym przez tę SAMĄ, właśnie wysłaną wiadomość
        # (bez tego powstaje nieskończona pętla wysyłania/kasowania).
        self._reminder_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """Odtwarza panele Zatwierdź/Odrzuć oraz przyciski instrukcji po restarcie bota."""
        self.bot.add_view(DonationReminderView())
        pending = await self.donation_service.get_all_pending()
        for donation in pending:
            self.bot.add_view(DonationReviewView(donation["donation_id"]))
        if pending:
            logger.info(f"Odtworzono {len(pending)} trwałych widoków zatwierdzania wpłat po restarcie.")

    async def _get_review_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        row = await self.bot.db.fetchone(
            "SELECT donation_review_channel_id FROM guild_config WHERE guild_id = ?",
            (guild.id,)
        )
        if not row or not row["donation_review_channel_id"]:
            return None
        channel = guild.get_channel(row["donation_review_channel_id"])
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _get_announce_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        row = await self.bot.db.fetchone(
            "SELECT donation_announce_channel_id FROM guild_config WHERE guild_id = ?",
            (guild.id,)
        )
        if not row or not row["donation_announce_channel_id"]:
            return None
        channel = guild.get_channel(row["donation_announce_channel_id"])
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _get_reminder_message_id(self, guild_id: int) -> int | None:
        row = await self.bot.db.fetchone(
            "SELECT donation_reminder_message_id FROM guild_config WHERE guild_id = ?",
            (guild_id,)
        )
        return row["donation_reminder_message_id"] if row and row["donation_reminder_message_id"] else None

    async def _set_reminder_message_id(self, guild_id: int, message_id: int | None) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, donation_reminder_message_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET donation_reminder_message_id = ?
            """,
            (guild_id, message_id, message_id)
        )

    def _build_reminder_embed(self, guild: discord.Guild) -> discord.Embed:
        """Buduje nowoczesny embed z instrukcją systemu wpłat 'przyklejany' na dole kanału wpłat."""
        embed = discord.Embed(
            title="💳 System Wpłat na Konto Organizacji",
            description=(
                "Wpłaty w bankomacie serwerowym zgłaszamy komendą:\n"
                "### 🔹 `/wplata zgloszenie`\n\n"
                "> W formularzu podaj kwotę, rodzaj wpłaty, swój nick i login oraz załącz zrzut ekranu z gry.\n\n"
                "📋 **Zasady poprawnego dowodu wpłaty:**\n"
                "• Zrzut ekranu **musi** zawierać widoczną **datę i godzinę** wpłaty z HUD.\n"
                "• Musi być widoczny **aktualny stan konta** organizacji po transakcji.\n"
                "• Zrzut ekranu nie może być ucięty ani zasłonięty."
            ),
            color=0xF1C40F,
        )
        embed.add_field(
            name="📊 Ranking Wpłat",
            value="`/wplata ranking`\n*TOP 10 dobrowolnych wpłat*",
            inline=True,
        )
        embed.add_field(
            name="💰 Twoje Wpłaty",
            value="`/wplata moje`\n*Suma Twoich wpłat dobrowolnych*",
            inline=True,
        )
        embed.set_footer(
            text=f"{guild.name} • System Wpłat",
            icon_url=guild.icon.url if guild.icon else None,
        )
        return embed

    def _get_reminder_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._reminder_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._reminder_locks[guild_id] = lock
        return lock

    async def refresh_donation_reminder(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        """Usuwa poprzednią instrukcję systemu wpłat (jeśli była) i wysyła nową na sam dół kanału.
        Wywoływane WYŁĄCZNIE bezpośrednio z DonationReviewView po wysłaniu ogłoszenia o zatwierdzonej
        wpłacie - nie nasłuchujemy na wszystkie wiadomości, żeby zwykły czat na kanale nie odpalał
        tego za każdym razem (i żeby uniknąć pętli wywołań własnych wiadomości bota)."""
        async with self._get_reminder_lock(guild.id):
            current_reminder_id = await self._get_reminder_message_id(guild.id)
            if current_reminder_id:
                try:
                    old_message = await channel.fetch_message(current_reminder_id)
                    await old_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            try:
                new_message = await channel.send(
                    embed=self._build_reminder_embed(guild),
                    view=DonationReminderView(),
                )
            except discord.HTTPException as e:
                logger.warning(f"Nie udało się wysłać przypomnienia o systemie wpłat: {e}")
                return

            await self._set_reminder_message_id(guild.id, new_message.id)

    @commands.Cog.listener("on_message")
    async def on_donation_channel_message(self, message: discord.Message) -> None:
        """Kanał ogłoszeń wpłat ma być 'czysty' - tylko wpłaty i instrukcja od bota.
        Zwykłe wiadomości od członków są od razu kasowane."""
        if message.guild is None or message.author.bot:
            return

        announce_channel = await self._get_announce_channel(message.guild)
        if not announce_channel or message.channel.id != announce_channel.id:
            return

        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Nie udało się skasować wiadomości na kanale wpłat ({message.channel.id}): {e}")

    wplata_group = app_commands.Group(name="wplata", description="System wpłat na konto organizacji i ranking ")

    # Osobna grupa TYLKO dla komend administracyjnych. Trzeba je trzymać w innej
    # grupie niż `wplata_group`, bo Discord ustawia widoczność/uprawnienia
    # (default_permissions) na poziomie całej grupy/komendy najwyższego poziomu,
    # a nie pojedynczych subkomend - inaczej "kanal", "dodaj" itd. i tak byłyby
    # widoczne pod /wplata dla zwykłych graczy, nawet jeśli @is_administrator()
    # blokowałby ich faktyczne użycie w praktyce.
    wplata_admin_group = app_commands.Group(
        name="wplataadmin",
        description="Zarządzanie systemem wpłat na konto organizacji (tylko administracja)",
        default_permissions=discord.Permissions(administrator=True)
    )

    @wplata_group.command(name="zgloszenie", description="Zgłoś wpłatę dokonaną w grze na konto organizacji.")
    @app_commands.describe(
        kwota="Kwota, którą wpłaciłeś na konto organizacyjne w grze",
        typ="Czy to była wpłata dobrowolna, wpisowe, za szkody, wykupienie minusa",
        nick="Twój aktualny nick w grze",
        login="Twój login do konta w grze",
        dowod="Screenshot z bankomatu potwierdzający wpłatę"
    )
    @app_commands.choices(typ=DONATION_TYPE_CHOICES)
    async def wplata_zgloszenie(
        self,
        interaction: discord.Interaction,
        kwota: app_commands.Range[int, 1],
        typ: str,
        nick: str,
        login: str,
        dowod: discord.Attachment
    ) -> None:
        if not dowod.content_type or not dowod.content_type.startswith("image/"):
            await interaction.response.send_message(
                embed=error_embed("Zły Format Dowodu", "Dowód wpłaty musi być zrzutem ekranu (obrazem)."),
                ephemeral=True
            )
            return

        review_channel = await self._get_review_channel(interaction.guild)
        if not review_channel:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kanał Weryfikacji Nieustawiony",
                    "Administracja nie skonfigurowała jeszcze kanału weryfikacji wpłat. Użyj `/wplata kanal`, aby to naprawić."
                ),
                ephemeral=True
            )
            return

        donation_id = await self.donation_service.submit(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            amount=kwota,
            donation_type=typ,
            proof_url=dowod.url,
            nick=nick,
            login=login
        )

        embed = build_review_embed(
            donation_id=donation_id,
            user_id=interaction.user.id,
            amount=kwota,
            donation_type=typ,
            nick=nick,
            login=login,
            proof_url=dowod.url,
            status="pending",
        )

        view = DonationReviewView(donation_id)
        review_message = await review_channel.send(embed=embed, view=view)
        await self.donation_service.set_review_message(donation_id, review_message.id)

        await interaction.response.send_message(
            embed=success_embed(
                "Zgłoszenie Wysłane ✅",
                f"Twoja wpłata **{format_number(kwota)}$** została przekazana do weryfikacji przez zarząd organizacji."
            ),
            ephemeral=True
        )

    @wplata_group.command(name="ranking", description="Pokazuje ranking honorowych dawców (tylko zatwierdzone, dobrowolne wpłaty).")
    async def wplata_ranking(self, interaction: discord.Interaction) -> None:
        leaderboard = await self.donation_service.get_leaderboard(interaction.guild_id, donation_type="dobrowolne", limit=10)

        if not leaderboard:
            await interaction.response.send_message(
                embed=info_embed("🏆 Ranking Wpłat Dobrowolnych", "Nikt jeszcze nie ma zatwierdzonej dobrowolnej wpłaty.")
            )
            return

        lines = []
        for i, entry in enumerate(leaderboard):
            prefix = MEDALS.get(i, f"`#{i + 1}`")
            lines.append(f"{prefix} <@{entry['user_id']}> — **{format_number(entry['total'])}$**")

        embed = create_embed(
            title="🏆 Ranking Honorowych Dawców • TOP 10",
            description="\n".join(lines),
            color=0xF1C40F
        )
        embed.set_footer(text="Liczą się wyłącznie zatwierdzone, dobrowolne wpłaty na konto organizacji.")
        await interaction.response.send_message(embed=embed)

    @wplata_group.command(name="moje", description="Pokazuje Twoją łączną sumę zatwierdzonych dobrowolnych wpłat.")
    async def wplata_moje(self, interaction: discord.Interaction) -> None:
        total = await self.donation_service.get_user_total(interaction.guild_id, interaction.user.id, "dobrowolne")
        embed = discord.Embed(
            title="💰 Twoje Wpłaty na Konto Organizacji",
            description=f"Łączna suma Twoich zatwierdzonych **dobrowolnych** wpłat wynosi:\n### 💵 **{format_number(total)}$**",
            color=0x2ECC71,
        )
        embed.set_footer(text=f"{interaction.guild.name if interaction.guild else 'Team77'} • Dziękujemy za wsparcie!")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @wplata_admin_group.command(name="kanal", description="Ustawia kanał, na który trafiają zgłoszenia wpłat do weryfikacji.")
    @app_commands.describe(channel="Kanał tekstowy dla zgłoszeń wpłat")
    @is_administrator()
    async def wplata_kanal(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, donation_review_channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET donation_review_channel_id = ?
            """,
            (interaction.guild_id, channel.id, channel.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Zaktualizowano Ustawienia", f"Zgłoszenia wpłat będą teraz trafiać na kanał {channel.mention}.")
        )

    @wplata_admin_group.command(name="kanal_wplat", description="Ustawia kanał, na który trafiają publiczne ogłoszenia o zatwierdzonych wpłatach.")
    @app_commands.describe(channel="Kanał tekstowy dla ogłoszeń o wpłatach członków")
    @is_administrator()
    async def wplata_kanal_wplat(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, donation_announce_channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET donation_announce_channel_id = ?
            """,
            (interaction.guild_id, channel.id, channel.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Zaktualizowano Ustawienia", f"Ogłoszenia o zatwierdzonych wpłatach będą teraz publikowane na kanale {channel.mention}.")
        )

    @wplata_admin_group.command(name="dodaj", description="Ręcznie dopisuje kwotę do konta wpłat użytkownika (korekta).")
    @app_commands.describe(
        uzytkownik="Użytkownik, któremu dopisujesz kwotę",
        kwota="Kwota do dopisania",
        typ="Typ wpłaty (dobrowolne liczy się do rankingu)",
        powod="Powód korekty (np. pominięte zgłoszenie, pomyłka w wcześniejszej weryfikacji)"
    )
    @app_commands.choices(typ=DONATION_TYPE_CHOICES)
    @is_administrator()
    async def wplata_dodaj(
        self,
        interaction: discord.Interaction,
        uzytkownik: discord.Member,
        kwota: app_commands.Range[int, 1],
        typ: str,
        powod: str
    ) -> None:
        await self.donation_service.admin_adjust(
            guild_id=interaction.guild_id,
            user_id=uzytkownik.id,
            amount=kwota,
            donation_type=typ,
            admin_id=interaction.user.id,
            reason=powod,
            remove=False
        )
        new_total = await self.donation_service.get_user_total(interaction.guild_id, uzytkownik.id, typ)
        await interaction.response.send_message(
            embed=success_embed(
                "Dopisano Kwotę",
                f"Dopisano **{format_number(kwota)}$** ({DONATION_TYPE_LABELS[typ]}) do konta {uzytkownik.mention}.\n"
                f"Powód: {powod}\nNowa suma ({DONATION_TYPE_LABELS[typ]}): **{format_number(new_total)}$**"
            )
        )

    @wplata_admin_group.command(name="usun", description="Ręcznie odejmuje kwotę z konta wpłat użytkownika (korekta).")
    @app_commands.describe(
        uzytkownik="Użytkownik, któremu odejmujesz kwotę",
        kwota="Kwota do odjęcia",
        typ="Z którego typu wpłat odjąć kwotę",
        powod="Powód korekty (np. fałszywe zgłoszenie, pomyłkowe zatwierdzenie, spór)"
    )
    @app_commands.choices(typ=DONATION_TYPE_CHOICES)
    @is_administrator()
    async def wplata_usun(
        self,
        interaction: discord.Interaction,
        uzytkownik: discord.Member,
        kwota: app_commands.Range[int, 1],
        typ: str,
        powod: str
    ) -> None:
        await self.donation_service.admin_adjust(
            guild_id=interaction.guild_id,
            user_id=uzytkownik.id,
            amount=kwota,
            donation_type=typ,
            admin_id=interaction.user.id,
            reason=powod,
            remove=True
        )
        new_total = await self.donation_service.get_user_total(interaction.guild_id, uzytkownik.id, typ)
        await interaction.response.send_message(
            embed=success_embed(
                "Odjęto Kwotę",
                f"Odjęto **{format_number(kwota)}$** ({DONATION_TYPE_LABELS[typ]}) z konta {uzytkownik.mention}.\n"
                f"Powód: {powod}\nNowa suma ({DONATION_TYPE_LABELS[typ]}): **{format_number(new_total)}$**"
            )
        )


async def setup(bot: commands.Bot) -> None:
    bot.add_view(DonationReminderView())
    await bot.add_cog(DonationsCog(bot))