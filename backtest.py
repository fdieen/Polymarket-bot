"""
Backtest — Weather Temperature Arbitrage

Simuleert trades op opgeloste Polymarket temperatuurmarkten (jan-apr 2026).
Marktdata: ColdMath's positie-history (904 temperature markten).
Temperatuur: Open-Meteo Archive API (werkelijke waarden).
Entry prijs: CLOB prices-history ~48u voor sluiting.

Run:       venv/bin/python backtest.py
Snel:      venv/bin/python backtest.py --quick   (eerste 100 markten)
"""
import json, sys, time, os, requests, statistics
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from weather_scanner import CITIES, parse_temperature_question, model_probability

DATA_API    = "https://data-api.polymarket.com"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
CLOB_API    = "https://clob.polymarket.com"

# ── Parameters ────────────────────────────────────────────────────────────────
MIN_GAP      = 0.20
MIN_ENTRY    = 0.10   # minimale entry prijs — sluit tail bets (<10¢) uit
TRADE_AMOUNT = 10.0
START_DATE   = "2026-01-01"
END_DATE     = "2026-04-06"
WHALE        = "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11"  # ColdMath
QUICK        = "--quick" in sys.argv


@dataclass
class BT:
    question: str; city: str; date: str; direction: str
    entry: float; model: float; gap: float
    actual: float; won: bool; pnl: float
    cid: str = ""


# ── Data ophalen ──────────────────────────────────────────────────────────────

def fetch_markets() -> list[dict]:
    print(f"  ColdMath posities ophalen...")
    all_pos, offset = [], 0
    while True:
        r = requests.get(f"{DATA_API}/positions",
            params={"user": WHALE, "limit": 500, "offset": offset, "sizeThreshold": 0},
            timeout=15)
        if not r.ok: break
        batch = r.json()
        if not batch: break
        all_pos.extend(batch)
        offset += 500
        if len(batch) < 500: break
        time.sleep(0.2)

    markets = [p for p in all_pos
               if "temperature" in p.get("title","").lower()
               and START_DATE <= (p.get("endDate","") or "")[:10] <= END_DATE]
    print(f"  {len(markets)} temperature markten gevonden ({START_DATE}→{END_DATE})")
    if QUICK:
        markets = markets[:100]
        print(f"  QUICK modus: eerste 100")
    return markets


_cache: dict = {}

def actual_temp(city: str, date: str) -> float | None:
    key = f"{city}:{date}"
    if key in _cache: return _cache[key]
    coords = CITIES.get(city)
    if not coords: return None
    try:
        r = requests.get(ARCHIVE_API, params={
            "latitude": coords[0], "longitude": coords[1],
            "start_date": date, "end_date": date,
            "daily": "temperature_2m_max", "timezone": "auto"}, timeout=10)
        temps = r.json().get("daily",{}).get("temperature_2m_max",[]) if r.ok else []
        result = float(temps[0]) if temps else None
        _cache[key] = result
        return result
    except: return None


def entry_price(cid: str, end_date: str, direction: str, avg_price: float = None) -> float | None:
    if not cid: return None
    try:
        r = requests.get(f"{CLOB_API}/markets/{cid}", timeout=8)
        if not r.ok: return None
        tokens = r.json().get("tokens", [])
        outcome = "yes" if "YES" in direction else "no"
        tok = next((t for t in tokens if t.get("outcome","").lower() == outcome), None) or (tokens[0] if tokens else None)
        if not tok: return None
        token_id = tok.get("token_id","")
        fallback  = float(tok.get("price", 0)) or None

        target_ts = int((datetime.fromisoformat(end_date[:10]) - timedelta(hours=48)).timestamp())
        r2 = requests.get(f"{CLOB_API}/prices-history",
            params={"market": token_id, "interval": "1d", "fidelity": 1}, timeout=10)
        if not r2.ok: return fallback
        history = r2.json().get("history", [])
        if not history: return fallback
        best  = min(history, key=lambda h: abs(h.get("t",0) - target_ts))
        price = float(best.get("p", 0))
        return price if 0 < price < 1 else fallback
    except: return None


def whale_avg_price(m: dict, direction: str) -> float | None:
    """Gebruik ColdMath's gemiddelde inkoopprijs als entry prijs proxy."""
    avg = float(m.get("avgPrice") or 0)
    outcome = (m.get("outcome") or "").lower()
    if avg <= 0 or avg >= 1:
        return None
    # ColdMath's avgPrice is de prijs voor zijn outcome (YES of NO)
    if direction == "BUY YES":
        return avg if outcome == "yes" else (1 - avg)
    else:
        return avg if outcome == "no" else (1 - avg)


# ── Backtest ──────────────────────────────────────────────────────────────────

def run() -> list[BT]:
    print(f"\n── Polymarket Weather Backtest {'(QUICK) ' if QUICK else ''}────────────────")
    markets = fetch_markets()
    trades, skipped = [], 0

    for m in markets:
        q      = m.get("title","") or m.get("question","")
        parsed = parse_temperature_question(q)
        if not parsed: skipped += 1; continue

        city   = parsed["city"]
        date_s = (m.get("endDate","") or "")[:10]
        cid    = m.get("conditionId","")

        # Werkelijke temp
        temp_c = actual_temp(city, date_s)
        if temp_c is None: skipped += 1; continue

        # Uitkomst: curPrice=0 → lost, curPrice=1 → won
        cur = float(m.get("curPrice") or 0)
        # Bepaal resolved_yes vanuit outcome + whale positie
        outcome = (m.get("outcome") or "").lower()
        if outcome == "yes":
            resolved_yes = cur >= 0.99 or cur == 1.0
        elif outcome == "no":
            resolved_yes = cur <= 0.01
        else:
            # Fallback via werkelijke temp
            cond = parsed.get("condition","above")
            lo, hi = parsed.get("temp_low",-999), parsed.get("temp_high",999)
            unit = parsed.get("unit","C")
            from weather_scanner import to_celsius
            lo_c = to_celsius(lo, unit); hi_c = to_celsius(hi, unit)
            resolved_yes = lo_c <= temp_c <= hi_c if cond in ("between","exact") \
                else temp_c >= lo_c if cond == "above" else temp_c <= hi_c

        # Modelkans
        model_prob = model_probability(temp_c, parsed, spread=1.5, days_ahead=1)

        # Entry prijs: CLOB history → whale avgPrice → skip
        direction  = "BUY YES" if model_prob >= 0.5 else "BUY NO"
        ep         = entry_price(cid, date_s, direction)
        if ep is None or ep <= 0 or ep >= 1:
            ep = whale_avg_price(m, direction)
        if ep is None or ep <= 0 or ep >= 1: skipped += 1; continue
        if ep < MIN_ENTRY: skipped += 1; continue  # geen tail bets

        gap_yes = model_prob - ep
        gap_no  = (1 - model_prob) - (1 - ep)

        if abs(gap_yes) >= abs(gap_no) and abs(gap_yes) >= MIN_GAP:
            direction = "BUY YES" if gap_yes > 0 else "BUY NO"
            gap = abs(gap_yes); bet = ep if direction == "BUY YES" else 1 - ep
        elif abs(gap_no) >= MIN_GAP:
            direction = "BUY NO" if gap_no > 0 else "BUY YES"
            gap = abs(gap_no); bet = (1-ep) if direction == "BUY NO" else ep
        else:
            skipped += 1; continue

        shares = TRADE_AMOUNT / bet
        won    = resolved_yes if direction == "BUY YES" else not resolved_yes
        pnl    = round(shares * (1 - bet) if won else -TRADE_AMOUNT, 2)

        trades.append(BT(question=q, city=city, date=date_s, direction=direction,
                         entry=round(bet,4), model=round(model_prob,4),
                         gap=round(gap,4), actual=temp_c, won=won, pnl=pnl, cid=cid))

        icon = "✓" if won else "✗"
        print(f"  {icon} {date_s} {city:18} {direction:8} "
              f"e={bet*100:.0f}% m={model_prob*100:.0f}% gap={gap*100:.0f}% "
              f"temp={temp_c:.1f}°C pnl={pnl:+.2f}")
        time.sleep(0.12)

    print(f"\n  {len(trades)} trades | {skipped} overgeslagen")
    return trades


# ── Resultaten ────────────────────────────────────────────────────────────────

def report(trades: list[BT]):
    if not trades:
        print("\nGeen trades. Probeer MIN_GAP te verlagen (nu {MIN_GAP*100:.0f}%).")
        return

    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]
    total_pnl = sum(t.pnl for t in trades)
    wr = len(wins)/len(trades)*100
    roi = total_pnl / (TRADE_AMOUNT * len(trades)) * 100

    rets = [t.pnl/TRADE_AMOUNT for t in trades]
    mean_r = statistics.mean(rets)
    std_r  = statistics.stdev(rets) if len(rets) > 1 else 0
    sharpe = round((mean_r/std_r)*(150**0.5), 2) if std_r > 1e-9 else 99.0

    equity = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.date):
        equity += t.pnl
        if equity > peak: peak = equity
        dd = (equity-peak)/max(abs(peak), TRADE_AMOUNT)
        if dd < max_dd: max_dd = dd

    gw = sum(t.pnl for t in wins)
    gl = sum(abs(t.pnl) for t in losses)
    pf = round(gw/gl, 2) if gl > 0 else 99.0

    # Per stad
    by_city: dict[str,list] = {}
    for t in trades: by_city.setdefault(t.city,[]).append(t)

    print("\n══ BACKTEST RESULTATEN ════════════════════════════════════")
    print(f"  Periode:       {START_DATE} → {END_DATE}")
    print(f"  Trades:        {len(trades)}  ({len(wins)}W / {len(losses)}L)")
    print(f"  Win rate:      {wr:.1f}%")
    print(f"  Totaal P&L:    ${total_pnl:+.2f}")
    print(f"  ROI:           {roi:+.1f}%  (op ${TRADE_AMOUNT*len(trades):.0f} ingezet)")
    print(f"  Sharpe:        {sharpe}")
    print(f"  Max Drawdown:  {max_dd*100:.1f}%")
    print(f"  Profit Factor: {pf}")
    print()
    print(f"── TOP STEDEN ──────────────────────────────────────────────")
    rows = sorted([(c,ts,sum(1 for t in ts if t.won),sum(t.pnl for t in ts))
                   for c,ts in by_city.items()], key=lambda x:-x[3])
    for city,ts,w,pnl in rows[:15]:
        print(f"  {city:20}  {len(ts):3}tr  {w/len(ts)*100:5.1f}%WR  {pnl:+8.2f}")

    # Opslaan
    os.makedirs("data", exist_ok=True)
    out = {"summary": {"period": f"{START_DATE}→{END_DATE}", "trades": len(trades),
                       "wins": len(wins), "losses": len(losses), "win_rate": round(wr,1),
                       "total_pnl": round(total_pnl,2), "roi_pct": round(roi,1),
                       "sharpe": sharpe, "max_drawdown": round(max_dd*100,1),
                       "profit_factor": pf},
           "trades": [t.__dict__ for t in sorted(trades, key=lambda x: x.date)]}
    with open("data/backtest_results.json","w") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Opgeslagen → data/backtest_results.json")
    print("═══════════════════════════════════════════════════════════")


if __name__ == "__main__":
    trades = run()
    report(trades)
