"""
TESTOWA wersja modułu aplikacji rekrutacyjnych (osobny cog od applications.py).

Zgłoszenie składa się teraz przez formularz GUI (Discord Modal), a nie przez
wpisanie wzoru na czacie — patrz views/application_views_test.py
(ApplicationStartButtonTest, ApplicationModalPart1Test/Part2Test). Ten plik
odpowiada już tylko za:
1) migrację TESTOWYCH kolumn konfiguracyjnych w guild_config (prefiks "test_"),
2) rejestrację trwałych widoków po restarcie bota (przycisk startowy formularza
   + przyciski Akceptuj/Odrzuć na kanale przeglądu).

Cała logika przetwarzania zgłoszenia (weryfikacja forum, budowanie embedów,
wysyłka na kanał przeglądu, DM z potwierdzeniem) mieszka teraz w
views/application_views_test.py, bo to tam trafia interakcja z modala —
nie ma już surowej wiadomości na kanale ani listenera on_message do
przetworzenia, więc nie trzeba jej usuwać z kanału.

Przycisk startowy wysyła się na kanał zgłoszeń komendą /config-test post_button
(patrz cogs/administration_test.py).
"""

import logging
import sqlite3

from discord.ext import commands

from views.application_views_test import ApplicationTestDecisionButton, ApplicationStartButtonTest

logger = logging.getLogger("AegisBot.Cogs.ApplicationsTest")


class ApplicationsTestCog(commands.Cog):
    """Rejestracja trwałych widoków testowego modułu aplikacji rekrutacyjnych (formularz GUI)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Migracja: dodaj TESTOWE kolumny konfiguracyjne do istniejącej tabeli guild_config,
        # jeśli ich brak. Prefiks "test_" celowo odróżnia je od kolumn produkcyjnych
        # (applications_channel_id itd.), żeby oba moduły mogły działać niezależnie
        # na tym samym serwerze bez wzajemnego nadpisywania configu.
        for column in (
            "test_applications_channel_id",
            "test_applications_review_channel_id",
            "test_applications_management_role_id",
            "test_applications_accept_role_id",
        ):
            try:
                await self.bot.db.execute(f"ALTER TABLE guild_config ADD COLUMN {column} INTEGER")
            except sqlite3.OperationalError:
                pass  # Kolumna już istnieje.

        # Rejestracja trwałych widoków (przetrwają restart bota):
        # - przycisk startowy formularza (statyczny custom_id, zwykły persistent View),
        # - przyciski Akceptuj/Odrzuć na kanale przeglądu (dynamiczne, kodują ID zgłaszającego).
        self.bot.add_view(ApplicationStartButtonTest())
        self.bot.add_dynamic_items(ApplicationTestDecisionButton)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationsTestCog(bot))
