#!/bin/bash
# Polymarket Bot — Onboarding script
# Uitvoeren op een nieuwe Ubuntu server: bash onboard.sh

set -e

echo "══════════════════════════════════════════"
echo "  Polymarket Bot — Setup"
echo "══════════════════════════════════════════"

# 1. Dependencies
echo "[1/5] Packages installeren..."
apt-get update -q
apt-get install -y python3-pip python3-venv git screen -q

# 2. Bot downloaden
echo "[2/5] Bot downloaden..."
git clone https://github.com/fdieen/Polymarket-bot.git /root/polymarket-bot
cd /root/polymarket-bot

# 3. Venv + packages
echo "[3/5] Python omgeving installeren..."
python3 -m venv venv
venv/bin/pip install -r requirements.txt -q
venv/bin/pip install flask -q

# 4. .env instellen
echo "[4/5] Configuratie..."
cp .env.example .env

echo ""
echo "Vul je gegevens in:"
read -p "Private key (PK): " pk
read -p "Odds API key (optioneel, Enter om over te slaan): " odds

sed -i "s/PK=0x.*/PK=$pk/" .env
if [ ! -z "$odds" ]; then
    sed -i "s/ODDS_API_KEY=/ODDS_API_KEY=$odds/" .env
fi

# 5. API keys genereren
echo "[5/5] Polymarket API keys genereren..."
venv/bin/python setup_keys.py

echo ""
echo "Kopieer de CLOB_API_KEY, CLOB_SECRET en CLOB_PASS_PHRASE hierboven"
echo "en voeg ze toe aan .env:"
echo ""
echo "  nano /root/polymarket-bot/.env"
echo ""
echo "Daarna de bot starten:"
echo "  screen -dmS polybot bash -c 'cd /root/polymarket-bot && venv/bin/python run.py --live > logs/bot.log 2>&1'"
echo ""
echo "En het dashboard:"
echo "  screen -dmS dashboard bash -c 'cd /root/polymarket-bot && venv/bin/python dashboard.py > logs/dashboard.log 2>&1'"
echo ""
echo "══════════════════════════════════════════"
echo "  Setup klaar!"
echo "══════════════════════════════════════════"
