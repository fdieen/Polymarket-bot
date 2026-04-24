# Polymarket Bot — CLAUDE.md

## Architectuur
- **Lokaal pad**: `/Users/sem/polymarket-bot/`
- **Server**: Hetzner `root@204.168.240.156` (Helsinki, **Finland**) — dit is de POLYMARKET server
- **Server pad**: `/opt/polymarket/`
- **Let op**: `178.104.85.94` is de Robinhood server (Nürnberg, Duitsland) — NIET voor Polymarket

## BELANGRIJK: Geoblock
De bot moet **op de Finland server draaien**, NIET lokaal en NIET op de Duitsland server.

- Lokaal (NL IP) → Polymarket geeft `403: Trading restricted in your region`
- Hetzner DE `178.104.85.94` → datacenter-IP geblokkeerd voor POST /order
- Hetzner FI `204.168.240.156` → werkt, Finland is niet geblokkeerd
- **Nooit de bot lokaal of op DE server draaien voor live trading**

## Deploy flow
```bash
rsync -avz /Users/sem/polymarket-bot/ root@204.168.240.156:/opt/polymarket/ \
  --exclude venv --exclude __pycache__ --exclude '*.pyc' --exclude logs
ssh root@204.168.240.156 "cd /opt/polymarket && pm2 restart polymarket-bot"
```

## PM2 op server
```bash
ssh root@204.168.240.156 "pm2 list"
ssh root@204.168.240.156 "pm2 logs polymarket-bot --lines 50"
```

## Lokaal draaien (alleen voor ontwikkelen / testen)
```bash
cd /Users/sem/polymarket-bot
venv/bin/python run.py --live   # WERKT NIET voor live orders vanwege geoblock
venv/bin/python weather_scanner.py  # scannen/analyseren mag wel
```

## Bot structuur
| Bestand | Functie |
|---------|---------|
| `run.py` | Hoofdproces — start alle threads |
| `auto_trader.py` | Scanner + order plaatsing + whale copy |
| `weather_scanner.py` | Polymarket temp markten vs weermodel |
| `whale_tracker.py` | Monitort alpha wallets (ColdMath, BeefSlayer) |
| `portfolio.py` | Positiebeheer, PnL tracking |
| `trade.py` | Order execution via CLOB API |
| `alerts.py` | Telegram notificaties |

## Huidige strategie instellingen
```python
MIN_GAP         = 0.35   # minimaal 35% gap model vs poly
EXACT_BAND_MIN_GAP = 0.50  # "between X-Y" markten: 50% (56% WR historisch)
MIN_YES_PRICE   = 0.20   # geen YES-bets onder 20¢
TAIL_BET_MIN    = 0.15   # geen whale-copy onder 15¢ (0% WR historisch onder 10¢)
TROPICAL_MIN_GAP = 0.55  # tropische steden: 55% gap
MARINE_MIN_GAP  = 0.50   # marine steden (SF, Seattle, LA): 50% gap
DATE_FILTER     = 1      # minimaal 1 dag vooruit (was 2 — te weinig kansen)
AUTO_MAX_TRADE  = 25     # max $25 per trade (was $50 — te groot voor saldo)
AUTO_DAILY_BUDGET = 100  # max $100/dag (was $300 — te agressief, ~66% van kapitaal)
```

## Whale copy filters
- ColdMath: skip boven **70¢** (83% WR maar -$44 PnL door asymmetrie: wint $0.30, verliest $25)
- BeefSlayer: skip boven **90¢**
- Beide: skip onder **15¢** (tail bets, 0% WR historisch)

## Historische performance (t/m april 2026)
- Whale copy: 27 trades | 70% WR | +$279
- Model: 4 trades | 50% WR | -$17
- Beste strategie: `BELOW/LOWER` markten (100% WR, +$294)
- Slechtste: exact band YES-bets + tail bets onder 10¢

## PnL checken
```bash
venv/bin/python -c "
from portfolio import load_portfolio
p = load_portfolio()
print(f'Cash: \${p.cash:.2f} | Winst: \${p.total_pnl:.2f} | Open: {len([x for x in p.positions if x[\"status\"]==\"open\"])} posities')
"
```

## Bekende issues / besluiten
- **403 lokaal**: altijd geoblock vanuit NL — bot hoort op Hetzner te draaien
- **ColdMath asymmetrie**: hoge WR maar negatieve PnL door kleine winsten + grote verliezen
- **Exact band markten**: nauwelijks winstgevend (56% WR) — hogere gap drempel ingezet
