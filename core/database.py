"""
Moduł obsługi bazy danych z obsługą MySQL (aiomysql) oraz SQLite (aiosqlite).
Zapewnia asynchroniczne połączenie, connection pooling, pełną kompatybilność zapytań SQL
oraz jednolity interfejs dla wszystkich Cogów i serwisów AegisBot.
"""

import logging
import re
from pathlib import Path
from typing import Any

# pyrefly: ignore [missing-import]
import aiosqlite
try:
    # pyrefly: ignore [missing-import]
    import aiomysql
except ImportError:
    aiomysql = None

import config

logger = logging.getLogger("AegisBot.Database")


class DatabaseRow(dict):
    """
    Opakowanie wyniku wiersza bazy danych.
    Zapewnia równoczesny dostęp:
      - słownikowy: row["col_name"], "col_name" in row.keys()
      - indeksowy: row[0], row[1]
      - metodę .get("col_name", default)
    """
    def __init__(self, data: dict, column_order: list[str] | tuple = ()):
        super().__init__(data)
        self._column_order = list(column_order) if column_order else list(data.keys())

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            col_name = self._column_order[key]
            return super().__getitem__(col_name)
        return super().__getitem__(key)

    def keys(self):
        return super().keys()

    def get(self, key: Any, default: Any = None) -> Any:
        if isinstance(key, int):
            if 0 <= key < len(self._column_order):
                return super().get(self._column_order[key], default)
            return default
        return super().get(key, default)


class DatabaseCursorResult:
    """Opakowanie wyniku operacji INSERT/UPDATE/DELETE dla zachowania atrybutów kursora."""
    def __init__(self, lastrowid: int | None = None, rowcount: int = 0) -> None:
        self.lastrowid = lastrowid
        self.rowcount = rowcount


def convert_query_to_mysql(query: str) -> str:
    """
    Konwertuje zapytania ze składni SQLite na kompatybilną z MySQL:
    - INSERT OR IGNORE INTO -> INSERT IGNORE INTO
    - INSERT OR REPLACE INTO -> REPLACE INTO
    - INTEGER PRIMARY KEY AUTOINCREMENT -> BIGINT AUTO_INCREMENT PRIMARY KEY
    - PRAGMA table_info(tbl) -> SHOW COLUMNS FROM tbl
    - Zamiana symbolu ? na %s (poza literałami tekstowymi)
    """
    q = query
    # Zamiana klauzul INSERT
    q = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT IGNORE INTO", q, flags=re.IGNORECASE)
    q = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "REPLACE INTO", q, flags=re.IGNORECASE)

    # Zamiana AUTOINCREMENT SQLite na MySQL
    q = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGINT AUTO_INCREMENT PRIMARY KEY",
        q,
        flags=re.IGNORECASE
    )

    # Zamiana ON CONFLICT (...) DO UPDATE SET na MariaDB/MySQL ON DUPLICATE KEY UPDATE
    q = re.sub(
        r"\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b",
        "ON DUPLICATE KEY UPDATE",
        q,
        flags=re.IGNORECASE
    )

    # Zamiana SQLite excluded.kolumna na MariaDB/MySQL VALUES(kolumna)
    q = re.sub(
        r"\bexcluded\.([a-zA-Z0-9_]+)\b",
        r"VALUES(\1)",
        q,
        flags=re.IGNORECASE
    )

    # Zamiana dwuargumentowego MAX(0, ...) na MySQL GREATEST(0, ...)
    q = re.sub(r"\bMAX\s*\(\s*0\s*,", "GREATEST(0,", q, flags=re.IGNORECASE)

    # Zamiana PRAGMA table_info na SHOW COLUMNS
    pragma_match = re.search(r"PRAGMA\s+table_info\s*\(\s*([a-zA-Z0-9_]+)\s*\)", q, flags=re.IGNORECASE)
    if pragma_match:
        tbl = pragma_match.group(1)
        return f"SHOW COLUMNS FROM `{tbl}`"

    # Zamiana ? na %s poza stringami
    parts = re.split(r"('(?:''|\\'|[^'])*'|\"(?:\"\"|\\\"|[^\"])*\")", q)
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace("?", "%s")
    return "".join(parts)


class Database:
    """
    Asynchroniczny menadżer bazy danych z obsługą MySQL i fallbackiem do SQLite.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or getattr(config, "DB_PATH", Path("data/bot.db"))
        self.db_type = getattr(config, "DB_TYPE", "sqlite").lower()

        # Połączenia SQLite
        self._sqlite_conn: aiosqlite.Connection | None = None

        # Pula połączeń MySQL
        self._mysql_pool: Any = None

    @property
    def is_mysql(self) -> bool:
        return self.db_type == "mysql"

    async def connect(self) -> None:
        """Nawiązuje połączenie z bazą danych (MySQL lub SQLite)."""
        if self.is_mysql:
            if aiomysql is None:
                raise RuntimeError("Pakiet 'aiomysql' nie jest zainstalowany. Zainstaluj go poleceniem: pip install aiomysql cryptography")

            host = getattr(config, "MYSQL_HOST", "127.0.0.1")
            port = getattr(config, "MYSQL_PORT", 3306)
            user = getattr(config, "MYSQL_USER", "root")
            password = getattr(config, "MYSQL_PASSWORD", "")
            db_name = getattr(config, "MYSQL_DATABASE", "aegisbot")
            pool_size = getattr(config, "MYSQL_POOL_SIZE", 10)

            logger.info(f"Nawiązywanie połączenia z bazą MySQL ({user}@{host}:{port}/{db_name})...")
            try:
                self._mysql_pool = await aiomysql.create_pool(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    db=db_name,
                    charset="utf8mb4",
                    autocommit=True,
                    cursorclass=aiomysql.DictCursor,
                    minsize=1,
                    maxsize=max(pool_size, 5),
                )
                logger.info(f"Pomyślnie połączono z bazą danych MySQL: {db_name}@{host}:{port}")
                await self.init_schema()
                return
            except Exception as exc:
                logger.error(
                    f"Nie udało się połączyć z bazą danych MySQL ({host}:{port}/{db_name}): {exc}. "
                    f"Sprawdź dane w pliku .env (MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE).",
                    exc_info=True
                )
                raise exc

        # Domyślnie lub w razie konfiguracji SQLite:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_conn = await aiosqlite.connect(self.db_path)
        self._sqlite_conn.row_factory = aiosqlite.Row
        await self._sqlite_conn.execute("PRAGMA foreign_keys = ON;")
        await self._sqlite_conn.execute("PRAGMA journal_mode = WAL;")
        await self._sqlite_conn.execute("PRAGMA synchronous = NORMAL;")
        await self._sqlite_conn.execute("PRAGMA busy_timeout = 5000;")
        logger.info(f"Połączono z lokalną bazą danych SQLite: {self.db_path}")
        await self.init_schema()

    async def close(self) -> None:
        """Bezpiecznie zamyka połączenie lub pulę połączeń bazy danych."""
        if self._mysql_pool is not None:
            self._mysql_pool.close()
            await self._mysql_pool.wait_closed()
            self._mysql_pool = None
            logger.info("Pula połączeń z bazą MySQL została zamknięta.")

        if self._sqlite_conn is not None:
            await self._sqlite_conn.close()
            self._sqlite_conn = None
            logger.info("Połączenie z bazą SQLite zostało zamknięte.")

    @property
    def conn(self) -> Any:
        if self.is_mysql:
            if self._mysql_pool is None:
                raise RuntimeError("Baza danych MySQL nie jest połączona. Wywołaj connect().")
            return self._mysql_pool
        if self._sqlite_conn is None:
            raise RuntimeError("Baza danych SQLite nie jest połączona. Wywołaj connect().")
        return self._sqlite_conn

    async def init_schema(self) -> None:
        """Tworzy schemat wszystkich tabel bazy danych."""
        if self.is_mysql:
            await self._init_mysql_schema()
        else:
            await self._init_sqlite_schema()

    async def _init_mysql_schema(self) -> None:
        """Inicjalizuje schemat tabel dla silnika MySQL."""
        queries = [
            # Konfiguracja serwera
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id BIGINT PRIMARY KEY,
                welcome_channel_id BIGINT,
                goodbye_channel_id BIGINT,
                log_channel_id BIGINT,
                suggestion_channel_id BIGINT,
                ticket_channel_id BIGINT,
                ticket_category_id BIGINT,
                verified_role_id BIGINT,
                unverified_role_id BIGINT,
                auto_role_id BIGINT,
                mod_role_id BIGINT,
                max_warnings INT DEFAULT 3,
                welcome_message TEXT,
                goodbye_message TEXT,
                verification_mode VARCHAR(32) DEFAULT 'button',
                welcome_enabled TINYINT(1) DEFAULT 1,
                goodbye_enabled TINYINT(1) DEFAULT 1,
                logs_enabled TINYINT(1) DEFAULT 1,
                tickets_enabled TINYINT(1) DEFAULT 1,
                verification_enabled TINYINT(1) DEFAULT 1,
                leveling_enabled TINYINT(1) DEFAULT 1,
                economy_enabled TINYINT(1) DEFAULT 1,
                suggestions_enabled TINYINT(1) DEFAULT 1,
                automod_enabled TINYINT(1) DEFAULT 1,
                stats_channel_id BIGINT,
                weekly_stats_channel_id BIGINT,
                donation_review_channel_id BIGINT,
                donation_announce_channel_id BIGINT,
                donation_reminder_message_id BIGINT,
                member_count_channel_id BIGINT,
                last_joined_channel_id BIGINT,
                member_count_role_id BIGINT,
                marketplace_channel_id BIGINT,
                marketplace_auto_thread TINYINT(1) DEFAULT 1,
                applications_channel_id BIGINT,
                applications_review_channel_id BIGINT,
                applications_management_role_id BIGINT,
                applications_accept_role_id BIGINT,
                member_log_channel_id BIGINT,
                attachment_log_channel_id BIGINT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Role nadawane po akceptacji podania
            """
            CREATE TABLE IF NOT EXISTS application_accept_roles (
                guild_id BIGINT NOT NULL,
                role_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Podania rekrutacyjne
            """
            CREATE TABLE IF NOT EXISTS applications (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                nick VARCHAR(255),
                login VARCHAR(255),
                uid VARCHAR(64),
                age VARCHAR(64),
                rp_points VARCHAR(64),
                recommended_by VARCHAR(255),
                rules_agreed VARCHAR(64),
                fee_agreed VARCHAR(64),
                forum_url VARCHAR(512),
                status VARCHAR(32) DEFAULT 'pending',
                decision_by BIGINT,
                decision_by_name VARCHAR(255),
                decision_reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_applications_guild (guild_id, status),
                INDEX idx_applications_user (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Sprawy moderacyjne
            """
            CREATE TABLE IF NOT EXISTS moderation_cases (
                case_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                action VARCHAR(64) NOT NULL,
                reason TEXT NOT NULL,
                duration BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME,
                active TINYINT(1) DEFAULT 1,
                INDEX idx_cases_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Ostrzeżenia
            """
            CREATE TABLE IF NOT EXISTS warnings (
                warning_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_warnings_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Zgłoszenia (Tickety)
            """
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                category VARCHAR(64) NOT NULL,
                status VARCHAR(32) DEFAULT 'OPEN',
                claimed_by BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                INDEX idx_tickets_guild_status (guild_id, status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS ticket_members (
                ticket_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                PRIMARY KEY (ticket_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Użytkownicy i poziomy
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS levels (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                xp BIGINT DEFAULT 0,
                level INT DEFAULT 0,
                last_xp_gain DATETIME,
                PRIMARY KEY (guild_id, user_id),
                INDEX idx_levels_guild_xp (guild_id, xp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS level_roles (
                guild_id BIGINT NOT NULL,
                level INT NOT NULL,
                role_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, level)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Ekonomia
            """
            CREATE TABLE IF NOT EXISTS economy_accounts (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                wallet BIGINT DEFAULT 0,
                bank BIGINT DEFAULT 0,
                last_daily DATETIME,
                last_weekly DATETIME,
                last_work DATETIME,
                last_rob DATETIME,
                PRIMARY KEY (guild_id, user_id),
                INDEX idx_economy_guild_wallet (guild_id, wallet)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS economy_transactions (
                transaction_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                type VARCHAR(64) NOT NULL,
                description TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS shop_items (
                item_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                price BIGINT NOT NULL,
                role_id BIGINT,
                max_uses INT DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS inventories (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                item_id BIGINT NOT NULL,
                quantity INT DEFAULT 1,
                PRIMARY KEY (guild_id, user_id, item_id),
                FOREIGN KEY (item_id) REFERENCES shop_items (item_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Sugestie
            """
            CREATE TABLE IF NOT EXISTS suggestions (
                suggestion_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                content TEXT NOT NULL,
                status VARCHAR(32) DEFAULT 'PENDING',
                admin_comment TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Konkursy (Giveaways)
            """
            CREATE TABLE IF NOT EXISTS giveaways (
                giveaway_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                prize TEXT NOT NULL,
                winners_count INT DEFAULT 1,
                end_time DATETIME NOT NULL,
                required_role_id BIGINT,
                forbidden_role_id BIGINT,
                min_account_age_days INT DEFAULT 0,
                min_server_age_days INT DEFAULT 0,
                ended TINYINT(1) DEFAULT 0,
                host_id BIGINT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                PRIMARY KEY (giveaway_id, user_id),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways (giveaway_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Przypomnienia
            """
            CREATE TABLE IF NOT EXISTS reminders (
                reminder_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                trigger_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Czarna lista
            """
            CREATE TABLE IF NOT EXISTS blacklists (
                entity_id BIGINT PRIMARY KEY,
                entity_type VARCHAR(32) NOT NULL,
                reason TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Automoderacja
            """
            CREATE TABLE IF NOT EXISTS automod_rules (
                guild_id BIGINT PRIMARY KEY,
                antispam_enabled TINYINT(1) DEFAULT 1,
                max_mentions INT DEFAULT 5,
                block_invites TINYINT(1) DEFAULT 1,
                block_links TINYINT(1) DEFAULT 0,
                caps_limit INT DEFAULT 70,
                ignored_channels TEXT,
                ignored_roles TEXT,
                allowed_links TEXT,
                forbidden_words TEXT,
                action VARCHAR(32) DEFAULT 'delete'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Aktywność
            """
            CREATE TABLE IF NOT EXISTS message_activity (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                date VARCHAR(32) NOT NULL,
                count INT DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, date),
                INDEX idx_message_activity_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS voice_activity (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                date VARCHAR(32) NOT NULL,
                seconds INT DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, date),
                INDEX idx_voice_activity_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Join-to-Create
            """
            CREATE TABLE IF NOT EXISTS jtc_config (
                guild_id BIGINT PRIMARY KEY,
                trigger_channel_id BIGINT NOT NULL,
                category_id BIGINT,
                name_template VARCHAR(255) DEFAULT 'Kanał {user}',
                user_limit INT DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS jtc_channels (
                channel_id BIGINT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                owner_id BIGINT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_jtc_channels_guild (guild_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Wpłaty (Darowizny)
            """
            CREATE TABLE IF NOT EXISTS donations (
                donation_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                donation_type VARCHAR(64) NOT NULL DEFAULT 'dobrowolne',
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                source VARCHAR(64) NOT NULL DEFAULT 'zgloszenie',
                proof_url TEXT,
                reason TEXT,
                submitted_by BIGINT NOT NULL,
                decided_by BIGINT,
                decided_at DATETIME,
                review_message_id BIGINT,
                nick VARCHAR(255),
                login VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_donations_guild_user (guild_id, user_id),
                INDEX idx_donations_guild_status (guild_id, status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Giełda / Pojazdy (Marketplace)
            """
            CREATE TABLE IF NOT EXISTS marketplace_listings (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                thread_id BIGINT,
                seller_id BIGINT NOT NULL,
                listing_type VARCHAR(32) NOT NULL,
                model VARCHAR(255) NOT NULL,
                vuid VARCHAR(64) NOT NULL,
                engine VARCHAR(64) NOT NULL,
                price VARCHAR(64) NOT NULL,
                mileage VARCHAR(64),
                drive VARCHAR(64),
                negotiable VARCHAR(64) DEFAULT 'Do negocjacji',
                tuning_mech TEXT,
                tuning_vis TEXT,
                extras TEXT,
                lights_color VARCHAR(64),
                speedo_color VARCHAR(64),
                is_limited_lights TINYINT(1) DEFAULT 0,
                is_limited_speedo TINYINT(1) DEFAULT 0,
                tuning_cost BIGINT DEFAULT 0,
                image_url TEXT,
                contact_nick VARCHAR(255),
                contact_phone VARCHAR(64),
                notes TEXT,
                status VARCHAR(32) DEFAULT 'ACTIVE',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                bumped_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_marketplace_listings_guild (guild_id, status),
                INDEX idx_marketplace_listings_seller (seller_id, status),
                INDEX idx_marketplace_listings_msg (message_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS marketplace_offers (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                listing_id BIGINT NOT NULL,
                buyer_id BIGINT NOT NULL,
                buyer_nick VARCHAR(255) NOT NULL,
                offer_amount VARCHAR(64) NOT NULL,
                message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_marketplace_offers_listing (listing_id),
                FOREIGN KEY (listing_id) REFERENCES marketplace_listings (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Rangi i kary
            """
            CREATE TABLE IF NOT EXISTS rank_thresholds (
                guild_id BIGINT NOT NULL,
                rank_name VARCHAR(255) NOT NULL,
                threshold INT NOT NULL,
                role_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, rank_name),
                INDEX idx_rank_thresholds_guild (guild_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS rank_points (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                amount INT NOT NULL,
                reason TEXT,
                admin_id BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_rank_points_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS strikes (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                amount INT NOT NULL,
                reason TEXT,
                admin_id BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_strikes_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Diamenty
            """
            CREATE TABLE IF NOT EXISTS user_diamonds (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                diamonds BIGINT NOT NULL DEFAULT 0,
                last_daily VARCHAR(64),
                PRIMARY KEY (guild_id, user_id),
                INDEX idx_user_diamonds_guild (guild_id, diamonds)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Rekruci
            """
            CREATE TABLE IF NOT EXISTS recruit_entries (
                entry_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                nick VARCHAR(255) NOT NULL,
                login VARCHAR(255) NOT NULL,
                uid VARCHAR(64) NOT NULL,
                added_by BIGINT NOT NULL,
                added_date VARCHAR(64) NOT NULL,
                profile_url TEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS recruit_messages (
                channel_id BIGINT PRIMARY KEY,
                message_id BIGINT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Zaproszenia i role
            """
            CREATE TABLE IF NOT EXISTS member_invites (
                guild_id BIGINT NOT NULL,
                inviter_id BIGINT NOT NULL,
                invited_id BIGINT NOT NULL,
                code VARCHAR(64),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, invited_id),
                INDEX idx_member_invites_inviter (guild_id, inviter_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            """
            CREATE TABLE IF NOT EXISTS saved_member_roles (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                roles_json TEXT NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Szablony embedów
            """
            CREATE TABLE IF NOT EXISTS embed_templates (
                template_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                name VARCHAR(128) NOT NULL,
                author_id BIGINT NOT NULL,
                data LONGTEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                UNIQUE KEY uq_guild_name (guild_id, name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """,
            # Nazwy użytkowników
            """
            CREATE TABLE IF NOT EXISTS member_names (
                guild_id VARCHAR(64) NOT NULL,
                user_id VARCHAR(64) NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """
        ]

        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                for q in queries:
                    await cursor.execute(q)
        logger.info("Schemat bazy danych MySQL został pomyślnie zainicjalizowany.")

    async def _init_sqlite_schema(self) -> None:
        """Inicjalizuje schemat tabel dla silnika SQLite."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                welcome_channel_id INTEGER,
                goodbye_channel_id INTEGER,
                log_channel_id INTEGER,
                suggestion_channel_id INTEGER,
                ticket_channel_id INTEGER,
                ticket_category_id INTEGER,
                verified_role_id INTEGER,
                unverified_role_id INTEGER,
                auto_role_id INTEGER,
                mod_role_id INTEGER,
                max_warnings INTEGER DEFAULT 3,
                welcome_message TEXT DEFAULT 'Witaj {user} na serwerze {server}!',
                goodbye_message TEXT DEFAULT 'Użytkownik {username} opuścił serwer.',
                verification_mode TEXT DEFAULT 'button',
                welcome_enabled INTEGER DEFAULT 1,
                goodbye_enabled INTEGER DEFAULT 1,
                logs_enabled INTEGER DEFAULT 1,
                tickets_enabled INTEGER DEFAULT 1,
                verification_enabled INTEGER DEFAULT 1,
                leveling_enabled INTEGER DEFAULT 1,
                economy_enabled INTEGER DEFAULT 1,
                suggestions_enabled INTEGER DEFAULT 1,
                automod_enabled INTEGER DEFAULT 1,
                stats_channel_id INTEGER,
                weekly_stats_channel_id INTEGER,
                donation_review_channel_id INTEGER,
                donation_announce_channel_id INTEGER,
                donation_reminder_message_id INTEGER,
                member_count_channel_id INTEGER,
                last_joined_channel_id INTEGER,
                member_count_role_id INTEGER,
                marketplace_channel_id INTEGER,
                marketplace_auto_thread INTEGER DEFAULT 1,
                applications_channel_id INTEGER,
                applications_review_channel_id INTEGER,
                applications_management_role_id INTEGER,
                applications_accept_role_id INTEGER,
                member_log_channel_id INTEGER,
                attachment_log_channel_id INTEGER
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS application_accept_roles (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                nick TEXT,
                login TEXT,
                uid TEXT,
                age TEXT,
                rp_points TEXT,
                recommended_by TEXT,
                rules_agreed TEXT,
                fee_agreed TEXT,
                forum_url TEXT,
                status TEXT DEFAULT 'pending',
                decision_by INTEGER,
                decision_by_name TEXT,
                decision_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS moderation_cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                duration INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                active INTEGER DEFAULT 1
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS warnings (
                warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                status TEXT DEFAULT 'OPEN',
                claimed_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS ticket_members (
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (ticket_id, user_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS levels (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                last_xp_gain TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS level_roles (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, level)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS economy_accounts (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                wallet INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                last_daily TIMESTAMP,
                last_weekly TIMESTAMP,
                last_work TIMESTAMP,
                last_rob TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS economy_transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS shop_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price INTEGER NOT NULL,
                role_id INTEGER,
                max_uses INTEGER DEFAULT 0
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS inventories (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (guild_id, user_id, item_id),
                FOREIGN KEY (item_id) REFERENCES shop_items (item_id) ON DELETE CASCADE
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS suggestions (
                suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
                admin_comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS giveaways (
                giveaway_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                winners_count INTEGER DEFAULT 1,
                end_time TIMESTAMP NOT NULL,
                required_role_id INTEGER,
                forbidden_role_id INTEGER,
                min_account_age_days INTEGER DEFAULT 0,
                min_server_age_days INTEGER DEFAULT 0,
                ended INTEGER DEFAULT 0,
                host_id INTEGER NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (giveaway_id, user_id),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways (giveaway_id) ON DELETE CASCADE
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                trigger_at TIMESTAMP NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS blacklists (
                entity_id INTEGER PRIMARY KEY,
                entity_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS automod_rules (
                guild_id INTEGER PRIMARY KEY,
                antispam_enabled INTEGER DEFAULT 1,
                max_mentions INTEGER DEFAULT 5,
                block_invites INTEGER DEFAULT 1,
                block_links INTEGER DEFAULT 0,
                caps_limit INTEGER DEFAULT 70,
                ignored_channels TEXT DEFAULT '[]',
                ignored_roles TEXT DEFAULT '[]',
                allowed_links TEXT DEFAULT '[]',
                forbidden_words TEXT DEFAULT '[]',
                action TEXT DEFAULT 'delete'
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS message_activity (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, date)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS voice_activity (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                seconds INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, date)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS jtc_config (
                guild_id INTEGER PRIMARY KEY,
                trigger_channel_id INTEGER NOT NULL,
                category_id INTEGER,
                name_template TEXT DEFAULT 'Kanał {user}',
                user_limit INTEGER DEFAULT 0
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS jtc_channels (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS donations (
                donation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                donation_type TEXT NOT NULL DEFAULT 'dobrowolne',
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT NOT NULL DEFAULT 'zgloszenie',
                proof_url TEXT,
                reason TEXT,
                submitted_by INTEGER NOT NULL,
                decided_by INTEGER,
                decided_at TIMESTAMP,
                review_message_id INTEGER,
                nick TEXT,
                login TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS marketplace_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                thread_id INTEGER,
                seller_id INTEGER NOT NULL,
                listing_type TEXT NOT NULL,
                model TEXT NOT NULL,
                vuid TEXT NOT NULL,
                engine TEXT NOT NULL,
                price TEXT NOT NULL,
                mileage TEXT,
                drive TEXT,
                negotiable TEXT DEFAULT 'Do negocjacji',
                tuning_mech TEXT DEFAULT '{}',
                tuning_vis TEXT DEFAULT '{}',
                extras TEXT DEFAULT '{}',
                lights_color TEXT,
                speedo_color TEXT,
                is_limited_lights INTEGER DEFAULT 0,
                is_limited_speedo INTEGER DEFAULT 0,
                tuning_cost INTEGER DEFAULT 0,
                image_url TEXT,
                contact_nick TEXT,
                contact_phone TEXT,
                notes TEXT,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                bumped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS marketplace_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL,
                buyer_id INTEGER NOT NULL,
                buyer_nick TEXT NOT NULL,
                offer_amount TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (listing_id) REFERENCES marketplace_listings (id) ON DELETE CASCADE
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS rank_thresholds (
                guild_id INTEGER NOT NULL,
                rank_name TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, rank_name)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS rank_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS strikes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS user_diamonds (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                diamonds INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                PRIMARY KEY (guild_id, user_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS recruit_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                nick TEXT NOT NULL,
                login TEXT NOT NULL,
                uid TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_date TEXT NOT NULL,
                profile_url TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS recruit_messages (
                channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS member_invites (
                guild_id INTEGER NOT NULL,
                inviter_id INTEGER NOT NULL,
                invited_id INTEGER NOT NULL,
                code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, invited_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS saved_member_roles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                roles_json TEXT NOT NULL,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS embed_templates (
                template_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                author_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(guild_id, name)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS member_names (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """,
            # Indeksy SQLite
            "CREATE INDEX IF NOT EXISTS idx_jtc_channels_guild ON jtc_channels (guild_id);",
            "CREATE INDEX IF NOT EXISTS idx_donations_guild_user ON donations (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_donations_guild_status ON donations (guild_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_message_activity_guild_user ON message_activity (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_voice_activity_guild_user ON voice_activity (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_cases_guild_user ON moderation_cases (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets (guild_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_levels_guild_xp ON levels (guild_id, xp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_economy_guild_wallet ON economy_accounts (guild_id, wallet DESC);",
            "CREATE INDEX IF NOT EXISTS idx_marketplace_listings_guild ON marketplace_listings (guild_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_marketplace_listings_seller ON marketplace_listings (seller_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_marketplace_listings_msg ON marketplace_listings (message_id);",
            "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_listing ON marketplace_offers (listing_id);",
            "CREATE INDEX IF NOT EXISTS idx_rank_points_guild_user ON rank_points (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_strikes_guild_user ON strikes (guild_id, user_id);",
            "CREATE INDEX IF NOT EXISTS idx_rank_thresholds_guild ON rank_thresholds (guild_id);",
            "CREATE INDEX IF NOT EXISTS idx_user_diamonds_guild ON user_diamonds (guild_id, diamonds DESC);",
            "CREATE INDEX IF NOT EXISTS idx_member_invites_inviter ON member_invites (guild_id, inviter_id);"
        ]

        async with self._sqlite_conn.cursor() as cursor:
            for q in queries:
                await cursor.execute(q)
        await self._sqlite_conn.commit()

        # Migracje dla starszych baz SQLite
        migrations = [
            ("guild_config", "donation_review_channel_id", "INTEGER"),
            ("guild_config", "donation_announce_channel_id", "INTEGER"),
            ("guild_config", "donation_reminder_message_id", "INTEGER"),
            ("guild_config", "member_count_channel_id", "INTEGER"),
            ("guild_config", "last_joined_channel_id", "INTEGER"),
            ("guild_config", "member_count_role_id", "INTEGER"),
            ("guild_config", "marketplace_channel_id", "INTEGER"),
            ("guild_config", "marketplace_auto_thread", "INTEGER DEFAULT 1"),
            ("guild_config", "weekly_stats_channel_id", "INTEGER"),
            ("guild_config", "applications_channel_id", "INTEGER"),
            ("guild_config", "applications_review_channel_id", "INTEGER"),
            ("guild_config", "applications_management_role_id", "INTEGER"),
            ("guild_config", "applications_accept_role_id", "INTEGER"),
            ("guild_config", "member_log_channel_id", "INTEGER"),
            ("guild_config", "attachment_log_channel_id", "INTEGER"),
            ("donations", "nick", "TEXT"),
            ("donations", "login", "TEXT"),
            ("recruit_entries", "profile_url", "TEXT"),
        ]
        for table, column, col_type in migrations:
            try:
                await self._sqlite_conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                await self._sqlite_conn.commit()
            except aiosqlite.OperationalError:
                pass

        logger.info("Schemat bazy danych SQLite został pomyślnie zainicjalizowany.")

    async def execute(self, query: str, parameters: tuple = ()) -> Any:
        """Wykonuje zapytanie wprowadzające zmiany (INSERT, UPDATE, DELETE, CREATE itp.)."""
        if self.is_mysql:
            sql = convert_query_to_mysql(query)
            try:
                async with self._mysql_pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(sql, parameters)
                        return DatabaseCursorResult(
                            lastrowid=cursor.lastrowid,
                            rowcount=cursor.rowcount
                        )
            except Exception as e:
                # Jeśli błąd to duplikat kolumny w ALTER TABLE, ignoruj
                if "Duplicate column name" in str(e) or "1060" in str(e):
                    return DatabaseCursorResult(lastrowid=None, rowcount=0)
                raise e

        # SQLite
        cursor = await self.conn.execute(query, parameters)
        await self.conn.commit()
        return cursor

    async def fetchone(self, query: str, parameters: tuple = ()) -> Any | None:
        """Pobiera jeden rekord z bazy danych."""
        if self.is_mysql:
            sql = convert_query_to_mysql(query)
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql, parameters)
                    row = await cursor.fetchone()
                    if row is None:
                        return None
                    # Jeśli zapytanie to było SHOW COLUMNS, dodaj mapowanie name = Field
                    if "SHOW COLUMNS FROM" in sql.upper():
                        row["name"] = row.get("Field")
                    col_order = [d[0] for d in cursor.description] if cursor.description else list(row.keys())
                    return DatabaseRow(row, col_order)

        # SQLite
        async with self.conn.execute(query, parameters) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, query: str, parameters: tuple = ()) -> list[Any]:
        """Pobiera wszystkie pasujące rekordy z bazy danych."""
        if self.is_mysql:
            sql = convert_query_to_mysql(query)
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql, parameters)
                    rows = await cursor.fetchall()
                    if not rows:
                        return []
                    col_order = [d[0] for d in cursor.description] if cursor.description else list(rows[0].keys())
                    is_show_cols = "SHOW COLUMNS FROM" in sql.upper()
                    result = []
                    for r in rows:
                        if is_show_cols:
                            r["name"] = r.get("Field")
                        result.append(DatabaseRow(r, col_order))
                    return result

        # SQLite
        async with self.conn.execute(query, parameters) as cursor:
            return await cursor.fetchall()