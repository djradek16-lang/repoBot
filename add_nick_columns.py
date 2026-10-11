"""
Skrypt aktualizujący strukturę bazy danych MySQL:
Dodaje kolumnę `nick VARCHAR(255)` obok `user_id` we wszystkich tabelach,
a następnie uzupełnia brakujące nicki na podstawie tabeli `member_names` oraz API Discorda.
"""

import sys
import asyncio
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.append(str(Path(__file__).resolve().parent))

import pymysql
import pymysql.cursors
import aiohttp
import config

TARGET_TABLES = [
    "user_diamonds",
    "levels",
    "economy_accounts",
    "donations",
    "rank_points",
    "strikes",
    "moderation_cases",
    "warnings",
    "tickets",
    "ticket_members",
    "suggestions",
    "giveaway_entries",
    "inventories",
    "economy_transactions",
    "saved_member_roles",
    "message_activity",
    "voice_activity",
    "user_voice_time",
    "reminders",
    "users"
]

def get_connection():
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

async def fetch_discord_username(session: aiohttp.ClientSession, user_id: int, bot_token: str) -> str | None:
    """Pobiera nazwę użytkownika bezpośrednio z API Discorda, jeśli brak w cache."""
    url = f"https://discord.com/api/v10/users/{user_id}"
    headers = {"Authorization": f"Bot {bot_token}"}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("global_name") or data.get("username")
    except Exception:
        pass
    return None

async def run_migration():
    print("==================================================")
    print("🚀 Rozpoczynanie aktualizacji bazy danych MySQL")
    print(f"🌐 Baza: {config.MYSQL_DATABASE}@{config.MYSQL_HOST}")
    print("==================================================")

    conn = get_connection()
    cursor = conn.cursor()

    # 1. Sprawdzenie, które tabele istnieją i które potrzebują kolumny `nick`
    added_columns = []
    already_had = []

    for table in TARGET_TABLES:
        # Sprawdź, czy tabela w ogóle istnieje
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """, (config.MYSQL_DATABASE, table))
        if cursor.fetchone()["c"] == 0:
            continue

        # Sprawdź, czy ma kolumnę nick
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'nick'
        """, (config.MYSQL_DATABASE, table))
        has_nick = cursor.fetchone()["c"] > 0

        # Sprawdź, czy ma kolumnę user_id
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'user_id'
        """, (config.MYSQL_DATABASE, table))
        has_user_id = cursor.fetchone()["c"] > 0

        if not has_user_id:
            continue

        if not has_nick:
            try:
                alter_sql = f"ALTER TABLE `{table}` ADD COLUMN `nick` VARCHAR(255) NULL AFTER `user_id`"
                cursor.execute(alter_sql)
                added_columns.append(table)
                print(f"✅ Dodano kolumnę `nick` do tabeli: `{table}` (obok `user_id`)")
            except Exception as e:
                print(f"⚠️ Błąd przy dodawaniu do `{table}`: {e}")
        else:
            already_had.append(table)

    print(f"\n📊 Podsumowanie struktury:")
    print(f" - Nowe kolumny dodane w tabelach: {len(added_columns)}")
    print(f" - Tabele już posiadające kolumnę: {len(already_had)}")

    # 2. Załaduj znane nicki z `member_names`
    print("\n[2/3] Ładowanie znanych nicków z tabeli `member_names`...")
    nick_map = {}
    cursor.execute("SELECT user_id, display_name FROM member_names WHERE display_name IS NOT NULL")
    for r in cursor.fetchall():
        uid = int(r["user_id"])
        nick_map[uid] = r["display_name"]
    print(f"✅ Załadowano {len(nick_map)} znanych nicków z bazy.")

    # 3. Zbieranie wszystkich unikalnych user_id, które mają NULL w nicku
    all_missing_uids = set()
    for table in TARGET_TABLES:
        try:
            cursor.execute(f"SELECT DISTINCT user_id FROM `{table}` WHERE nick IS NULL OR nick = ''")
            for r in cursor.fetchall():
                if r.get("user_id"):
                    all_missing_uids.add(int(r["user_id"]))
        except Exception:
            pass

    print(f"\n[3/3] Znaleziono {len(all_missing_uids)} unikalnych użytkowników do uzupełnienia...")

    # Pobierz z Discord API te, których nie ma w nick_map
    to_fetch_from_api = [uid for uid in all_missing_uids if uid not in nick_map]
    if to_fetch_from_api and config.DISCORD_TOKEN:
        print(f"📡 Odpytywanie Discord API o {len(to_fetch_from_api)} brakujących nicków...")
        async with aiohttp.ClientSession() as session:
            for uid in to_fetch_from_api:
                fetched_nick = await fetch_discord_username(session, uid, config.DISCORD_TOKEN)
                if fetched_nick:
                    nick_map[uid] = fetched_nick
                    # Zapisz także do member_names
                    try:
                        cursor.execute("""
                            INSERT INTO member_names (guild_id, user_id, display_name)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE display_name = VALUES(display_name)
                        """, (getattr(config, "MAIN_GUILD_ID", 1538095709163888783), uid, fetched_nick))
                    except Exception:
                        pass
                await asyncio.sleep(0.1)  # Rate limit safety

    # 4. Aktualizacja wartości `nick` we wszystkich tabelach
    print("\n💾 Aktualizowanie kolumn `nick` danymi graczy...")
    updated_total = 0
    for table in TARGET_TABLES:
        try:
            cursor.execute("""
                SELECT COUNT(*) AS c
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'nick'
            """, (config.MYSQL_DATABASE, table))
            if cursor.fetchone()["c"] == 0:
                continue

            for uid, name in nick_map.items():
                res = cursor.execute(f"UPDATE `{table}` SET nick = %s WHERE user_id = %s AND (nick IS NULL OR nick = '')", (name, uid))
                updated_total += res
        except Exception as e:
            print(f"⚠️ Błąd przy aktualizacji `{table}`: {e}")

    cursor.close()
    conn.close()

    print("\n==================================================")
    print(f"🎉 SUKCES! Kolumna `nick` została dodana do wszystkich tabel.")
    print(f"✨ Uzupełniono łącznie {updated_total} wpisów rzeczywistymi nickami!")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(run_migration())
