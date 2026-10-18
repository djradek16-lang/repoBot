import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
def _get_int_env(key: str, default: int = 0) -> int:
    val = os.getenv(key, "").strip()
    if val.isdigit():
        return int(val)
    return default
def _get_bool_env(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip()
OWNER_ID: int = _get_int_env("OWNER_ID", 0)
GUILD_ID: int = _get_int_env("GUILD_ID", 0)
TEST_GUILD_ID: int = _get_int_env("TEST_GUILD_ID", 0) or GUILD_ID
# --- Konfiguracja Bazy Danych (MySQL / SQLite) ---
# Jeśli ustawione jest DB_TYPE=mysql lub podano MYSQL_HOST, bot łączy się z MySQL.
# W przeciwnym razie bot korzysta lokalnie z SQLite (bot.db).
DB_TYPE: str = os.getenv("DB_TYPE", "mysql" if os.getenv("MYSQL_HOST") else "sqlite").strip().lower()
MYSQL_HOST: str = os.getenv("MYSQL_HOST", "").strip()
MYSQL_PORT: int = _get_int_env("MYSQL_PORT", 3306)
MYSQL_USER: str = os.getenv("MYSQL_USER", "").strip()
MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "").strip()
MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "").strip()
MYSQL_POOL_SIZE: int = _get_int_env("MYSQL_POOL_SIZE", 10)
DB_PATH = DATA_DIR / "bot.db"

# --- Konfiguracja węzła Lavalink ---
# Uzupełnij te wartości w pliku .env na serwerze (NIE tutaj w kodzie):
#   LAVALINK_HOST=twoj-host.przyklad.com
#   LAVALINK_PORT=2333
#   LAVALINK_PASSWORD=twoje-haslo
#   LAVALINK_SECURE=true      # true jeśli host używa https/wss (SSL), false dla zwykłego http
LAVALINK_HOST: str = os.getenv("LAVALINK_HOST", "").strip()
LAVALINK_PORT: int = _get_int_env("LAVALINK_PORT", 2333)
LAVALINK_PASSWORD: str = os.getenv("LAVALINK_PASSWORD", "").strip()
LAVALINK_SECURE: bool = _get_bool_env("LAVALINK_SECURE", False)
LAVALINK_IDENTIFIER: str = os.getenv("LAVALINK_IDENTIFIER", "MainNode").strip()

# --- Oficjalny Stream Radia Project FM ---
RADIO_PROJECTFM_URL: str = os.getenv(
    "RADIO_PROJECTFM_URL",
    "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3"
).strip()
RADIO_PROJECTFM_LOGO: str = "https://ramowka.projectrpg.pl/logo.png"
RADIO_PROJECTFM_WEBSITE: str = "https://radio.projectrpg.pl"

# Konfiguracja wyglądu embedów i wytycznych motywu
BOT_NAME: str = "Aegis Bot"
DEFAULT_EMBED_COLOR: int = 0x5865F2  # Blurple
SUCCESS_COLOR: int = 0x57F287        # Zielony
ERROR_COLOR: int = 0xED4245          # Czerwony
WARNING_COLOR: int = 0xFEE75C        # Żółty
INFO_COLOR: int = 0x3498DB           # Błękitny
MODERATION_COLOR: int = 0xE67E22     # Pomarańczowy
LOG_COLOR: int = 0x9B59B6            # Fioletowy
FOOTER_TEXT: str = f"{BOT_NAME} • Team77"
FOOTER_ICON_URL: str = ""

# --- Konfiguracja panelu webowego (Web Dashboard) ---
DASHBOARD_ENABLED: bool = _get_bool_env("DASHBOARD_ENABLED", True)
DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0").strip()
DASHBOARD_PORT: int = _get_int_env("DASHBOARD_PORT", 8080)
DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_PASSWORD", "team77admin").strip()

# Sesja forum ProjectRPG (do pobierania floty organizacji Team77 z /vehicles/)
PRPG_FORUM_MEMBER_ID: str = os.getenv("PRPG_FORUM_MEMBER_ID", "16164").strip()
PRPG_FORUM_LOGIN_KEY: str = os.getenv("PRPG_FORUM_LOGIN_KEY", "f1b77797a1711b742297e11c91167cd1").strip()
PRPG_FORUM_DEVICE_KEY: str = os.getenv("PRPG_FORUM_DEVICE_KEY", "a62ab335b53834dd50dbca260c8a4d84").strip()
PRPG_FORUM_SESSION: str = os.getenv("PRPG_FORUM_SESSION", "6163f36e6602ac7c801690c651620cb9").strip()
