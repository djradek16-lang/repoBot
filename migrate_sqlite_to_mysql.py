"""
Skrypt migracji danych z lokalnej bazy SQLite (data/bot.db) do bazy MySQL.
Bezpiecznie przenosi wszystkie dotychczasowe poziomy (XP), zgłoszenia, wpłaty,
rekreutacje, logi moderacji, konfigurację oraz szablony.
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

# pyrefly: ignore [missing-import]
import pymysql
# pyrefly: ignore [missing-import]
import pymysql.cursors

import config
from core.database import Database

def migrate() -> None:
    db_path = config.DB_PATH
    if not db_path.exists():
        print(f"❌ Plik bazy SQLite nie istnieje: {db_path}")
        sys.exit(1)

    host = config.MYSQL_HOST
    port = config.MYSQL_PORT
    user = config.MYSQL_USER
    password = config.MYSQL_PASSWORD
    db_name = config.MYSQL_DATABASE

    if not host or not user or not db_name:
        print("❌ Brak konfiguracji MySQL w pliku .env!")
        print("   Upewnij się, że uzupełniłeś:")
        print("   MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE")
        sys.exit(1)

    print("==================================================")
    print("🚀 Rozpoczynanie migracji danych: SQLite -> MySQL")
    print(f"📁 SQLite: {db_path}")
    print(f"🌐 MySQL:  {user}@{host}:{port}/{db_name}")
    print("==================================================")

    # 1. Inicjalizacja schematu w MySQL
    print("\n[1/3] Inicjalizacja schematu tabel w bazie MySQL...")
    async def init_schema():
        original_db_type = config.DB_TYPE
        config.DB_TYPE = "mysql"
        db = Database()
        await db.connect()
        await db.close()
        config.DB_TYPE = original_db_type

    asyncio.run(init_schema())
    print("✅ Schemat tabel w MySQL jest gotowy.")

    # 2. Połączenie z SQLite i MySQL synchronicznie dla szybkiej migracji
    print("\n[2/3] Łączenie z obiema bazami...")
    sqlite_conn = sqlite3.connect(db_path)
    sqlite_conn.row_factory = sqlite3.Row

    mysql_conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db_name,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor
    )

    # Pobierz listę tabel z SQLite
    cur = sqlite_conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row["name"] for row in cur.fetchall()]

    print(f"Znaleziono {len(tables)} tabel w SQLite do zmigrowania.\n")

    # 3. Kopiowanie danych
    print("[3/3] Przenoszenie rekordów...")
    total_migrated = 0

    with mysql_conn.cursor() as mysql_cur:
        for table in tables:
            # Sprawdź czy tabela istnieje w MySQL
            try:
                mysql_cur.execute(f"SHOW COLUMNS FROM `{table}`;")
                mysql_cols = {col["Field"] for col in mysql_cur.fetchall()}
            except Exception:
                print(f"  ⚠️ Pomijam tabelę '{table}' (nie istnieje w schemacie MySQL).")
                continue

            # Pobierz dane z SQLite
            cur.execute(f"SELECT * FROM [{table}];")
            rows = cur.fetchall()
            if not rows:
                print(f"  ℹ️ Tabela '{table}': pusta (0 wierszy).")
                continue

            # Przefiltruj kolumny tylko do tych, które faktycznie istnieją w tabeli docelowej MySQL
            sample_keys = [k for k in rows[0].keys() if k in mysql_cols]
            if not sample_keys:
                continue

            col_names_sql = ", ".join(f"`{k}`" for k in sample_keys)
            placeholders = ", ".join(["%s"] * len(sample_keys))
            insert_sql = f"INSERT IGNORE INTO `{table}` ({col_names_sql}) VALUES ({placeholders})"

            migrated_count = 0
            for row in rows:
                vals = [row[k] for k in sample_keys]
                try:
                    mysql_cur.execute(insert_sql, vals)
                    migrated_count += 1
                except Exception as e:
                    print(f"    ⚠️ Błąd wiersza w '{table}': {e}")

            mysql_conn.commit()
            total_migrated += migrated_count
            print(f"  ✅ Tabela '{table}': pomyślnie zmigrowano {migrated_count}/{len(rows)} wierszy.")

    sqlite_conn.close()
    mysql_conn.close()

    print("\n==================================================")
    print(f"🎉 Migracja zakończona sukcesem!")
    print(f"Łącznie przeniesiono {total_migrated} rekordów do MySQL.")
    print("==================================================")

if __name__ == "__main__":
    migrate()
