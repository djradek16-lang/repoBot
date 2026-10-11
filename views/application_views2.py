"""
Widoki modułu aplikacji rekrutacyjnych.

Zgłoszenie składa się przez formularz GUI (Discord Modal), a nie przez wpisanie
wzoru na czacie. Discord pozwala na maksymalnie 5 pól tekstowych w jednym
modalu, a formularz ma ich 8, więc jest podzielony na dwa modale:
ApplicationModalPart1 (Nick, Login, UID, Wiek, Ilość RP) -> po submit pokazuje
się efemeryczna wiadomość z przyciskiem "Dalej" (ContinueToPart2View) ->
dopiero kliknięcie TEGO przycisku otwiera ApplicationModalPart2 (Kto polecił,
Regulamin, Wpisowe). Modal NIE jest otwierany bezpośrednio z on_submit
poprzedniego modala — to znany, niestabilny wzorzec w discord.py, który potrafi
skutkować błędem Discorda "400 — In type: Value must be one of {4, 5, 6, 7, 10,
12}" (patrz Rapptz/discord.py#10010). Dopiero po drugim submicie zgłoszenie
jest przetwarzane (weryfikacja forum, embed na kanał przeglądu, DM z
potwierdzeniem). Wejściem do formularza jest trwały przycisk
ApplicationStartButton, wysyłany na kanał zgłoszeń komendą /config post_button.

Zawiera też trwałe przyciski (Dynamic Persistent Items) do akceptacji /
odrzucenia aplikacji bezpośrednio pod embedem na kanale przeglądu.
Custom ID koduje akcję oraz ID zgłaszającego (np. "app_decision:accept:123"),
dzięki czemu przyciski działają poprawnie nawet po restarcie bota — nie
trzeba trzymać osobnej tabeli w bazie danych ani ręcznie rejestrować każdej
wiadomości.
"""

import logging
import re
from typing import Optional

import discord

from utils.embeds import success_embed, error_embed, warning_embed, info_embed
from cogs.projectrpg import _find_forum_profile_url, _scrape_forum_game_info, _build_manual_search_url
import config

logger = logging.getLogger("AegisBot.Views.Applications")

CUSTOM_ID_TEMPLATE = r"app_decision:(?P<action>accept|reject):(?P<user_id>[0-9]+)"
START_BUTTON_CUSTOM_ID = "app_start_button"

# Etykiety pól formularza w kolejności występowania w formularzu (Modal).
REQUIRED_LABELS: list[str] = [
    "Nick",
    "Login",
    "UID",
    "Wiek",
    "Ilość RP",
    "Kto polecił Ci organizację?",
    "Czy zapoznałaś/eś się z regulaminem i będziesz go przestrzegał/a?",
    "Czy wyrażasz zgodę na wpłacenie wpisowego w wysokości 3000 PLN?",
]

# Krótsze, czytelne etykiety do wyświetlenia w embedzie.
DISPLAY_LABELS: dict[str, str] = {
    "Nick": "🪪 Nick",
    "Login": "🔑 Login",
    "UID": "🆔 UID",
    "Wiek": "🎂 Wiek",
    "Ilość RP": "⏱️ Ilość RP",
    "Kto polecił Ci organizację?": "🤝 Polecił(a)",
    "Czy zapoznałaś/eś się z regulaminem i będziesz go przestrzegał/a?": "📜 Regulamin",
    "Czy wyrażasz zgodę na wpłacenie wpisowego w wysokości 3000 PLN?": "💰 Wpisowe (3000 PLN)",
}

# Znaki specjalne Discord Markdown, które mogą uruchomić formatowanie, gdy
# przylegają bezpośrednio do tekstu: * (pogrubienie/kursywa), _ (kursywa/
# podkreślenie... właściwie pogrubienie w Discordzie), ~ (przekreślenie),
# ` (kod), | (spoiler).
_MARKDOWN_SPECIAL_RE = re.compile(r"([*_~`|])")


def _field_display(value: str) -> str:
    """Przygotowuje wartość pola do pokazania w embedzie na Discordzie.

    Surowe wartości z formularza trafiają tu wprost od użytkownika, więc mogą
    zawierać znaki specjalne Discord Markdown. Wstawiamy zaraz po nich
    niewidoczny znak zero-width space (U+200B), żeby przerwać wymagane przez
    parser Discorda "przyleganie" markera do tekstu — formatowanie się nie
    uruchamia, a na ekranie widać dokładnie to, co użytkownik wpisał.
    """
    value = (value or "").strip()
    if not value:
        return "*brak odpowiedzi*"
    return _MARKDOWN_SPECIAL_RE.sub("\\1\u200b", value)


def _only_digits(value: str) -> str:
    """Zwraca wyłącznie cyfry z podanego tekstu (do porównywania numerów UID)."""
    return re.sub(r"\D", "", value or "")


async def _find_forum_profile_with_retry(query: str, *, guarantee_link: bool) -> Optional[dict]:
    """Owija _find_forum_profile_url — JEDNA próba na zapytanie, z cloudscraper_retry=False.

    WAŻNE (poprawione na podstawie realnych logów bota): _find_forum_profile_url_via_cloudscraper
    sama w sobie robi do 4 prób (CLOUDSCRAPER_MAX_ATTEMPTS) przy KAŻDYM wywołaniu. Wcześniejsza
    wersja tej funkcji dodatkowo ponawiała całe wyszukiwanie 3x na zwykłe "brak wyniku" (nie
    tylko na wyjątek) — dla 3 pól (Nick/Login/UID) dawało to do ~36 żądań cloudscraper do forum
    w kilkanaście sekund. Logi pokazały, że to WŁASNE bombardowanie zaczynało wywoływać
    429 (rate limit) od forum już w trakcie pojedynczej weryfikacji, więc taki "retry"
    pogarszał sprawę zamiast pomagać. Dlatego teraz: JEDNA próba na zapytanie (cloudscraper
    dostaje cloudscraper_retry=False, czyli 1 zamiast 4 prób wewnątrz), a odporność na
    chwilowe niepowodzenie bierze się z RÓŻNYCH metod w łańcuchu (API/cloudscraper/aiohttp/
    DuckDuckGo/Bing) i z trzech różnych zapytań (Nick, Login, UID) w verify_against_forum —
    nie z powtarzania TEGO SAMEGO zapytania tą samą metodą."""
    try:
        return await _find_forum_profile_url(query, guarantee_link=guarantee_link, cloudscraper_retry=False)
    except Exception as e:
        logger.warning(f"Wyszukanie profilu forum nieudane ('{query}', guarantee_link={guarantee_link}): {e}")
        raise


async def _scrape_forum_game_info_with_retry(profile_url: str) -> Optional[dict]:
    """Owija _scrape_forum_game_info — również JEDNA próba (patrz docstring
    _find_forum_profile_with_retry: powtarzanie żądań do tego samego, już obciążonego forum
    w ramach jednej weryfikacji robi więcej szkody niż pożytku)."""
    try:
        return await _scrape_forum_game_info(profile_url)
    except Exception as e:
        logger.warning(f"Pobranie danych profilu forum nieudane ('{profile_url}'): {e}")
        return None


async def verify_against_forum(nick: str, login: str, provided_uid: str) -> dict:
    """Weryfikuje podane w formularzu Nick/Login/UID względem publicznego profilu forum ProjectRPG.

    Szuka DOKŁADNEGO dopasowania po kolei: Nick, potem Login, potem UID — JEDNA próba na
    każde zapytanie (patrz _find_forum_profile_with_retry — powtarzanie tego samego zapytania
    tą samą metodą tylko eskaluje blokadę forum, nie pomaga ją obejść). Celowo wołane z
    guarantee_link=False, żeby móc przejść przez wszystkie trzy identyfikatory zamiast
    zatrzymać się na pierwszym: z guarantee_link=True samo wyszukanie Nicku ZAWSZE zwróciłoby
    jakiś wynik (patrz _find_forum_profile_url — pkt 5, gwarantowana deska ratunku), więc Login
    i UID nigdy nie zostałyby sprawdzone.

    Jeśli żadna z trzech wartości nie da dokładnego trafienia (albo scraping/UID na stronie
    profilu zawiedzie), zgłoszenie WCIĄŻ dostaje link do ręcznego sprawdzenia — budowany
    LOKALNIE przez _build_manual_search_url (czysty URL, zero żądań sieciowych, więc nigdy
    się nie wywali) zamiast odpytywać forum/DuckDuckGo/Bing po raz drugi dla tego samego
    zapytania. Dzięki temu weryfikacja ZAWSZE kończy się jakimś sprawdzalnym wynikiem — nigdy
    suchym brakiem linku.

    Zwraca słownik: {'status': 'match'|'mismatch'|'unknown'|'not_found'|'error',
    'forum_uid': str|None, 'profile_url': str|None}.
    """
    queries = [q.strip() for q in (nick, login, provided_uid) if (q or "").strip()]

    forum_ref = None
    had_error = False

    for query in queries:
        try:
            candidate = await _find_forum_profile_with_retry(query, guarantee_link=False)
        except Exception as e:
            logger.warning(f"Błąd wyszukiwania profilu forum podczas weryfikacji aplikacji ('{query}'): {e}")
            had_error = True
            continue
        if candidate:
            forum_ref = candidate
            break

    if forum_ref is None:
        fallback_query = queries[0] if queries else ""
        fallback_url = _build_manual_search_url(fallback_query) if fallback_query else None
        status = "error" if had_error else "not_found"
        return {"status": status, "forum_uid": None, "profile_url": fallback_url}

    game_info = await _scrape_forum_game_info_with_retry(forum_ref["profile_url"])
    forum_uid = game_info.get("uid") if game_info else None

    if forum_uid and _only_digits(forum_uid) and _only_digits(forum_uid) == _only_digits(provided_uid):
        return {"status": "match", "forum_uid": forum_uid, "profile_url": forum_ref["profile_url"]}
    if forum_uid:
        return {"status": "mismatch", "forum_uid": forum_uid, "profile_url": forum_ref["profile_url"]}
    # Znaleziono profil, ale nie udało się odczytać z niego UID — mimo to zawsze dajemy
    # link do TEGO konkretnego profilu (nie tylko do wyszukiwarki), więc sztab może
    # zweryfikować ręcznie jednym kliknięciem.
    return {"status": "unknown", "forum_uid": None, "profile_url": forum_ref["profile_url"]}


async def _get_accept_role_ids(db, guild_id: int) -> set[int]:
    """
    Zwraca zbiór ID ról nadawanych po akceptacji aplikacji.

    Łączy nową listę wielu ról (`application_accept_roles`) ze starą,
    pojedynczą rolą (`guild_config.applications_accept_role_id`) — dzięki
    czemu serwery skonfigurowane przed wprowadzeniem obsługi wielu ról
    nadal działają bez konieczności ręcznej migracji.
    """
    role_ids: set[int] = set()

    rows = await db.fetchall(
        "SELECT role_id FROM application_accept_roles WHERE guild_id = ?",
        (guild_id,)
    )
    role_ids.update(row["role_id"] for row in rows)

    legacy_row = await db.fetchone(
        "SELECT applications_accept_role_id FROM guild_config WHERE guild_id = ?",
        (guild_id,)
    )
    if legacy_row and "applications_accept_role_id" in legacy_row.keys() and legacy_row["applications_accept_role_id"]:
        role_ids.add(legacy_row["applications_accept_role_id"])

    return role_ids


async def _has_review_permission(interaction: discord.Interaction) -> bool:
    """Sprawdza czy osoba klikająca przycisk może decydować o aplikacjach."""
    member = interaction.user
    guild = interaction.guild
    if guild is None or not isinstance(member, discord.Member):
        return False

    if member.id == guild.owner_id or member.guild_permissions.administrator:
        return True

    row = await interaction.client.db.fetchone(
        "SELECT applications_management_role_id, mod_role_id FROM guild_config WHERE guild_id = ?",
        (guild.id,)
    )
    if not row:
        return False

    for column in ("applications_management_role_id", "mod_role_id"):
        role_id = row[column] if column in row.keys() else None
        if role_id and any(r.id == role_id for r in member.roles):
            return True

    return False


def _get_embed_field_value(embed: discord.Embed, display_name: str) -> Optional[str]:
    """Zwraca wartość pola embedu o podanej nazwie wyświetlanej (np. '🪪 Nick'), albo None."""
    for field in embed.fields:
        if field.name == display_name:
            return field.value
    return None


def _find_recruit_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Znajduje rolę 'Rekrut' na serwerze (dopasowanie nazwy bez rozróżniania wielkości liter)."""
    return discord.utils.find(lambda r: r.name.lower() == "rekrut", guild.roles)


def _decided_embed(original: discord.Embed, action: str, decided_by: discord.Member) -> discord.Embed:
    """Zwraca kopię embedu zgłoszenia oznaczoną jako zaakceptowaną/odrzuconą."""
    embed = original.copy()
    if action == "accept":
        embed.color = config.SUCCESS_COLOR
        status_line = f"✅ **Zaakceptowano** przez {decided_by.mention}"
    else:
        embed.color = config.ERROR_COLOR
        status_line = f"❌ **Odrzucono** przez {decided_by.mention}"

    if embed.description and "Zgłaszający" in embed.description:
        embed.description = f"{embed.description}\n\n{status_line}"
    else:
        embed.description = status_line

    return embed


def build_review_embed(interaction: discord.Interaction, fields: dict[str, str], verification: dict | None = None) -> discord.Embed:
    embed_color = config.INFO_COLOR
    if verification and verification["status"] in ("mismatch", "not_found"):
        embed_color = config.WARNING_COLOR

    author = interaction.user
    embed = discord.Embed(
        title="📥 Nowa aplikacja rekrutacyjna",
        description=f"👤 Zgłaszający: {author.mention}",
        color=embed_color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=f"{author} ({author.id})", icon_url=author.display_avatar.url)
    embed.set_thumbnail(url=author.display_avatar.url)

    for key in REQUIRED_LABELS:
        embed.add_field(name=DISPLAY_LABELS[key], value=_field_display(fields.get(key, "")), inline=False)

    if verification:
        status = verification["status"]
        provided_uid = fields.get("UID", "?")
        if status == "match":
            v_text = f"✅ Zgadza się z forum (UID `{verification['forum_uid']}`)"
        elif status == "mismatch":
            v_text = f"⚠️ **NIEZGODNOŚĆ** — forum pokazuje UID `{verification['forum_uid']}`, w zgłoszeniu podano `{provided_uid}`"
        elif status == "not_found":
            safe_nick = _MARKDOWN_SPECIAL_RE.sub("\\1\u200b", fields.get("Nick", "") or "?")
            v_text = f"❌ Nie znaleziono dokładnego profilu **{safe_nick}** na forum ProjectRPG (sprawdzono nick, login i UID) — sprawdź ręcznie"
        elif status == "unknown":
            v_text = "❔ Znaleziono profil, ale nie udało się odczytać UID ze strony — sprawdź ręcznie"
        else:
            v_text = "⏳ Weryfikacja nieudana (błąd/timeout połączenia z forum) — sprawdź ręcznie"

        if verification.get("profile_url"):
            link_label = "Otwórz profil forum" if status in ("match", "mismatch", "unknown") else "🔎 Szukaj na forum"
            v_text += f"\n[{link_label}]({verification['profile_url']})"

        embed.add_field(name="🔎 Weryfikacja forum ProjectRPG", value=v_text, inline=False)

    embed.set_footer(text="Zgłoszenie złożone przez formularz")
    return embed


def build_dm_embed(fields: dict[str, str], guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="✅ Twoja aplikacja została złożona",
        description=f"Zgłoszenie na serwerze **{guild.name}** zostało zapisane i przekazane do zarządu organizacji. Poniżej kopia Twoich odpowiedzi:",
        color=config.SUCCESS_COLOR,
        timestamp=discord.utils.utcnow()
    )
    for key in REQUIRED_LABELS:
        embed.add_field(name=DISPLAY_LABELS[key], value=_field_display(fields.get(key, "")), inline=False)

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=config.FOOTER_TEXT)
    return embed


def build_recruitment_post_embed(guild: discord.Guild) -> discord.Embed:
    """Buduje embed rekrutacyjny do wysłania komendą /config post_button na
    kanał zgłoszeń, razem z trwałym przyciskiem ApplicationStartButton.
    """
    embed = discord.Embed(
        title="🏆 REKRUTACJA — TEAM77",
        description=(
            "Dołącz do jednej z najlepszych organizacji na serwerze!\n\n"
            "Nie szukamy ideałów — szukamy ludzi, którzy nie mają kija w tyłku, "
            "ogarniają i dobrze się bawią. Jeśli to Ty, wypełnij formularz poniżej."
        ),
        color=config.INFO_COLOR
    )

    embed.add_field(name="\u200b", value="▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬", inline=False)

    embed.add_field(
        name="⚠️ ZASADY",
        value=(
            "🔹 Upewnij się, że wszystkie podane dane są prawdziwe.\n"
            "🔹 Prosimy o zapoznanie się z regulaminem przed złożeniem aplikacji.\n"
            "🔹 W przypadku jakichś niedokładności skontaktujemy się z Tobą w wiadomości prywatnej.\n"
            "  \n"
            "⏳ Cierpliwości — widzimy, mamy oczy."
        ),
        inline=False
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Team77 • System Rekrutacji")
    return embed


async def _process_application(interaction: discord.Interaction, fields: dict[str, str]) -> None:
    """Przetwarza kompletne zgłoszenie (po submicie drugiego modala): weryfikuje na forum,
    przekazuje na kanał przeglądu i wysyła potwierdzenie DM. Wywoływane z on_submit
    ApplicationModalPart2."""
    bot = interaction.client
    guild = interaction.guild
    author = interaction.user

    await interaction.response.defer(ephemeral=True)

    row = await bot.db.fetchone(
        "SELECT applications_review_channel_id, applications_management_role_id FROM guild_config WHERE guild_id = ?",
        (guild.id,)
    )

    verification = await verify_against_forum(fields.get("Nick", ""), fields.get("Login", ""), fields.get("UID", ""))

    review_channel_id = row["applications_review_channel_id"] if row else None
    management_role_id = row["applications_management_role_id"] if row and "applications_management_role_id" in row.keys() else None
    if review_channel_id:
        review_channel = guild.get_channel(review_channel_id)
        if review_channel:
            ping_content = f"<@&{management_role_id}>" if management_role_id else None
            decision_view = discord.ui.View(timeout=None)
            decision_view.add_item(ApplicationDecisionButton("accept", author.id))
            decision_view.add_item(ApplicationDecisionButton("reject", author.id))
            try:
                await review_channel.send(
                    content=ping_content,
                    embed=build_review_embed(interaction, fields, verification),
                    view=decision_view,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
                )
            except discord.HTTPException as e:
                logger.error(f"Nie udało się przekazać aplikacji na kanał przeglądu: {e}")
        else:
            logger.warning(f"Skonfigurowany kanał przeglądu aplikacji ({review_channel_id}) nie istnieje na serwerze {guild.id}.")
    else:
        logger.warning(f"Kanał przeglądu aplikacji nie jest skonfigurowany dla serwera {guild.id} — zgłoszenie NIE zostało nigdzie przekazane sztabowi.")

    dm_sent = False
    try:
        await author.send(embed=build_dm_embed(fields, guild))
        dm_sent = True
    except (discord.Forbidden, discord.HTTPException):
        dm_sent = False

    confirmation_text = "Twoje zgłoszenie zostało zapisane i przekazane do zarządu organizacji do oceny."
    if not dm_sent:
        confirmation_text += "\n*(Nie mogłem wysłać Ci wiadomości prywatnej — sprawdź ustawienia prywatności serwera, jeśli chcesz otrzymywać kopię odpowiedzi na DM.)*"
    await interaction.followup.send(embed=success_embed("Aplikacja złożona", confirmation_text), ephemeral=True)


class ApplicationModalPart2(discord.ui.Modal, title="Aplikacja rekrutacyjna — część 2/2"):
    """Drugi krok formularza — otwierany automatycznie po submicie części 1."""

    kto_polecil = discord.ui.TextInput(
        label="Kto polecił Ci organizację?",
        style=discord.TextStyle.short,
        required=False,
        max_length=100
    )
    regulamin = discord.ui.TextInput(
        label="Zapoznałaś/eś się z regulaminem?",
        style=discord.TextStyle.short,
        placeholder="tak / nie",
        required=True,
        max_length=20
    )
    wpisowe = discord.ui.TextInput(
        label="Zgoda na wpisowe 3000 PLN?",
        style=discord.TextStyle.short,
        placeholder="tak / nie",
        required=True,
        max_length=20
    )

    def __init__(self, part1_fields: dict[str, str]) -> None:
        super().__init__()
        self._part1_fields = part1_fields

    async def on_submit(self, interaction: discord.Interaction) -> None:
        fields = dict(self._part1_fields)
        fields["Kto polecił Ci organizację?"] = str(self.kto_polecil)
        fields["Czy zapoznałaś/eś się z regulaminem i będziesz go przestrzegał/a?"] = str(self.regulamin)
        fields["Czy wyrażasz zgodę na wpłacenie wpisowego w wysokości 3000 PLN?"] = str(self.wpisowe)
        await _process_application(interaction, fields)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Błąd w części 2/2 formularza aplikacji: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Wystąpił błąd podczas przetwarzania zgłoszenia. Spróbuj ponownie."),
                ephemeral=True
            )


class ApplicationModalPart1(discord.ui.Modal, title="Aplikacja rekrutacyjna — część 1/2"):
    """Pierwszy krok formularza — otwierany po kliknięciu przycisku 'Złóż aplikację'."""

    nick = discord.ui.TextInput(label="Nick", style=discord.TextStyle.short, required=True, max_length=32)
    login = discord.ui.TextInput(label="Login", style=discord.TextStyle.short, required=True, max_length=32)
    uid = discord.ui.TextInput(label="UID", style=discord.TextStyle.short, required=True, max_length=20)
    wiek = discord.ui.TextInput(label="Wiek", style=discord.TextStyle.short, required=True, max_length=10)
    ilosc_rp = discord.ui.TextInput(label="Ilość RP", style=discord.TextStyle.short, required=True, max_length=50)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        part1_fields = {
            "Nick": str(self.nick),
            "Login": str(self.login),
            "UID": str(self.uid),
            "Wiek": str(self.wiek),
            "Ilość RP": str(self.ilosc_rp),
        }
        # UWAGA: otwieranie drugiego modala BEZPOŚREDNIO z on_submit pierwszego
        # (interaction.response.send_modal wewnątrz on_submit modala) potrafi
        # skutkować błędem Discorda "400 Invalid Form Body — In type: Value must
        # be one of {4, 5, 6, 7, 10, 12}" — to znany, niestabilny wzorzec w
        # discord.py (modal-z-modala), patrz np. Rapptz/discord.py#10010.
        # Dlatego zamiast tego pokazujemy krótką, efemeryczną wiadomość z
        # przyciskiem "Dalej" — drugi modal otwiera się dopiero z kliknięcia
        # TEGO przycisku (interakcja komponentu), co jest w pełni stabilne.
        await interaction.response.send_message(
            embed=info_embed(
                "Część 1/2 zapisana",
                "Kliknij przycisk poniżej, aby przejść do drugiej części formularza."
            ),
            view=ContinueToPart2View(part1_fields),
            ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Błąd w części 1/2 formularza aplikacji: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Wystąpił błąd podczas otwierania formularza. Spróbuj ponownie."),
                ephemeral=True
            )


class ContinueToPart2View(discord.ui.View):
    """Pośredni przycisk 'Dalej' między częścią 1/2 a 2/2 formularza.

    NIE jest to trwały widok (brak custom_id rejestrowanego po restarcie) —
    dane części 1 trzymane są tylko w pamięci obiektu przez czas trwania
    tej pojedynczej, efemerycznej wiadomości (timeout 300s wystarcza, bo to
    tylko chwilowe przejście między dwoma krokami tego samego zgłoszenia).
    """

    def __init__(self, part1_fields: dict[str, str]) -> None:
        super().__init__(timeout=300)
        self._part1_fields = part1_fields

    @discord.ui.button(label="Dalej — część 2/2", emoji="➡️", style=discord.ButtonStyle.primary)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ApplicationModalPart2(self._part1_fields))
        self.stop()


class ApplicationStartButton(discord.ui.View):
    """Trwały przycisk 'Złóż aplikację' wysyłany na kanał zgłoszeń.

    Zwykły persistent View (nie DynamicItem) wystarczy, bo custom_id jest stały —
    przycisk nie koduje żadnych danych per-użytkownik (w przeciwieństwie do
    ApplicationDecisionButton, który koduje akcję + ID zgłaszającego).
    Rejestrowany w cog_load przez bot.add_view(ApplicationStartButton()).
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Złóż aplikację",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id=START_BUTTON_CUSTOM_ID
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ApplicationModalPart1())


class ApplicationDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=CUSTOM_ID_TEMPLATE
):
    """Przycisk Akceptuj / Odrzuć dla konkretnego zgłaszającego."""

    def __init__(self, action: str, user_id: int) -> None:
        self.action = action
        self.user_id = user_id

        if action == "accept":
            style = discord.ButtonStyle.success
            label = "Akceptuj"
            emoji = "✅"
        else:
            style = discord.ButtonStyle.danger
            label = "Odrzuć"
            emoji = "❌"

        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"app_decision:{action}:{user_id}"
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match, /
    ) -> "ApplicationDecisionButton":
        return cls(match["action"], int(match["user_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        if not await _has_review_permission(interaction):
            await interaction.response.send_message(
                embed=error_embed("Brak Uprawnień", "Nie masz uprawnień do decydowania o aplikacjach rekrutacyjnych."),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        applicant = guild.get_member(self.user_id)
        if applicant is None:
            try:
                applicant = await guild.fetch_member(self.user_id)
            except discord.HTTPException:
                applicant = None

        if applicant is None:
            await interaction.followup.send(
                embed=error_embed("Użytkownik Niedostępny", "Zgłaszający opuścił już serwer — nie można kontynuować decyzji."),
                ephemeral=True
            )
            return

        bot = interaction.client
        role_added = False
        added_roles: list[discord.Role] = []

        if self.action == "accept":
            role_ids = await _get_accept_role_ids(bot.db, guild.id)

            if not role_ids:
                await interaction.followup.send(
                    embed=warning_embed(
                        "Role Nieskonfigurowane",
                        "Zgłoszenie zostanie oznaczone jako zaakceptowane, ale żadna rola nie zostanie nadana — "
                        "skonfiguruj je komendą `/config add_accept_role` (można dodać kilka)."
                    ),
                    ephemeral=True
                )
            else:
                roles_to_add: list[discord.Role] = []
                missing_role_ids: list[int] = []
                for role_id in role_ids:
                    role = guild.get_role(role_id)
                    if role is None:
                        missing_role_ids.append(role_id)
                    else:
                        roles_to_add.append(role)

                if missing_role_ids:
                    await interaction.followup.send(
                        embed=error_embed(
                            "Błąd Roli",
                            f"{len(missing_role_ids)} skonfigurowanych ról po akceptacji już nie istnieje na serwerze "
                            "i zostały pominięte."
                        ),
                        ephemeral=True
                    )

                if roles_to_add:
                    try:
                        await applicant.add_roles(*roles_to_add, reason=f"Aplikacja zaakceptowana przez {interaction.user}")
                        role_added = True
                        added_roles = roles_to_add
                    except discord.Forbidden:
                        await interaction.followup.send(
                            embed=error_embed(
                                "Brak Uprawnień Bota",
                                "Nie mogę nadać jednej lub więcej ról — sprawdź hierarchię ról bota."
                            ),
                            ephemeral=True
                        )
                    except discord.HTTPException as e:
                        logger.error(f"Nie udało się nadać ról po akceptacji aplikacji: {e}")
                        await interaction.followup.send(
                            embed=error_embed("Błąd", "Wystąpił błąd podczas nadawania ról."),
                            ephemeral=True
                        )

        original_embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()

        recruit_role_removed = False
        nick_changed = False
        new_nick: Optional[str] = None

        if self.action == "accept":
            # Zdejmij rolę "Rekrut" — akceptacja aplikacji oznacza, że użytkownik nie jest
            # już w trakcie rekrutacji.
            recruit_role = _find_recruit_role(guild)
            if recruit_role and recruit_role in applicant.roles:
                try:
                    await applicant.remove_roles(
                        recruit_role, reason=f"Aplikacja zaakceptowana przez {interaction.user}"
                    )
                    recruit_role_removed = True
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=error_embed(
                            "Brak Uprawnień Bota",
                            "Nie mogę zdjąć roli Rekrut — sprawdź hierarchię ról bota."
                        ),
                        ephemeral=True
                    )
                except discord.HTTPException as e:
                    logger.error(f"Nie udało się zdjąć roli Rekrut po akceptacji aplikacji: {e}")

            # Zmień nick zgłaszającego na ten podany w formularzu rekrutacyjnym.
            raw_nick = _get_embed_field_value(original_embed, "🪪 Nick")
            if raw_nick and raw_nick != "*brak odpowiedzi*":
                new_nick = raw_nick[:32]  # limit Discorda na długość nicku
                try:
                    await applicant.edit(
                        nick=new_nick, reason=f"Aplikacja zaakceptowana przez {interaction.user}"
                    )
                    nick_changed = True
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=error_embed(
                            "Brak Uprawnień Bota",
                            "Nie mogę zmienić nicku zgłaszającego — sprawdź hierarchię ról bota "
                            "(nie mogę zmieniać nicku osób z wyższą rolą)."
                        ),
                        ephemeral=True
                    )
                except discord.HTTPException as e:
                    logger.error(f"Nie udało się zmienić nicku po akceptacji aplikacji: {e}")

        decided_embed = _decided_embed(original_embed, self.action, interaction.user)

        try:
            await interaction.message.edit(embed=decided_embed, view=None)
        except discord.HTTPException as e:
            logger.error(f"Nie udało się zaktualizować wiadomości aplikacji po decyzji: {e}")

        try:
            if self.action == "accept":
                dm_desc = f"Twoja aplikacja na serwerze **{guild.name}** została **zaakceptowana**! 🎉"
                if role_added:
                    if len(added_roles) == 1:
                        dm_desc += "\nOtrzymałeś/aś odpowiednią rolę na serwerze."
                    else:
                        dm_desc += "\nOtrzymałeś/aś odpowiednie role na serwerze."
                await applicant.send(embed=success_embed("Aplikacja Zaakceptowana", dm_desc))
            else:
                await applicant.send(embed=error_embed(
                    "Aplikacja Odrzucona",
                    f"Twoja aplikacja na serwerze **{guild.name}** została **odrzucona**."
                ))
        except (discord.Forbidden, discord.HTTPException):
            pass

        summary = f"Zgłoszenie {applicant.mention} zostało " + ("zaakceptowane." if self.action == "accept" else "odrzucone.")
        if self.action == "accept" and role_added:
            if len(added_roles) == 1:
                summary += f" Rola {added_roles[0].mention} została nadana."
            else:
                roles_list = ", ".join(role.mention for role in added_roles)
                summary += f" Nadano {len(added_roles)} ról: {roles_list}."
        if self.action == "accept" and recruit_role_removed:
            summary += " Rola Rekrut została zdjęta."
        if self.action == "accept" and nick_changed:
            summary += f" Nick zmieniony na **{new_nick}**."
        await interaction.followup.send(embed=success_embed("Decyzja Zapisana", summary), ephemeral=True)
