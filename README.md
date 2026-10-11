# Aegis Bot — Zaawansowany Bot Discord w Pythonie 3.12 (discord.py 2.x)

Dedykowany, modułowy, profesjonalny i kompletny bot Discord zaprojektowany dla dużych serwerów. Oparty w pełni na interfejsie **Slash Commands (`app_commands`)**, bazie danych **SQLite (aiosqlite)** oraz architekturze **Cogs**.

---

## 📋 Spis Treści
1. [Wymagania](#-wymagania)
2. [Struktura Projektu](#-struktura-projektu)
3. [Instalacja i Konfiguracja (Windows & Linux)](#-instalacja-i-konfiguracja)
4. [Rejestracja Bota w Discord Developer Portal](#-rejestracja-bota-w-discord-developer-portal)
5. [Instalacja FFmpeg (dla modułu muzycznego)](#-instalacja-ffmpeg)
6. [Uruchomienie Bota](#-uruchomienie-bota)
7. [Rozwiązywanie Problemów](#-rozwiązywanie-problemów)

---

## ⚙️ Wymagania
* **Python 3.12** lub nowszy
* **FFmpeg** (wymagany do odtwarzania muzyki z YouTube)
* Pakiety określone w `requirements.txt`:
  * `discord.py>=2.3.2`
  * `aiosqlite>=0.19.0`
  * `python-dotenv>=1.0.0`
  * `aiohttp>=3.9.0`
  * `Pillow>=10.2.0`
  * `yt-dlp>=2024.3.10`
  * `PyNaCl>=1.5.0`

---

## 📁 Struktura Projektu

```text
discord_bot/
│
├── main.py                    # Główny plik startowy aplikacji
├── config.py                  # Główna konfiguracja i motywy embedów
├── requirements.txt           # Zależności bibliotek Pythona
├── .env.example               # Wzorzec pliku zmiennych środowiskowych
├── README.md                  # Dokumentacja instruktażowa
│
├── core/                      # Rdzeń aplikacji
│   ├── bot.py                 # Klasa główna AegisBot
│   ├── database.py            # Menadżer bazy danych SQLite i schemat
│   ├── checks.py              # Weryfikacja uprawnień komend
│   ├── logging_config.py      # Konfiguracja rotacji logów
│   └── constants.py           # Stałe i enumy systemowe
│
├── cogs/                      # Rozszerzenia funkcjonalne (Moduły Cogs)
│   ├── moderation.py          # System moderacji i kar
│   ├── administration.py      # Konfiguracja serwera (/config)
│   ├── tickets.py             # System zgłoszeń
│   ├── verification.py        # Weryfikacja nowych użytkowników
│   ├── leveling.py            # Poziomy i XP za aktywność
│   ├── economy.py             # Waluta, sklep i praca
│   ├── welcome.py             # Powitania i pożegnania
│   ├── reaction_roles.py      # Panele wybieralnych ról
│   ├── suggestions.py         # System propozycji
│   ├── giveaways.py           # Konkursy i losowania
│   ├── utility.py             # Komendy narzędziowe
│   ├── fun.py                 # Komendy rozrywkowe i gry
│   ├── music.py               # Odtwarzacz muzyczny
│   ├── logs.py                # Logowanie zdarzeń serwera
│   └── owner.py               # Zarządzanie botem przez właściciela
│
├── views/                     # Interaktywne komponenty Discord (UI Views)
│   ├── ticket_views.py
│   ├── verification_views.py
│   ├── role_views.py
│   ├── pagination.py
│   └── confirmation.py
│
├── services/                  # Logika biznesowa i serwisy
│   ├── permission_service.py
│   ├── punishment_service.py
│   ├── economy_service.py
│   ├── leveling_service.py
│   └── transcript_service.py
│
├── utils/                     # Pomocnicze funkcje i formatowanie
│   ├── embeds.py
│   ├── converters.py
│   ├── cooldowns.py
│   ├── time_parser.py
│   └── helpers.py
│
└── data/                      # Dane bota (Baza danych bot.db)
    └── bot.db
```

---

## 🚀 Instalacja i Konfiguracja

### Krok 1: Klonowanie / Pobranie projektu
Upewnij się, że pliki projektu znajdują się w wybranym katalogu.

### Krok 2: Przygotowanie środowiska venv i instalacja zależności

#### 🪟 Windows (PowerShell / Command Prompt):
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

#### 🐧 Linux / macOS (Bash / Zsh):
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 🔑 Rejestracja Bota w Discord Developer Portal

1. Przejdź do [Discord Developer Portal](https://discord.com/developers/applications).
2. Kliknij **New Application**, wpisz nazwę bota i zatwierdź.
3. Przejdź do zakładki **Bot**:
   * Kliknij **Reset Token** i skopiuj wygenerowany token.
   * Włącz opcje **Privileged Gateway Intents**:
     * ✅ **PRESENCE INTENT**
     * ✅ **SERVER MEMBERS INTENT**
     * ✅ **MESSAGE CONTENT INTENT**
4. Utwórz plik `.env` w głównym katalogu bota (na podstawie `.env.example`):
```env
DISCORD_TOKEN=TWÓJ_TAJNY_TOKEN_TUTAJ
OWNER_ID=TWÓJ_DISCORD_ID_UŻYTKOWNIKA
TEST_GUILD_ID=ID_SERWERA_TESTOWEGO_(OPCJONALNIE)

# Baza Danych MySQL (GameHost / VPS / Zewnętrzna)
DB_TYPE=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=uzytkownik_bazy
MYSQL_PASSWORD=haslo_bazy
MYSQL_DATABASE=nazwa_bazy
```
*Uwaga: Jeśli nie podasz danych MySQL lub ustawisz `DB_TYPE=sqlite`, bot automatycznie korzysta z lokalnej bazy SQLite (`data/bot.db`).*

### 🗄️ Migracja z SQLite do MySQL
Jeśli masz już dane w `data/bot.db` i chcesz przenieść wszystko (poziomy, zgłoszenia, wpłaty, logi itp.) do bazy MySQL:
```bash
python migrate_sqlite_to_mysql.py
```

### 🔄 Aktualizacje plików na hoście przez Git
Bot posiada wsparcie dla wygodnych aktualizacji:
* **Na serwerze Linux / GameHost**:
  ```bash
  bash update.sh
  ```
* **Bezpośrednio z poziomu Discorda**: jako właściciel bota wpisz komendę slash `/owner git_pull` – bot automatycznie pobierze zmiany z repozytorium Git i przeładuje moduły bez przestojów!
* **W konsoli Windows**:
  ```cmd
  update.bat
  ```

5. W zakładce **OAuth2 -> URL Generator** zaznacz:
   * Scopes: `bot`, `applications.commands`
   * Bot Permissions: `Administrator` (lub odpowiednie uprawnienia moderacyjne, zarządzania kanałami i rolami).
6. Skopiuj link i wygeneruj zaproszenie, aby dodać bota na swój serwer.

---

## 🎵 Instalacja FFmpeg

Moduł muzyczny wymaga programu FFmpeg w ścieżce systemowej (`PATH`).

* **Windows**: Pobierz wersję produkcyjną z [ffmpeg.org](https://ffmpeg.org/download.html), rozpakuj np. do `C:\ffmpeg` i dodaj podkatalog `C:\ffmpeg\bin` do zmiennych środowiskowych systemowych (`PATH`).
* **Linux (Ubuntu/Debian)**:
```bash
sudo apt update && sudo apt install -y ffmpeg
```
* **macOS (Homebrew)**:
```bash
brew install ffmpeg
```

---

## ▶️ Uruchomienie Bota

#### Windows:
```powershell
venv\Scripts\activate
python main.py
```

#### Linux / macOS:
```bash
source venv/bin/activate
python3 main.py
```

---

## ❓ Rozwiązywanie Problemów

* **`disallowed intents`**: Upewnij się, że włączyłeś wszystkie 3 **Privileged Gateway Intents** w Discord Developer Portal dla swojej aplikacji.
* **Komendy slash nie pojawiają się na serwerze**: Jeśli nie podałeś `TEST_GUILD_ID`, synchronizacja globalna Discorda może trwać do 1 godziny. Podaj `TEST_GUILD_ID` w `.env`, aby komendy pojawiły się natychmiastowo.
* **Błąd odtwarzacza muzyki / PyNaCl**: Upewnij się, że pakiet `PyNaCl` jest poprawnie zainstalowany i system posiada wkompilowane biblioteki audio.
