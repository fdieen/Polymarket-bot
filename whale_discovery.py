"""
Whale Discovery — vindt automatisch traders met een bewezen edge op Polymarket.

Haalt de top wallets op van de leaderboard API, berekent per wallet de
Sharpe ratio, winrate en trade-profiel, en geeft een gerankte lijst terug.

Gebruik:
  venv/bin/python whale_discovery.py          # scan + print top 20
  venv/bin/python whale_discovery.py --save   # opslaan in data/discovered_whales.json
"""

import json
import math
import time
import requests
import argparse
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

DATA_API  = "https://data-api.polymarket.com"
LEADERBOARD_API = f"{DATA_API}/v1/leaderboard"

# Filters — pas aan naar wens
MIN_TRADES      = 30        # minimaal aantal trades
MAX_AVG_SIZE    = 2000      # max gemiddelde trade size in USDC (anders niet te volgen)
MIN_WIN_RATE    = 52.0      # minimale winrate %
MIN_PNL         = 1000      # minimale totale winst in USDC
MAX_WALLETS     = 200       # hoeveel wallets scannen van de leaderboard


@dataclass
class WhaleSummary:
    rank:        int
    address:     str
    name:        str
    pnl:         float       # totale winst USDC
    vol:         float       # totale volume USDC
    win_rate:    float       # % winnende trades
    sharpe:      float       # Sharpe ratio op trade-returns
    avg_size:    float       # gemiddelde trade size USDC
    n_trades:    int         # aantal trades geanalyseerd
    top_cat:     str         # dominante marktcategorie
    cat_pct:     float       # % trades in die categorie
    last_trade:  str         # datum laatste trade
    copyable:    bool        # voldoet aan alle filters


def _fetch_leaderboard(limit: int = MAX_WALLETS) -> list[dict]:
    """Haalt top wallets op van de Polymarket leaderboard."""
    wallets = []
    batch   = 100
    offset  = 0
    while len(wallets) < limit:
        r = requests.get(
            LEADERBOARD_API,
            params={
                "timePeriod": "all",
                "orderBy":    "PNL",
                "limit":      min(batch, limit - len(wallets)),
                "offset":     offset,
                "category":   "overall",
            },
            timeout=10,
        )
        if r.status_code != 200:
            break
        batch_data = r.json()
        if not batch_data:
            break
        wallets.extend(batch_data)
        if len(batch_data) < batch:
            break
        offset += batch
    return wallets


def _categorize(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ["temperature", "weather", "rain", "snow", "wind", "celsius", "fahrenheit"]):
        return "weather"
    if any(x in t for x in ["bitcoin", "btc", "eth", "crypto", "sol", "price"]):
        return "crypto"
    if any(x in t for x in ["nba", "nfl", "mlb", "soccer", "match", "vs.", " vs ", "game", "champion", "finals", "playoff", "tournament", "win the", "masters"]):
        return "sports"
    if any(x in t for x in ["election", "president", "trump", "harris", "democrat", "republican", "senate", "congress", "prime minister", "chancellor"]):
        return "politics"
    if any(x in t for x in ["fed", "rate", "gdp", "inflation", "recession", "market cap"]):
        return "economics"
    return "other"


def _calc_sharpe(returns: list[float]) -> float:
    """Sharpe ratio op basis van per-trade returns (% van inzet)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean  = sum(returns) / n
    var   = sum((r - mean) ** 2 for r in returns) / max(n - 1, 1)
    std   = var ** 0.5
    if std < 1e-9:
        return 99.0 if mean > 0 else 0.0
    # Annualiseer naar ~150 trades/jaar
    annual = mean * 150
    vol_a  = std  * (150 ** 0.5)
    return round(annual / vol_a, 2)


def analyze_wallet(address: str, name: str, pnl: float, vol: float, rank: int) -> WhaleSummary | None:
    """
    Haalt trades + posities op voor één wallet en berekent alle metrics.
    Sharpe berekend op basis van gerealiseerde positie-returns (cashPnl / amount).
    Returns None als te weinig data beschikbaar.
    """
    # ── Trades (voor categorie + size + last_trade) ────────────────────────
    try:
        r = requests.get(
            f"{DATA_API}/activity",
            params={"user": address, "limit": 500},
            timeout=12,
        )
        if r.status_code != 200:
            return None
        trades = [t for t in r.json() if float(t.get("usdcSize") or 0) >= 1.0]
        if len(trades) < MIN_TRADES:
            return None
    except Exception:
        return None

    sizes     = [float(t.get("usdcSize") or 0) for t in trades]
    avg_size  = round(sum(sizes) / len(sizes), 2)

    cats: dict[str, int] = {}
    for t in trades:
        c = _categorize(t.get("title", ""))
        cats[c] = cats.get(c, 0) + 1
    top_cat = max(cats, key=cats.get)
    cat_pct = round(cats[top_cat] / len(trades) * 100, 1)

    timestamps = [int(t.get("timestamp", 0)) for t in trades if t.get("timestamp")]
    last_trade = (
        datetime.fromtimestamp(max(timestamps), tz=timezone.utc).strftime("%Y-%m-%d")
        if timestamps else "?"
    )

    # ── Posities: gebruik voor Sharpe en winrate ───────────────────────────
    returns      = []
    wins         = 0
    total_scored = 0
    try:
        rp = requests.get(
            f"{DATA_API}/positions",
            params={"user": address, "limit": 500, "sizeThreshold": 0.1},
            timeout=12,
        )
        if rp.status_code == 200:
            for pos in rp.json():
                amt  = float(pos.get("size") or pos.get("amount") or 0)
                cur  = float(pos.get("curPrice") or 0)
                pnl_val = float(pos.get("cashPnl") or 0)
                avg_p   = float(pos.get("avgPrice") or 0)
                if avg_p <= 0:
                    continue

                # Gerealiseerde positie: prijs bijna zeker (resolutie)
                if cur >= 0.97 or cur <= 0.03:
                    cost = float(pos.get("size") or 0) * avg_p
                    if cost > 0.5:
                        ret = pnl_val / cost
                        returns.append(ret)
                        total_scored += 1
                        if pnl_val > 0:
                            wins += 1
                else:
                    # Open positie: gebruik unrealized return als indicatief datapunt
                    cost = float(pos.get("size") or 0) * avg_p
                    if cost > 1.0:
                        ret = pnl_val / cost
                        returns.append(ret)
    except Exception:
        pass

    win_rate = round(wins / total_scored * 100, 1) if total_scored >= 5 else 0.0
    sharpe   = _calc_sharpe(returns) if len(returns) >= 5 else round(pnl / max(vol, 1) * 50, 2)

    copyable = (
        avg_size <= MAX_AVG_SIZE
        and pnl >= MIN_PNL
        and (win_rate >= MIN_WIN_RATE or total_scored < 5)
    )

    return WhaleSummary(
        rank=rank,
        address=address,
        name=name,
        pnl=round(pnl, 0),
        vol=round(vol, 0),
        win_rate=win_rate,
        sharpe=sharpe,
        avg_size=avg_size,
        n_trades=len(trades),
        top_cat=top_cat,
        cat_pct=cat_pct,
        last_trade=last_trade,
        copyable=copyable,
    )


def discover(
    max_wallets:  int   = MAX_WALLETS,
    min_sharpe:   float = 0.3,
    verbose:      bool  = True,
) -> list[WhaleSummary]:
    """
    Scant de top wallets en geeft een gerankte lijst terug op Sharpe ratio.

    Args:
        max_wallets:  hoeveel wallets van de leaderboard scannen
        min_sharpe:   minimale Sharpe ratio voor de uitvoer
        verbose:      print voortgang

    Returns:
        list[WhaleSummary] gesorteerd op Sharpe ratio (hoog → laag)
    """
    if verbose:
        print(f"Leaderboard ophalen (top {max_wallets})...")

    leaderboard = _fetch_leaderboard(max_wallets)
    if verbose:
        print(f"  {len(leaderboard)} wallets gevonden")

    results = []
    for i, entry in enumerate(leaderboard):
        addr  = entry.get("proxyWallet", "")
        name  = entry.get("userName", addr[:8])
        pnl   = float(entry.get("pnl") or 0)
        vol   = float(entry.get("vol") or 0)
        rank  = int(entry.get("rank") or i + 1)

        if pnl < MIN_PNL:
            continue  # skip verliezers

        if verbose and i % 10 == 0:
            print(f"  [{i+1}/{len(leaderboard)}] {name}...")

        summary = analyze_wallet(addr, name, pnl, vol, rank)
        if summary and summary.sharpe >= min_sharpe:
            results.append(summary)

        time.sleep(0.15)  # rate limiting

    results.sort(key=lambda x: x.sharpe, reverse=True)
    return results


def display(results: list[WhaleSummary], top: int = 25):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── Whale Discovery [{ts}] — Top {top} op Sharpe ──────────────────────────")
    print(f"{'#':>3} {'Naam':<18} {'Sharpe':>7} {'Winrate':>8} {'PNL':>10} {'AvgSize':>8} {'Cat':<10} {'%':>5} {'Trades':>6}  Adres")
    print("─" * 115)

    shown = 0
    for w in results:
        if shown >= top:
            break
        flag = " ✓" if w.copyable else "  "
        print(
            f"{w.rank:>3} {w.name:<18} {w.sharpe:>7.2f} "
            f"{w.win_rate:>7.1f}% ${w.pnl:>9,.0f} ${w.avg_size:>7,.0f} "
            f"{w.top_cat:<10} {w.cat_pct:>4.0f}% {w.n_trades:>6}{flag}  {w.address}"
        )
        shown += 1

    copyable = [w for w in results if w.copyable]
    print(f"\n  Totaal geanalyseerd: {len(results)} | Kopieerbaar (✓): {len(copyable)}")


def save(results: list[WhaleSummary], path: str = "data/discovered_whales.json"):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count":        len(results),
        "whales":       [asdict(w) for w in results],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  Opgeslagen in {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Whale Discovery")
    parser.add_argument("--save",        action="store_true", help="Sla resultaten op in JSON")
    parser.add_argument("--top",         type=int, default=200, help="Aantal wallets om te scannen")
    parser.add_argument("--min-sharpe",  type=float, default=0.3, help="Minimale Sharpe ratio")
    parser.add_argument("--show",        type=int, default=25, help="Aantal resultaten om te tonen")
    args = parser.parse_args()

    results = discover(max_wallets=args.top, min_sharpe=args.min_sharpe, verbose=True)
    display(results, top=args.show)

    if args.save:
        save(results)
