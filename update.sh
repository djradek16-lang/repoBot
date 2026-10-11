#!/bin/bash
# ==============================================================================
# Skrypt aktualizacji AegisBot na hoście (GameHost / VPS / Linux)
# Uruchom: bash update.sh
# ==============================================================================

echo "=================================================="
echo "🔄 Pobieranie najnowszych zmian z repozytorium Git..."
echo "=================================================="

git fetch --all
git pull origin $(git rev-parse --abbrev-ref HEAD)

echo ""
echo "📦 Sprawdzanie i instalowanie wymaganych pakietów..."
python3 -m pip install -r requirements.txt --quiet

echo ""
echo "✅ Aktualizacja plików z repozytorium zakończona!"
echo "Jeśli bot działa w tle, zrestartuj go w panelu hostingu lub wpisz w Discordzie: /owner restart"
echo "=================================================="
