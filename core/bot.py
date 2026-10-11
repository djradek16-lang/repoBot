"""
Główny klasa bota dziedzicząca po commands.Bot.
Obsługuje cykl życia bota, ładowanie rozszerzeń Cog, bazę danych, synchronizację komend
oraz połączenie z węzłem Lavalink (wavelink).
"""

import logging
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import tasks
from discord.ext import commands
import wavelink

import config
from core.database import Database

logger = logging.getLogger("AegisBot.Core")

class AegisBot(commands.Bot):
    """
    Zaawansowany bot Discord z architekturą rozszerzeń Cog i obsługą slash commands.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        intents.guilds = True
        intents.reactions = True
        intents.voice_states = True

        allowed_mentions = discord.AllowedMentions(
            roles=True,
            everyone=True,
            users=True,
            replied_user=True
        )

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=allowed_mentions,
            help_command=None
        )

        self.db = Database(config.DB_PATH)
        self.owner_id_val = config.OWNER_ID
        self.test_guild_id = config.TEST_GUILD_ID

    async def setup_hook(self) -> None:
        """Inicjalizacja bazy danych, ładowanie Cogów, połączenie z Lavalink i synchronizacja komend slash."""
        logger.info("Nawiązywanie połączenia z bazą danych...")
        await self.db.connect()

        # Rejestracja globalnego handler'a błędów drzewa komend slash
        self.tree.on_error = self.on_app_command_error
        self.tree.interaction_check = self.global_interaction_check

        # --- Połączenie z węzłem Lavalink ---
        await self._connect_lavalink()

        # Ładowanie wszystkich modułów z katalogu cogs/
        cogs_dir = config.BASE_DIR / "cogs"
        if cogs_dir.exists():
            for filepath in cogs_dir.glob("*.py"):
                if filepath.name.startswith("_") or filepath.name.endswith("_test.py") or filepath.name.startswith("test_"):
                    continue
                cog_name = f"cogs.{filepath.stem}"
                try:
                    await self.load_extension(cog_name)
                    logger.info(f"Pomyślnie załadowano Cog: {cog_name}")
                except Exception as e:
                    logger.error(f"Błąd podczas ładowania Coga {cog_name}: {e}", exc_info=True)

        # Synchronizacja slash commands
        # ZAWSZE synchronizujemy globalnie - inaczej stare globalne komendy (widoczne
        # u użytkowników z poprzedniej synchronizacji) wywołują "CommandNotFound" gdy
        # bot rejestruje je tylko dla konkretnej gildii.
        logger.info("Rejestracja i synchronizacja komend slash z Discord API (globalnie)...")
        try:
            synced = await self.tree.sync()
            logger.info(f"Zsynchronizowano {len(synced)} komend slash globalnie.")
        except Exception as e:
            logger.error(f"Błąd podczas globalnej synchronizacji komend slash: {e}", exc_info=True)

        # Jeśli ustawiono TEST_GUILD_ID - synchronizuj DODATKOWO dla gildii testowej
        # (komendy gildyjne odświeżają się natychmiastowo, bez 1h opóźnienia globalnego).
        if self.test_guild_id:
            try:
                guild_obj = discord.Object(id=self.test_guild_id)
                self.tree.copy_global_to(guild=guild_obj)
                synced_guild = await self.tree.sync(guild=guild_obj)
                logger.info(f"Zsynchronizowano {len(synced_guild)} komend slash dla serwera testowego ({self.test_guild_id}).")
            except Exception as e:
                logger.error(f"Błąd podczas synchronizacji komend slash dla gildii testowej: {e}", exc_info=True)

    async def _connect_lavalink(self) -> None:
        """Łączy bota z węzłem Lavalink skonfigurowanym w .env (LAVALINK_HOST/PORT/PASSWORD)."""
        if not config.LAVALINK_HOST or not config.LAVALINK_PASSWORD:
            logger.warning(
                "Brak konfiguracji Lavalink (LAVALINK_HOST / LAVALINK_PASSWORD w .env) — "
                "moduł muzyczny nie będzie działał, dopóki nie uzupełnisz tych zmiennych."
            )
            return

        scheme = "https" if config.LAVALINK_SECURE else "http"
        uri = f"{scheme}://{config.LAVALINK_HOST}:{config.LAVALINK_PORT}"

        node = wavelink.Node(
            uri=uri,
            password=config.LAVALINK_PASSWORD,
            identifier=config.LAVALINK_IDENTIFIER,
        )

        try:
            await wavelink.Pool.connect(client=self, nodes=[node])
            logger.info(f"Inicjalizacja połączenia z węzłem Lavalink: {uri} (poczekaj na 'NodeReady' w logach).")
        except Exception as e:
            logger.error(f"Nie udało się nawiązać połączenia z węzłem Lavalink ({uri}): {e}", exc_info=True)

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        """Wywoływane, gdy węzeł Lavalink faktycznie potwierdzi gotowość (handshake zakończony)."""
        logger.info(
            f"Węzeł Lavalink '{payload.node.identifier}' jest GOTOWY. "
            f"Sesja: {payload.session_id} | Resumed: {payload.resumed}"
        )

    STATUSES: list[tuple] = [
        (discord.ActivityType.watching,   "Team77 w ❤️"),
    ]
    _status_index: int = 0

    async def on_ready(self) -> None:
        """Procedura po zalogowaniu i nawiązaniu połączenia z Gateway Discorda."""
        user_name = self.user.name if self.user else "AegisBot"
        user_id = self.user.id if self.user else 0
        logger.info(f"==================================================")
        logger.info(f" Zalogowano jako: {user_name} (ID: {user_id})")
        logger.info(f" Liczba połączonych serwerów: {len(self.guilds)}")
        logger.info(f" Wersja discord.py: {discord.__version__}")
        logger.info(f" Status: Bot is ready and running! [Ready]")
        logger.info(f"==================================================")
        
        # Wypisanie bezpośrednio na stdout dla panelu Pterodactyl / Gamehost (Startup Done Trigger)
        print(f"Bot is ready! Logged in as {user_name}. Started successfully. [Done]", flush=True)

        if not self.rotate_status.is_running():
            self.rotate_status.start()

    @tasks.loop(seconds=30)
    async def rotate_status(self) -> None:
        """Co 30 sekund zmienia status bota na kolejny z listy, dynamicznie przeliczając graczy i serwery."""
        if not self.guilds:
            return

        total_members = sum(g.member_count or len(g.members) for g in self.guilds)
        total_guilds = len(self.guilds)

        activity_type, raw_name = self.STATUSES[self._status_index % len(self.STATUSES)]
        try:
            name = raw_name.format(
                guilds=total_guilds,
                members=total_members,
                players=total_members,
                users=total_members
            )
        except Exception:
            name = raw_name

        activity = discord.Activity(type=activity_type, name=name)
        await self.change_presence(status=discord.Status.online, activity=activity)
        self.__class__._status_index += 1

    @rotate_status.before_loop
    async def before_rotate_status(self) -> None:
        await self.wait_until_ready()

    # Nazwy komend muzycznych (dostępne dla wszystkich użytkowników)
    MUSIC_COMMANDS: frozenset[str] = frozenset({
        "play", "skip", "stop", "pause", "resume", "queue", "volume",
        "nowplaying", "np", "loop", "shuffle", "clearqueue", "remove", "skipto",
        "radio", "projectfm", "audycja"
    })

    # Nazwy Cogów, których WSZYSTKIE komendy są publiczne (dostępne dla każdego),
    # niezależnie od tego, jak się nazywają - wystarczy dopisać tu nazwę Coga.
    PUBLIC_COGS: frozenset[str] = frozenset({"MusicCog"})

    # Pojedyncze komendy publiczne spoza powyższych Cogów (np. /stats) - dostępne
    # dla każdego, nawet jeśli reszta komend w ich Cogu zostaje zablokowana.
    PUBLIC_COMMANDS: frozenset[str] = frozenset({
        "stats",
        # System wpłat na konto organizacji (DonationsCog) - zgłaszanie, ranking
        # i podgląd własnej sumy mają być dostępne dla wszystkich graczy.
        # Komendy admina tego Coga (kanal, kanal_wplat, dodaj, usun) NIE są tu
        # wymienione i nadal wymagają uprawnień administratora.
        "zgloszenie", "ranking", "moje",
        # System Diamentów - odbiór dziennego diamentu i ranking są publiczne;
        # komendy zarządzania (/diamentadmin) zostają zablokowane dla zwykłych graczy.
        "diament", "topdiax",
    })

    async def global_interaction_check(self, interaction: discord.Interaction) -> bool:
        """
        Globalny filtr komend slash:
        - Komendy z grupy 'music' (MusicCog) oraz komendy z PUBLIC_COMMANDS (np. /stats) → dostępne dla wszystkich.
        - Wszystkie pozostałe komendy → tylko Właściciel bota lub Administrator serwera.
        """
        cmd = interaction.command
        if cmd is None:
            return True

        # Wyciągnij nazwę komendy (dla grup: nazwa grupy lub subkomendy)
        cmd_name = cmd.name
        cog_name = getattr(cmd.binding, '__cog_name__', '') if cmd.binding else ''

        # Cogs.music (MusicCog) oraz komendy jawnie oznaczone jako publiczne (np. /stats)
        # są dostępne dla każdego, niezależnie od uprawnień.
        if (
            cmd_name in self.MUSIC_COMMANDS
            or cmd_name in self.PUBLIC_COMMANDS
            or cog_name in self.PUBLIC_COGS
        ):
            return True

        # Tylko właściciel bota (OWNER_ID z .env) ma pełen dostęp do wszystkich komend
        if interaction.user.id == self.owner_id_val:
            return True

        # Administrator serwera (oraz Właściciel Serwera) mają pełen dostęp do
        # pozostałych komend na równi z Właścicielem bota - zgodnie z opisem tej
        # metody powyżej. Wcześniej ten warunek nie istniał, więc mimo
        # dokumentacji faktycznie przechodził tędy WYŁĄCZNIE Właściciel bota.
        if isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.administrator
            or (interaction.guild is not None and interaction.user.id == interaction.guild.owner_id)
        ):
            return True

        # Brak uprawnień — zablokuj z komunikatem
        embed = discord.Embed(
            title="🔒  Dostęp Zablokowany",
            description=(
                "Ta komenda jest aktualnie **niedostępna**.\n\n"
                "✅ Możesz korzystać z komend sekcji **Muzyki**:\n"
                "`/play` · `/skip` · `/stop` · `/pause` · `/resume` · `/queue` · `/nowplaying` · `/volume` · `/loop` · `/shuffle` · `/clearqueue` · `/remove`\n"
                "oraz z komendy `/stats`."
            ),
            color=discord.Color(0xe74c3c)
        )
        embed.set_footer(text="Dostęp do pozostałych komend jest wyłącznie dla właściciela bota.")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

        return False

    async def close(self) -> None:
        """Wywoływane przy zamknięciu bota. Gwarantuje bezpieczny zrzut zasobów."""
        logger.info("Rozpoczynanie bezpiecznego wyłączania bota (Graceful Shutdown)...")
        await self.db.close()
        await super().close()
        logger.info("Bot został pomyślnie i bezpiecznie wyłączony.")

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        """Obsługa błędów powstałych podczas wykonywania komend slash."""
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original

        cmd_name = interaction.command.name if interaction.command else "Nieznana komenda"
        logger.error(f"Wyjątek w komendzie '{cmd_name}': {error}", exc_info=error)

        embed = discord.Embed(
            title="❌ Wystąpił błąd",
            color=config.ERROR_COLOR
        )
        embed.set_footer(text=config.FOOTER_TEXT)

        if isinstance(error, app_commands.MissingPermissions):
            perms = ", ".join(error.missing_permissions)
            embed.description = f"Nie posiadasz wymaganych uprawnień do wykonania tej komendy: `{perms}`."
        elif isinstance(error, app_commands.BotMissingPermissions):
            perms = ", ".join(error.missing_permissions)
            embed.description = f"Bot nie posiada wystarczających uprawnień na tym serwerze: `{perms}`."
        elif isinstance(error, app_commands.CommandOnCooldown):
            embed.description = f"Komenda jest obecnie zablokowana. Spróbuj ponownie za `{error.retry_after:.1f}`s."
        elif isinstance(error, app_commands.CheckFailure):
            embed.description = str(error) or "Nie spełniasz kryteriów wymaganych do użycia tej komendy."
        else:
            embed.description = "Wystąpił nieoczekiwany błąd wewnętrzny bota. Błąd został przekazany do logów."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as send_err:
            logger.error(f"Nie udało się przekazać informacji o błędzie do interakcji: {send_err}")