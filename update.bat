@echo off
chcp 65001 >nul
echo ==================================================
echo  Pobieranie zmian z repozytorium Git...
echo ==================================================
git pull
echo.
echo  Sprawdzanie i instalowanie pakietow...
python -m pip install -r requirements.txt --quiet
echo.
echo ==================================================
echo  [OK] Pliki bota zaktualizowane pomyslnie!
echo ==================================================
pause
