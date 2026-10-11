@echo off
:: ============================================================
::  AegisBot — Instalator Usługi Windows (NSSM)
::  !! Kliknij PRAWYM -> "Uruchom jako administrator" !!
:: ============================================================

set SERVICE_NAME=AegisBot
set BOT_DIR=C:\Users\ja\Desktop\bocik
set PYTHON_EXE=C:\Users\ja\AppData\Local\Programs\Python\Python312\python.exe
set MAIN_SCRIPT=%BOT_DIR%\main.py
set NSSM_EXE=%BOT_DIR%\nssm\nssm.exe
set LOG_DIR=%BOT_DIR%\logs

:: Utwórz folder na logi
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo.
echo  ====================================================
echo   AegisBot - Instalator Uslugi Windows (NSSM)
echo  ====================================================
echo.

:: Sprawdź czy nssm.exe istnieje
if not exist "%NSSM_EXE%" (
    echo  [BLAD] Nie znaleziono %NSSM_EXE%
    echo.
    echo  Pobierz NSSM ze strony: https://nssm.cc/download
    echo  Wypakuj i umieść plik nssm.exe w folderze:
    echo    %BOT_DIR%\nssm\nssm.exe
    echo.
    pause
    exit /b 1
)

:: Usuń istniejącą usługę (jeśli jest)
echo  [1/5] Usuwam stara usluge (jesli istnieje)...
"%NSSM_EXE%" stop %SERVICE_NAME% >nul 2>&1
"%NSSM_EXE%" remove %SERVICE_NAME% confirm >nul 2>&1

:: Zainstaluj nową usługę
echo  [2/5] Instaluje usluge %SERVICE_NAME%...
"%NSSM_EXE%" install %SERVICE_NAME% "%PYTHON_EXE%" "%MAIN_SCRIPT%"

:: Konfiguracja ścieżki roboczej
echo  [3/5] Konfiguruje sciezke robocza i logi...
"%NSSM_EXE%" set %SERVICE_NAME% AppDirectory "%BOT_DIR%"

:: Logi stdout i stderr do pliku
"%NSSM_EXE%" set %SERVICE_NAME% AppStdout "%LOG_DIR%\aegisbot_stdout.log"
"%NSSM_EXE%" set %SERVICE_NAME% AppStderr "%LOG_DIR%\aegisbot_stderr.log"
"%NSSM_EXE%" set %SERVICE_NAME% AppStdoutCreationDisposition 4
"%NSSM_EXE%" set %SERVICE_NAME% AppStderrCreationDisposition 4

:: Auto-restart przy crashu (po 5 sekundach)
"%NSSM_EXE%" set %SERVICE_NAME% AppRestartDelay 5000

:: Opis usługi
"%NSSM_EXE%" set %SERVICE_NAME% Description "AegisBot - Discord Bot dzialajacy 24/7 jako usluga Windows"

:: Typ startowania: automatyczny (przy starcie systemu)
"%NSSM_EXE%" set %SERVICE_NAME% Start SERVICE_AUTO_START

:: Uruchom usługę
echo  [4/5] Uruchamiam usluge...
"%NSSM_EXE%" start %SERVICE_NAME%

:: Sprawdź status
echo  [5/5] Sprawdzam status uslugi...
timeout /t 3 >nul
"%NSSM_EXE%" status %SERVICE_NAME%

echo.
echo  ====================================================
echo   [OK] Usluga AegisBot zainstalowana i uruchomiona!
echo.
echo   Bot startuje automatycznie przy wlaczeniu komputera
echo   i restartuje sie automatycznie po crashu (po 5s).
echo.
echo   Logi bota znajdziesz w:
echo     %LOG_DIR%\aegisbot_stdout.log
echo     %LOG_DIR%\aegisbot_stderr.log
echo.
echo   Zarzadzanie usluga (w PowerShell jako Administrator):
echo     Zatrzymaj : nssm stop AegisBot
echo     Uruchom   : nssm start AegisBot
echo     Usun      : nssm remove AegisBot confirm
echo  ====================================================
echo.
pause
