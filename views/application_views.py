"""
Widoki modułu aplikacji rekrutacyjnych.

Zgłoszenie składa się przez nowoczesny, jednoetapowy formularz GUI (Discord Modal).
Formularz zawiera dokładnie 5 pól tekstowych:
1. Nick
2. Login
3. UID
4. Wiek
5. Dlaczego Team77? (Krótkie uzasadnienie)

Brak wymogów ilości godzin/RP oraz brak sztywnych ograniczeń wiekowych.
Formularz mieści się w pojedynczym oknie dialogowym Discord Modal (Discord dopuszcza
maksymalnie 5 komponentów TextInput per Modal), co eliminuje konieczność dzielenia
procesu na wiele etapów i zapewnia natychmiastowe oraz stabilne działanie.

Wejściem do formularza jest trwały przycisk ApplicationStartButton, wysyłany na kanał
zgłoszeń komendą /config post_button. Po wysłaniu zgłoszenia następuje automatyczna
weryfikacja kandydata na forum ProjectRPG, po czym weryfikowane podanie wraz z
przyciskami decyzyjnymi trafia na kanał przeglądu zarządu, a kandydat otrzymuje kopię na DM.
"""

import logging
import re
from typing import Any, Optional

import discord

from utils.embeds import success_embed, error_embed, warning_embed, info_embed
from cogs.projectrpg import _find_forum_profile_url, _scrape_forum_game_info, _build_manual_search_url
import config

logger = logging.getLogger("AegisBot.Views.Applications")

CUSTOM_ID_TEMPLATE = r"app_decision:(?P<action>accept|reject|delete|lookup):(?P<user_id>[0-9]+)"
START_BUTTON_CUSTOM_ID = "app_start_button"

# Etykiety pól formularza w kolejności występowania w formularzu (Modal).
REQUIRED_LABELS: list[str] = [
    "Nick",
    "Login",
    "UID",
    "Wiek",
    "Dlaczego Team77? (Krótkie uzasadnienie)",
]

# Krótsze, czytelne etykiety do wyświetlenia w embedzie.
DISPLAY_LABELS: dict[str, str] = {
    "Nick": "🪪 1. Nick",
    "Login": "🔑 2. Login",
    "UID": "🆔 3. UID",
    "Wiek": "🎂 4. Wiek",
    "Dlaczego Team77? (Krótkie uzasadnienie)": "💬 5. Dlaczego Team77?",
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
    """Zwraca wartość pola embedu o podanej nazwie wyświetlanej, albo None."""
    for field in embed.fields:
        if display_name.lower() in field.name.lower():
            val = field.value.strip()
            if val.startswith("`") and val.endswith("`") and len(val) >= 2:
                val = val[1:-1]
            return val
    return None


def _find_recruit_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Znajduje rolę 'Rekrut' na serwerze (dopasowanie nazwy bez rozróżniania wielkości liter)."""
    return discord.utils.find(lambda r: r.name.lower() == "rekrut", guild.roles)


def _decided_embed(original: discord.Embed, action: str, decided_by: Any, reason: Optional[str] = None) -> discord.Embed:
    """Zwraca kopię embedu zgłoszenia oznaczoną jako zaakceptowaną/odrzuconą."""
    embed = original.copy()
    decided_by_text = decided_by.mention if hasattr(decided_by, "mention") else str(decided_by or "System")
    if action == "accept":
        embed.color = config.SUCCESS_COLOR
        status_line = f"✅ **Zaakceptowano** przez {decided_by_text}"
    else:
        embed.color = config.ERROR_COLOR
        status_line = f"❌ **Odrzucono** przez {decided_by_text}"
        if reason:
            status_line += f"\n📝 **Powód:** {reason}"

    if embed.description and "Zgłaszający" in embed.description:
        embed.description = f"{embed.description}\n\n{status_line}"
    else:
        embed.description = status_line

    return embed


def build_review_embed(interaction: discord.Interaction, fields: dict[str, str], verification: dict | None = None) -> discord.Embed:
    embed_color = 0xF1C40F  # Złoty kolor Team77
    if verification and verification.get("status") in ("mismatch", "not_found", "error"):
        embed_color = 0xE67E22
    elif verification and verification.get("status") == "match":
        embed_color = 0x2ECC71

    author = interaction.user
    embed = discord.Embed(
        title="📥 Nowa aplikacja rekrutacyjna — Team77",
        description=(
            f"👤 **Kandydat:** {author.mention} (`{author.name}` • ID: `{author.id}`)\n"
            f"📅 **Data złożenia:** <t:{int(discord.utils.utcnow().timestamp())}:F>"
        ),
        color=embed_color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=f"{author.display_name} ({author.id})", icon_url=author.display_avatar.url)
    embed.set_thumbnail(url=author.display_avatar.url)

    # Pola podstawowe (Nick, Login, UID, Wiek) w układzie inline
    inline_keys = ["Nick", "Login", "UID", "Wiek"]
    for key in inline_keys:
        val = _field_display(fields.get(key, ""))
        embed.add_field(name=DISPLAY_LABELS.get(key, key), value=f"`{val}`", inline=True)

    # Pole uzasadnienia (Dlaczego Team77) na pełną szerokość
    why_key = "Dlaczego Team77? (Krótkie uzasadnienie)"
    why_val = _field_display(fields.get(why_key, ""))
    embed.add_field(
        name=DISPLAY_LABELS.get(why_key, "💬 5. Dlaczego Team77?"),
        value=f"> {why_val}" if why_val != "*brak odpowiedzi*" else "*brak odpowiedzi*",
        inline=False
    )

    if verification:
        status = verification.get("status", "unknown")
        provided_uid = fields.get("UID", "?")
        if status == "match":
            v_text = f"✅ Zgadza się z forum (UID `{verification.get('forum_uid')}`)"
        elif status == "mismatch":
            v_text = f"⚠️ **NIEZGODNOŚĆ** — forum wskazuje UID `{verification.get('forum_uid')}`, w zgłoszeniu podano `{provided_uid}`"
        elif status == "not_found":
            safe_nick = _MARKDOWN_SPECIAL_RE.sub("\\1\u200b", fields.get("Nick", "") or "?")
            v_text = f"❌ Nie znaleziono profilu **{safe_nick}** na forum ProjectRPG (sprawdzono Nick, Login, UID) — wymagana weryfikacja ręczna"
        elif status == "unknown":
            v_text = "❔ Znaleziono profil, ale nie udało się odczytać UID ze strony — wymagana weryfikacja ręczna"
        else:
            v_text = "⏳ Weryfikacja nieudana (błąd/timeout połączenia z forum) — wymagana weryfikacja ręczna"

        if verification.get("profile_url"):
            link_label = "🔗 Otwórz profil na forum ProjectRPG" if status in ("match", "mismatch", "unknown") else "🔎 Szukaj na forum"
            v_text += f"\n[{link_label}]({verification['profile_url']})"

        embed.add_field(name="🔎 Weryfikacja forum ProjectRPG", value=v_text, inline=False)

    embed.set_footer(text="Team77 • System Rekrutacji • Wybierz akcję decyzyjną poniżej")
    return embed


def build_dm_embed(fields: dict[str, str], guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="📨 Twoja aplikacja do Team77 została złożona!",
        description=(
            f"Dziękujemy za złożenie podania do organizacji **Team77** na serwerze **{guild.name}**!\n"
            "Zgłoszenie zostało pomyślnie zarejestrowane i przekazane do zarządu. "
            "Poniżej znajduje się podsumowanie Twoich odpowiedzi:\n"
        ),
        color=0xF1C40F,
        timestamp=discord.utils.utcnow()
    )

    for key in ["Nick", "Login", "UID", "Wiek"]:
        embed.add_field(name=DISPLAY_LABELS.get(key, key), value=_field_display(fields.get(key, "")), inline=True)

    why_key = "Dlaczego Team77? (Krótkie uzasadnienie)"
    embed.add_field(
        name=DISPLAY_LABELS.get(why_key, "💬 5. Dlaczego Team77?"),
        value=_field_display(fields.get(why_key, "")),
        inline=False
    )

    embed.add_field(
        name="📌 Co dalej?",
        value=(
            "Zarząd organizacji zapozna się z Twoim podaniem. Gdy tylko zapadnie decyzja, "
            "otrzymasz powiadomienie w wiadomości prywatnej (DM). Prosimy o cierpliwość!"
        ),
        inline=False
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Team77 • System Rekrutacji")
    return embed


def build_recruitment_post_embed(guild: discord.Guild) -> discord.Embed:
    """Buduje embed rekrutacyjny do wysłania komendą /config post_button na
    kanał zgłoszeń, razem z trwałym przyciskiem ApplicationStartButton.
    """
    embed = discord.Embed(
        title="🏛️ REKRUTACJA — ORGANIZACJA TEAM77",
        description=(
            "Dołącz do elitarnej organizacji na serwerze ProjectRPG!\n\n"
            "Szukamy aktywnych, dojrzałych i zgranych graczy, którzy chcą wspólnie budować potęgę Team77. "
            "U nas liczy się dobra atmosfera, wzajemny szacunek i wspólna zabawa."
        ),
        color=0xF1C40F
    )

    embed.add_field(
        name="✨ CO OFERUJEMY",
        value=(
            "🔹 Rozbudowaną flotę pojazdów organizacyjnych\n"
            "🔹 Aktywny, zintegrowany zarząd i pomocną ekipę\n"
            "🔹 Cotygodniowe premie, eventy i konkursy z nagrodami\n"
            "🔹 Dostęp do rozbudowanego bota"
        ),
        inline=False
    )

    embed.add_field(
        name="📌 WYMAGANIA REKRUTACYJNE",
        value=(
            "🔹 Kultura osobista oraz szacunek do członków organizacji\n"
            "🔹 Posiadanie sprawnego mikrofonu i obecność na Discordzie\n"
            "🔹 Aktywność w grze oraz chęć integracji z zespołem\n"
            "🔹 Gotowość do opłacenia wpisowego organizacyjnego (3 000 PLN)"
        ),
        inline=False
    )

    embed.add_field(
        name="📝 PROCES APLIKACJI",
        value=(
            "1️⃣ Kliknij przycisk **Złóż aplikację** poniżej.\n"
            "2️⃣ Wypełnij krótki formularz zgłoszeniowy (5 pytań).\n"
            "3️⃣ Zarząd rozpatrzy Twoje zgłoszenie w kanale weryfikacji.\n"
            "4️⃣ Otrzymasz natychmiastowe powiadomienie na DM o decyzji!"
        ),
        inline=False
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Team77 • Rekrutacja • Kliknij przycisk poniżej, aby złożyć podanie")
    return embed


async def _process_application(interaction: discord.Interaction, fields: dict[str, str]) -> None:
    """Przetwarza kompletne zgłoszenie: weryfikuje na forum, przekazuje na kanał
    przeglądu z przyciskami decyzyjnymi, zapisuje do bazy i wysyła potwierdzenie DM."""
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
            decision_view.add_item(ApplicationDecisionButton("lookup", author.id))
            decision_view.add_item(ApplicationDecisionButton("delete", author.id))
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

    # Zapis do bazy danych aplikacji (dla panelu Web i archiwum)
    forum_url = (verification or {}).get("profile_url") or ""
    try:
        await bot.db.execute(
            """
            INSERT INTO applications (
                guild_id, user_id, nick, login, uid, age, why,
                recommended_by, rules_agreed, fee_agreed, forum_url, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                guild.id,
                author.id,
                fields.get("Nick", ""),
                fields.get("Login", ""),
                fields.get("UID", ""),
                fields.get("Wiek", ""),
                fields.get("Dlaczego Team77? (Krótkie uzasadnienie)", ""),
                "",
                "tak",
                "tak",
                forum_url,
            )
        )
    except Exception as e:
        logger.error(f"Błąd zapisu aplikacji do bazy danych: {e}")

    dm_sent = False
    try:
        await author.send(embed=build_dm_embed(fields, guild))
        dm_sent = True
    except (discord.Forbidden, discord.HTTPException):
        dm_sent = False

    confirmation_text = "Twoje zgłoszenie do organizacji **Team77** zostało pomyślnie zapisane i przekazane do zarządu."
    if not dm_sent:
        confirmation_text += "\n*(Nie mogłem wysłać Ci wiadomości prywatnej — sprawdź ustawienia prywatności serwera, jeśli chcesz otrzymywać kopię odpowiedzi na DM.)*"
    await interaction.followup.send(embed=success_embed("Aplikacja złożona", confirmation_text), ephemeral=True)


class ApplicationModal(discord.ui.Modal, title="Rekrutacja — Organizacja Team77"):
    """Jednoetapowy formularz rekrutacyjny Team77 (5 pól tekstowych)."""

    nick = discord.ui.TextInput(
        label="Nick",
        style=discord.TextStyle.short,
        placeholder="Twój aktualny nick na serwerze MTA",
        required=True,
        max_length=32
    )
    login = discord.ui.TextInput(
        label="Login",
        style=discord.TextStyle.short,
        placeholder="Dokładny login / nazwa konta z forum ProjectRPG",
        required=True,
        max_length=32
    )
    uid = discord.ui.TextInput(
        label="UID",
        style=discord.TextStyle.short,
        placeholder="Twój unikalny numer UID z gry (np. 12345)",
        required=True,
        max_length=20
    )
    wiek = discord.ui.TextInput(
        label="Wiek",
        style=discord.TextStyle.short,
        placeholder="Twój wiek (np. 18)",
        required=True,
        max_length=10
    )
    dlaczego = discord.ui.TextInput(
        label="Dlaczego Team77? (Krótkie uzasadnienie)",
        style=discord.TextStyle.paragraph,
        placeholder="Napisz krótko, dlaczego akurat Team77 i co wnosisz do ekipy...",
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        fields = {
            "Nick": str(self.nick).strip(),
            "Login": str(self.login).strip(),
            "UID": str(self.uid).strip(),
            "Wiek": str(self.wiek).strip(),
            "Dlaczego Team77? (Krótkie uzasadnienie)": str(self.dlaczego).strip(),
        }
        await _process_application(interaction, fields)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Błąd w formularzu aplikacji: {error}", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Wystąpił błąd podczas wysyłania zgłoszenia. Spróbuj ponownie."),
                ephemeral=True
            )


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
        await interaction.response.send_modal(ApplicationModal())


class ApplicationRejectModal(discord.ui.Modal, title="Odrzucenie Aplikacji"):
    """Formularz podania powodu odrzucenia aplikacji rekrutacyjnej."""

    reason_input = discord.ui.TextInput(
        label="Powód odrzucenia",
        style=discord.TextStyle.paragraph,
        placeholder="Wpisz powód odrzucenia aplikacji.",
        required=True,
        max_length=500
    )

    def __init__(self, user_id: int, message: discord.Message) -> None:
        super().__init__()
        self.user_id = user_id
        self.target_message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        reason = self.reason_input.value.strip() or None
        await _handle_decision(
            interaction,
            action="reject",
            user_id=self.user_id,
            target_message=self.target_message,
            reason=reason
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Błąd w formularzu odrzucenia aplikacji: {error}", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed("Błąd", "Wystąpił błąd podczas odrzucania aplikacji."),
                ephemeral=True
            )


async def _handle_decision(
    interaction: discord.Interaction,
    action: str,
    user_id: int,
    target_message: discord.Message,
    reason: Optional[str] = None
) -> None:
    """Wspólna logika akceptacji lub odrzucenia aplikacji."""
    guild = interaction.guild
    if guild is None:
        return

    if not interaction.response.is_done():
        await interaction.response.defer()

    applicant = guild.get_member(user_id)
    if applicant is None:
        try:
            applicant = await guild.fetch_member(user_id)
        except discord.HTTPException:
            applicant = None

    bot = interaction.client

    if applicant is None:
        if action == "reject":
            # Pozwól zamknąć i odrzucić zgłoszenie w bazie i na kanale przeglądu nawet po opuszczeniu serwera
            original_embed = target_message.embeds[0] if target_message.embeds else discord.Embed()
            decided_embed = _decided_embed(original_embed, "reject", interaction.user, reason=reason or "Kandydat opuścił serwer Discord.")
            try:
                await target_message.edit(embed=decided_embed, view=None)
            except discord.HTTPException as e:
                logger.error(f"Nie udało się zaktualizować wiadomości po decyzji: {e}")

            await bot.db.execute(
                """
                UPDATE applications
                SET status = 'reject', decision_by = ?, decision_by_name = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND guild_id = ? AND status = 'pending'
                """,
                (interaction.user.id, str(interaction.user), reason or "Kandydat opuścił serwer Discord.", user_id, guild.id)
            )

            await interaction.followup.send(
                embed=warning_embed(
                    "Kandydat Opuścił Serwer",
                    f"Zgłaszający <@{user_id}> opuścił serwer Discord. Podanie zostało oznaczone w bazie jako **odrzucone**, a przyciski decyzyjne zdjęte."
                ),
                ephemeral=True
            )
            return
        else:
            await interaction.followup.send(
                embed=error_embed(
                    "Użytkownik Niedostępny",
                    "Zgłaszający opuścił już serwer Discord — nie można nadać mu ról. Możesz odrzucić to podanie przyciskiem 'Odrzuć' lub trwale je usunąć przyciskiem '🗑️ Usuń'."
                ),
                ephemeral=True
            )
            return

    role_added = False
    added_roles: list[discord.Role] = []

    if action == "accept":
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

    original_embed = target_message.embeds[0] if target_message.embeds else discord.Embed()

    recruit_role_removed = False
    nick_changed = False
    new_nick: Optional[str] = None

    if action == "accept":
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
        raw_nick = _get_embed_field_value(original_embed, "Nick")
        if raw_nick and raw_nick != "*brak odpowiedzi*":
            new_nick = raw_nick[:32]
            try:
                await applicant.edit(
                    nick=new_nick, reason=f"Aplikacja zaakceptowana przez {interaction.user}"
                )
                nick_changed = True
            except discord.Forbidden:
                pass
            except discord.HTTPException as e:
                logger.error(f"Nie udało się zmienić nicku po akceptacji aplikacji: {e}")

    decided_embed = _decided_embed(original_embed, action, interaction.user, reason=reason)

    try:
        await target_message.edit(embed=decided_embed, view=None)
    except discord.HTTPException as e:
        logger.error(f"Nie udało się zaktualizować wiadomości aplikacji po decyzji: {e}")

    dm_sent = True
    try:
        if action == "accept":
            c_nick = new_nick or applicant.display_name
            dm_embed = discord.Embed(
                title=f"Witaj w Organizacji Team77, {c_nick}!",
                description=(
                    f"Z przyjemnością informujemy, że Twoje podanie do organizacji **Team77** zostało "
                    f"**pozytywnie rozpatrzone** przez Zarząd!\n\n"
                    f"Otrzymałeś już rolę **Rekruta** na naszym serwerze Discord."
                ),
                color=0x23A55A
            )
            dm_embed.set_author(name="🎉 GRATULACJE • TWOJE PODANIE ZOSTAŁO ZAAKCEPTOWANE!")
            dm_embed.add_field(
                name="📌 Co należy teraz zrobić?",
                value=(
                    "1. Wejdź na kanał **#ogłoszenia-organizacji** i zapoznaj się z aktualnościami.\n"
                    "2. Skontaktuj się z Zarządem w grze lub na kanale **#wpisowe** w celu uiszczenia 3,000 PLN.\n"
                    "3. Pobierz role powiadomień i wejdź na nasz kanał głosowy!"
                ),
                inline=False
            )
            dm_embed.set_footer(text="Do zobaczenia na serwerze! • Zarząd Team77")
            await applicant.send(embed=dm_embed)
        else:
            dm_embed = discord.Embed(
                title="Twoje Podanie Zostało Odrzucone",
                description=(
                    "Dziękujemy za poświęcony czas i chęć dołączenia do naszej organizacji.\n"
                    "Niestety, Twoja aplikacja nie została zaakceptowana na obecną chwilę."
                ),
                color=0xF23F43
            )
            dm_embed.set_author(name="DECYZJA ZARZĄDU • REKRUTACJA TEAM77")
            dm_embed.add_field(
                name="❌ Powód Decyzji",
                value=f"*{reason or 'Zbyt mała ilość godzin na serwerze lub aktywne kary w kartotece.'}*",
                inline=False
            )
            dm_embed.add_field(
                name="⏳ Kolejna Próba",
                value="Możesz złożyć kolejne podanie za **7 dni** po poprawie aktywności.",
                inline=False
            )
            dm_embed.set_footer(text="Życzymy powodzenia na serwerze! • Zarząd Team77")
            await applicant.send(embed=dm_embed)
    except (discord.Forbidden, discord.HTTPException):
        dm_sent = False

    summary = f"Zgłoszenie {applicant.mention} zostało " + ("zaakceptowane." if action == "accept" else "odrzucone.")
    if action == "accept" and role_added:
        if len(added_roles) == 1:
            summary += f" Rola {added_roles[0].mention} została nadana."
        else:
            roles_list = ", ".join(role.mention for role in added_roles)
            summary += f" Nadano {len(added_roles)} ról: {roles_list}."
    if action == "accept" and recruit_role_removed:
        summary += " Rola Rekrut została zdjęta."
    if action == "accept" and nick_changed:
        summary += f" Nick zmieniony na **{new_nick}**."
    if action == "reject" and reason:
        summary += f"\n📝 Powód: *{reason}*"
    if not dm_sent:
        summary += "\n⚠️ *(Nie udało się wysłać wiadomości DM do kandydata — ma zablokowane wiadomości prywatne)*"

    # Aktualizacja rekordu w bazie danych
    try:
        await bot.db.execute(
            """
            UPDATE applications
            SET status = ?, decision_by = ?, decision_by_name = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND guild_id = ? AND status = 'pending'
            """,
            (action, interaction.user.id, str(interaction.user), reason, user_id, guild.id)
        )
    except Exception as e:
        logger.error(f"Błąd aktualizacji aplikacji w bazie danych: {e}")

    await interaction.followup.send(embed=success_embed("Decyzja Zapisana", summary), ephemeral=True)


async def _handle_delete(
    interaction: discord.Interaction,
    user_id: int,
    target_message: discord.Message
) -> None:
    """Trwale usuwa zgłoszenie z bazy danych i usuwa przyciski z wiadomości przeglądu."""
    guild = interaction.guild
    if guild is None:
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    bot = interaction.client

    try:
        await bot.db.execute(
            "DELETE FROM applications WHERE user_id = ? AND guild_id = ?",
            (user_id, guild.id)
        )
    except Exception as e:
        logger.error(f"Błąd usuwania aplikacji z bazy: {e}")

    original_embed = target_message.embeds[0] if target_message.embeds else discord.Embed()
    deleted_embed = original_embed.copy()
    deleted_embed.color = 0x4f545c
    deleted_embed.title = "🗑️ Zgłoszenie Usunięte z Bazy"
    deleted_embed.description = f"Zgłoszenie kandydata <@{user_id}> (ID: `{user_id}`) zostało trwale usunięte z bazy danych przez {interaction.user.mention}."

    try:
        await target_message.edit(embed=deleted_embed, view=None)
    except discord.HTTPException as e:
        logger.warning(f"Nie udało się zaktualizować wiadomości po usunięciu: {e}")

    await interaction.followup.send(
        embed=success_embed(
            "Podanie Usunięte",
            f"Zgłoszenie użytkownika <@{user_id}> zostało pomyślnie i bezpowrotnie usunięte z bazy danych. Przyciski decyzyjne zostały zdjęte."
        ),
        ephemeral=True
    )


async def _handle_lookup(
    interaction: discord.Interaction,
    user_id: int,
    target_message: discord.Message
) -> None:
    """Wyświetla efemeryczny raport ze skanera ProjectRPG / MTA dla danego zgłoszenia."""
    guild = interaction.guild
    if guild is None:
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    bot = interaction.client
    app_row = await bot.db.fetchone(
        "SELECT * FROM applications WHERE user_id = ? AND guild_id = ? ORDER BY id DESC LIMIT 1",
        (user_id, guild.id)
    )

    nick = (app_row["nick"] if app_row and app_row["nick"] else "") or ""
    uid = (app_row["uid"] if app_row and app_row["uid"] else "") or ""
    login = (app_row["login"] if app_row and app_row["login"] else "") or ""

    query = nick or uid or login or str(user_id)
    from cogs.projectrpg import _find_forum_profile_url, _scrape_forum_game_info, _get_ase_data

    forum_ref = await _find_forum_profile_url(query) if query else None
    game_info = None
    if forum_ref and forum_ref.get("profile_url"):
        game_info = await _scrape_forum_game_info(forum_ref["profile_url"])

    is_online_mta = False
    try:
        ase_data = await _get_ase_data()
        players = ase_data.get("players", [])
        if nick:
            is_online_mta = any(nick.lower() == p.get("name", "").lower() for p in players)
    except Exception:
        pass

    embed = info_embed(
        f"📊 Skaner Gracza: {nick or uid or user_id}",
        f"Informacje o kandydacie <@{user_id}> pobrane z forum i serwera ProjectRPG."
    )
    embed.add_field(name="🪪 Nick / Login", value=f"{nick or '-'} / {login or '-'}", inline=True)
    embed.add_field(name="🆔 UID", value=uid or (game_info.get("uid") if game_info else "-"), inline=True)
    embed.add_field(
        name="🎮 Status MTA",
        value="🟢 Online na serwerze MTA" if is_online_mta else "⚪ Offline w grze",
        inline=True
    )

    if game_info:
        embed.add_field(name="⏱️ Punkty RP", value=game_info.get("rp_points") or "Brak", inline=True)
        if game_info.get("reputation"):
            embed.add_field(name="⭐ Reputacja", value=str(game_info.get("reputation")), inline=True)

    if forum_ref and forum_ref.get("profile_url"):
        embed.add_field(name="🔗 Profil Forum", value=f"[Otwórz profil na forum]({forum_ref['profile_url']})", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


async def execute_application_decision_external(
    bot,
    guild: Optional[discord.Guild],
    user_id: int,
    action: str,
    admin_name: str,
    admin_id: int = 0,
    reason: Optional[str] = None
) -> dict:
    """Wykonuje decyzję o akceptacji/odrzuceniu/usunięciu aplikacji z poziomu panelu Web."""
    applicant = guild.get_member(user_id) if guild else None
    if applicant is None and guild:
        try:
            applicant = await guild.fetch_member(user_id)
        except Exception:
            applicant = None

    guild_id = guild.id if guild else 0

    if action == "delete":
        await bot.db.execute(
            "DELETE FROM applications WHERE user_id = ? AND (guild_id = ? OR guild_id = 0)",
            (user_id, guild_id)
        )
        return {
            "success": True,
            "action": "delete",
            "deleted": True,
            "applicant_found": applicant is not None
        }

    role_added = False
    added_roles = []
    recruit_removed = False

    if action == "accept" and applicant and guild:
        role_ids = await _get_accept_role_ids(bot.db, guild.id)
        roles_to_add = [guild.get_role(r_id) for r_id in role_ids if guild.get_role(r_id)]
        if roles_to_add:
            try:
                await applicant.add_roles(*roles_to_add, reason=f"Aplikacja zaakceptowana w panelu Web przez {admin_name}")
                role_added = True
                added_roles = roles_to_add
            except Exception as e:
                logger.error(f"Błąd nadawania ról z panelu web: {e}")

        # Usunięcie roli rekrut
        recruit_role = _find_recruit_role(guild)
        if recruit_role and recruit_role in applicant.roles:
            try:
                await applicant.remove_roles(recruit_role, reason=f"Aplikacja zaakceptowana w panelu Web przez {admin_name}")
                recruit_removed = True
            except Exception:
                pass

        # DM do kandydata
        try:
            dm_embed = discord.Embed(
                title=f"Witaj w Organizacji Team77, {applicant.display_name}!",
                description=(
                    f"Z przyjemnością informujemy, że Twoje podanie do organizacji **{guild.name}** zostało "
                    f"**pozytywnie rozpatrzone** przez Zarząd ({admin_name})!\n\n"
                    f"Otrzymałeś już rolę **Rekruta** na naszym serwerze Discord."
                ),
                color=0x23A55A
            )
            dm_embed.set_author(name="🎉 GRATULACJE • TWOJE PODANIE ZOSTAŁO ZAAKCEPTOWANE!")
            dm_embed.add_field(
                name="📌 Co należy teraz zrobić?",
                value=(
                    "1. Wejdź na kanał **#ogłoszenia-organizacji** i zapoznaj się z aktualnościami.\n"
                    "2. Skontaktuj się z Zarządem w grze lub na kanale **#wpisowe** w celu uiszczenia 3,000 PLN.\n"
                    "3. Pobierz role powiadomień i wejdź na nasz kanał głosowy!"
                ),
                inline=False
            )
            dm_embed.set_footer(text="Do zobaczenia na serwerze! • Zarząd Team77")
            await applicant.send(embed=dm_embed)
        except Exception:
            pass

    elif action == "reject" and applicant and guild:
        try:
            dm_embed = discord.Embed(
                title="Twoje Podanie Zostało Odrzucone",
                description=(
                    f"Dziękujemy za poświęcony czas i chęć dołączenia do naszej organizacji.\n"
                    f"Niestety, Twoja aplikacja nie została zaakceptowana na obecną chwilę przez zarząd ({admin_name})."
                ),
                color=0xF23F43
            )
            dm_embed.set_author(name="DECYZJA ZARZĄDU • REKRUTACJA TEAM77")
            dm_embed.add_field(
                name="❌ Powód Decyzji",
                value=f"*{reason or 'Zbyt mała ilość godzin na serwerze lub aktywne kary w kartotece.'}*",
                inline=False
            )
            dm_embed.add_field(
                name="⏳ Kolejna Próba",
                value="Możesz złożyć kolejne podanie za **7 dni** po poprawie aktywności.",
                inline=False
            )
            dm_embed.set_footer(text="Życzymy powodzenia na serwerze! • Zarząd Team77")
            await applicant.send(embed=dm_embed)
        except Exception:
            pass

    # Aktualizacja bazy danych (nawet jeśli applicant opuścił serwer)
    await bot.db.execute(
        """
        UPDATE applications
        SET status = ?, decision_by = ?, decision_by_name = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND (guild_id = ? OR guild_id = 0)
        """,
        (action, admin_id, admin_name, reason or ("Kandydat opuścił serwer Discord" if not applicant else None), user_id, guild_id)
    )

    return {
        "success": True,
        "action": action,
        "role_added": role_added,
        "applicant_found": applicant is not None
    }


class ApplicationDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=CUSTOM_ID_TEMPLATE
):
    """Trwały przycisk akcji rekrutacyjnej (Zaakceptuj Kandydata, Odrzuć Podanie, Profil na Forum, Usuń)."""

    def __init__(self, action: str, user_id: int) -> None:
        self.action = action
        self.user_id = user_id

        if action == "accept":
            style = discord.ButtonStyle.success
            label = "Zaakceptuj Kandydata"
            emoji = "✅"
        elif action == "reject":
            style = discord.ButtonStyle.danger
            label = "Odrzuć Podanie"
            emoji = "❌"
        elif action == "lookup":
            style = discord.ButtonStyle.secondary
            label = "Profil na Forum ProjectRPG"
            emoji = "🔍"
        elif action == "delete":
            style = discord.ButtonStyle.secondary
            label = "Usuń"
            emoji = "🗑️"
        else:
            style = discord.ButtonStyle.secondary
            label = action.capitalize()
            emoji = "📋"

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
                embed=error_embed("Brak Uprawnień", "Nie masz uprawnień do zarządzania aplikacjami rekrutacyjnymi."),
                ephemeral=True
            )
            return

        if self.action == "lookup":
            await _handle_lookup(interaction, user_id=self.user_id, target_message=interaction.message)
            return

        # Sprawdź, czy aplikacja nie została już rozpatrzona (brak komponentów w wiadomości)
        if interaction.message and not interaction.message.components:
            await interaction.response.send_message(
                embed=warning_embed("Aplikacja Rozpatrzona", "To zgłoszenie zostało już wcześniej rozpatrzone."),
                ephemeral=True
            )
            return

        if self.action == "reject":
            await interaction.response.send_modal(ApplicationRejectModal(self.user_id, interaction.message))
            return

        if self.action == "delete":
            await _handle_delete(interaction, user_id=self.user_id, target_message=interaction.message)
            return

        await _handle_decision(
            interaction,
            action="accept",
            user_id=self.user_id,
            target_message=interaction.message
        )
        return

