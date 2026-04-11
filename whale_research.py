"""
Whale Research Script — Vindt de beste Polymarket traders met aantoonbare edge.

Stap 1: Verzamel seed wallets via bekende whales
Stap 2: Analyseer elk wallet (PnL, win rate, volume, strategie)
Stap 3: Filter op kwaliteit
Stap 4: Genereer rapport
"""

import json
import time
import os
import requests
from datetime import datetime, timezone
from collections import defaultdict

DATA_API = "https://data-api.polymarket.com"

SEED_WALLETS = {
    "ColdMath":  "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11",
    "Tetranode": "0xa5ff62b194b46176eed0803ca5c02165194b52cd",
    "Nevo":      "0x0b139a844402c57b1853687e90aa9fb38a0e41d0",
}

OUTPUT_PATH = "/Users/sem/polymarket-bot/data/whale_research.md"


def get(url, params=None, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                print(f"    Rate limited, wacht 5s...")
                time.sleep(5)
            else:
                return None
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
            else:
                return None
    return None


def fetch_activity(user=None, market=None, limit=100):
    params = {"limit": limit}
    if user:
        params["user"] = user
    if market:
        params["market"] = market
    data = get(f"{DATA_API}/activity", params)
    time.sleep(0.3)
    return data or []


def fetch_positions(user, limit=100):
    data = get(f"{DATA_API}/positions", {"user": user, "limit": limit, "sizeThreshold": 0.01})
    time.sleep(0.3)
    return data or []


def fetch_profile(address):
    data = get(f"{DATA_API}/profile", {"address": address})
    time.sleep(0.3)
    if isinstance(data, list) and data:
        return data[0]
    return data or {}


def classify_market(title):
    title_lower = (title or "").lower()
    if any(w in title_lower for w in ["bitcoin", "btc", "eth", "ethereum", "crypto", "solana", "doge", "xrp", "bnb"]):
        return "crypto"
    if any(w in title_lower for w in ["temperature", "weather", "rain", "snow", "hurricane", "storm", "celsius", "fahrenheit"]):
        return "weather"
    if any(w in title_lower for w in ["president", "election", "senate", "congress", "trump", "biden", "harris", "vote", "political", "democrat", "republican", "parliament", "minister"]):
        return "politiek"
    if any(w in title_lower for w in ["nba", "nfl", "soccer", "football", "baseball", "tennis", "sport", "championship", "league", "cup", "game", "match", "score", "team"]):
        return "sport"
    if any(w in title_lower for w in ["fed", "interest rate", "gdp", "inflation", "recession", "unemployment", "economy", "market cap", "nasdaq", "s&p"]):
        return "macro"
    if any(w in title_lower for w in ["ai", "artificial intelligence", "gpt", "openai", "anthropic", "llm"]):
        return "tech/ai"
    return "overig"


def collect_leaderboard_wallets() -> dict:
    """Haal wallets op van de Polymarket leaderboard pagina."""
    import re
    print("  Leaderboard scrapen...")
    try:
        r = requests.get(
            "https://polymarket.com/leaderboard",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=12,
        )
        wallets = list(set(re.findall(r'0x[a-fA-F0-9]{40}', r.text)))
        result = {w[:8] + "...LB": w for w in wallets}
        print(f"  {len(result)} wallets gevonden op leaderboard")
        return result
    except Exception as e:
        print(f"  Leaderboard scrape mislukt: {e}")
        return {}


def collect_wallet_candidates():
    """Stap 1: Verzamel 50-100 unieke wallets."""
    print("\n=== STAP 1: WALLETS VERZAMELEN ===")
    all_wallets = dict(SEED_WALLETS)

    # Voeg leaderboard wallets toe als extra bron
    lb_wallets = collect_leaderboard_wallets()
    for label, addr in lb_wallets.items():
        if addr not in all_wallets.values():
            all_wallets[label] = addr

    seen = set(all_wallets.values())

    for whale_name, whale_addr in SEED_WALLETS.items():
        print(f"\n  Trades ophalen voor {whale_name}...")
        trades = fetch_activity(user=whale_addr, limit=50)
        print(f"  {len(trades)} trades gevonden")

        # Unieke markten
        markets = list({t.get("conditionId", "") for t in trades if t.get("conditionId")})[:15]
        print(f"  {len(markets)} unieke markten gevonden")

        for cid in markets:
            market_traders = fetch_activity(market=cid, limit=50)
            for t in market_traders:
                wallet = t.get("proxyWallet", "")
                if wallet and len(wallet) == 42 and wallet not in seen:
                    seen.add(wallet)
                    label = wallet[:8] + "..."
                    all_wallets[label] = wallet

        print(f"  Totaal wallets tot nu toe: {len(all_wallets)}")

        if len(all_wallets) >= 100:
            break

    print(f"\n  Totaal unieke wallets verzameld: {len(all_wallets)}")
    return all_wallets


def analyze_wallet(name, address):
    """Stap 2: Analyseer één wallet grondig."""
    result = {
        "name": name,
        "address": address,
        "error": None,
        "total_volume": 0,
        "total_pnl": 0,
        "win_rate": 0,
        "closed_trades": 0,
        "won_trades": 0,
        "lost_trades": 0,
        "avg_entry_price": 0,
        "avg_trade_size": 0,
        "dominant_market": "overig",
        "market_distribution": {},
        "trade_count": 0,
        "is_hindsight": False,
        "qualifies": False,
        "open_positions": 0,
    }

    # Haal activiteit op
    activity = fetch_activity(user=address, limit=100)
    if not activity:
        result["error"] = "geen activiteit"
        return result

    result["trade_count"] = len(activity)

    # Bereken volume en entry prijzen
    volumes = []
    entry_prices = []
    market_types = []

    for t in activity:
        size = float(t.get("usdcSize") or 0)
        price = float(t.get("price") or 0)
        title = t.get("title", "")

        if size > 0:
            volumes.append(size)
        if price > 0 and t.get("side") == "BUY":
            entry_prices.append(price)
        market_types.append(classify_market(title))

    result["total_volume"] = sum(volumes)
    result["avg_trade_size"] = sum(volumes) / len(volumes) if volumes else 0
    result["avg_entry_price"] = sum(entry_prices) / len(entry_prices) if entry_prices else 0

    # Markt distributie
    dist = defaultdict(int)
    for m in market_types:
        dist[m] += 1
    result["market_distribution"] = dict(dist)
    if dist:
        result["dominant_market"] = max(dist, key=dist.get)

    # Hindsight check
    result["is_hindsight"] = result["avg_entry_price"] > 0.85

    # Haal posities op
    positions = fetch_positions(address, limit=100)
    result["open_positions"] = len([p for p in positions if float(p.get("curPrice") or 0) not in (0.0, 1.0)])

    # Bereken alleen gerealiseerde PnL van GESLOTEN posities (curPrice = 0 of 1)
    realized_pnl = 0
    unrealized_pnl = 0
    closed = []

    for p in positions:
        pnl = float(p.get("cashPnl") or 0)
        cur_price = float(p.get("curPrice") or 0)
        avg_price = float(p.get("avgPrice") or 0)

        if cur_price >= 0.99:      # gesloten — gewonnen
            realized_pnl += pnl
            closed.append(("won", pnl, avg_price))
        elif cur_price <= 0.01:    # gesloten — verloren
            realized_pnl += pnl
            closed.append(("lost", pnl, avg_price))
        else:                      # nog open — telt niet mee voor P&L beoordeling
            unrealized_pnl += pnl

    result["total_pnl"]      = realized_pnl
    result["unrealized_pnl"] = unrealized_pnl

    # Win rate op gesloten posities met niet-triviaal entry (niet hindsight)
    qualifying_closed = [(outcome, pnl, entry) for outcome, pnl, entry in closed if entry < 0.85]
    result["closed_trades"] = len(qualifying_closed)
    result["won_trades"]    = len([x for x in qualifying_closed if x[0] == "won"])
    result["lost_trades"]   = len([x for x in qualifying_closed if x[0] == "lost"])

    if qualifying_closed:
        result["win_rate"] = result["won_trades"] / len(qualifying_closed)

    return result


def passes_quality_filter(w):
    """Stap 3: Kwaliteitsfilters op gerealiseerde PnL van gesloten trades."""
    if w.get("error"):
        return False, "API fout"
    if w["trade_count"] < 10:
        return False, f"Te weinig trades: {w['trade_count']}"
    if w["total_volume"] < 5000:
        return False, f"Volume te laag: ${w['total_volume']:.0f}"
    if w["avg_trade_size"] < 30:
        return False, f"Gem. trade te klein: ${w['avg_trade_size']:.0f}"
    if w["closed_trades"] < 5:
        return False, f"Te weinig gesloten trades: {w['closed_trades']}"
    if w["total_pnl"] <= 0:
        return False, f"Geen positieve gerealiseerde PnL: ${w['total_pnl']:.0f}"
    if w["win_rate"] < 0.50 and w["closed_trades"] >= 10:
        return False, f"Win rate te laag: {w['win_rate']*100:.1f}%"
    if w["is_hindsight"]:
        return False, f"Hindsight trader (gem. entry: {w['avg_entry_price']*100:.0f}%)"
    return True, "OK"


def detect_strategy(w, positions_raw):
    """Detecteer handelspatroon op basis van data."""
    hints = []

    # Vroeg instapper?
    if w["avg_entry_price"] < 0.35:
        hints.append("vroeg instapper (laag entry < 35%)")
    elif w["avg_entry_price"] < 0.55:
        hints.append("medium-prijs entries (35-55%)")

    # Specialist?
    dominant = w.get("dominant_market", "overig")
    dist = w.get("market_distribution", {})
    total = sum(dist.values()) or 1
    dom_pct = dist.get(dominant, 0) / total * 100
    if dom_pct > 60:
        hints.append(f"specialist in {dominant} ({dom_pct:.0f}% van trades)")
    else:
        hints.append(f"generalist — {dominant} dominant ({dom_pct:.0f}%)")

    # Gemiddelde trade grootte
    if w["avg_trade_size"] > 5000:
        hints.append(f"grote posities (gem. ${w['avg_trade_size']:.0f})")
    elif w["avg_trade_size"] > 500:
        hints.append(f"middelgrote posities (gem. ${w['avg_trade_size']:.0f})")
    else:
        hints.append(f"kleine posities (gem. ${w['avg_trade_size']:.0f})")

    # High volume?
    if w["total_volume"] > 500000:
        hints.append("zeer hoog volume (>$500K) — professionele trader")
    elif w["total_volume"] > 100000:
        hints.append("hoog volume (>$100K)")

    return " | ".join(hints) if hints else "onbekend"


def generate_report(qualified, all_results):
    """Stap 4: Genereer markdown rapport."""
    os.makedirs("/Users/sem/polymarket-bot/data", exist_ok=True)

    # Sorteer op PnL
    qualified_sorted = sorted(qualified, key=lambda w: w["total_pnl"], reverse=True)

    lines = []
    lines.append("# Polymarket Whale Research Rapport")
    lines.append(f"\n_Gegenereerd op: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")
    lines.append(f"**Totaal geanalyseerde wallets:** {len(all_results)}")
    lines.append(f"**Wallets die filters passeren:** {len(qualified)}\n")
    lines.append("---\n")

    lines.append("## Gekwalificeerde Traders\n")

    for i, w in enumerate(qualified_sorted, 1):
        name = w["name"]
        addr = w["address"]
        pnl = w["total_pnl"]
        wr = w["win_rate"] * 100
        closed = w["closed_trades"]
        entry = w["avg_entry_price"] * 100
        vol = w["total_volume"]
        dom = w["dominant_market"]
        strategy = w.get("strategy", "n.v.t.")
        copyable = "JA" if (w["win_rate"] > 0.60 and w["closed_trades"] >= 20 and pnl > 10000) else "MISSCHIEN"

        lines.append(f"## {i}. {name}")
        lines.append(f"- **Wallet:** `{addr}`")
        lines.append(f"- **PnL:** +${pnl:,.0f}")
        lines.append(f"- **Win rate:** {wr:.1f}% (op {closed} gesloten trades, gem. entry {entry:.0f}%)")
        lines.append(f"- **Volume:** ${vol:,.0f}")
        lines.append(f"- **Specialiteit:** {dom}")
        dist_str = ", ".join([f"{k}: {v}" for k, v in w.get("market_distribution", {}).items()])
        lines.append(f"- **Markt distributie:** {dist_str}")
        lines.append(f"- **Strategie:** {strategy}")
        lines.append(f"- **Kopieerbaar:** {copyable}")
        reden = []
        if pnl > 50000:
            reden.append(f"Bewezen winstgevend (${pnl:,.0f} PnL)")
        if wr > 60:
            reden.append(f"Sterke win rate ({wr:.1f}%)")
        if closed >= 30:
            reden.append(f"Ruim voldoende data ({closed} trades)")
        if not w["is_hindsight"]:
            reden.append(f"Geen hindsight trading (entry {entry:.0f}%)")
        lines.append(f"- **Reden:** {'; '.join(reden) if reden else 'Marginaal gekwalificeerd'}")
        lines.append("")

    lines.append("---\n")
    lines.append("## Niet-gekwalificeerde Wallets (samenvatting)\n")
    not_qualified = [w for w in all_results if not w.get("qualifies")]
    lines.append(f"Totaal gefilterd: {len(not_qualified)} wallets.\n")

    # Toon top 10 van bijna-gekwalificeerden
    almost = sorted(
        [w for w in not_qualified if w.get("total_pnl", 0) > 1000 and not w.get("error")],
        key=lambda w: w.get("total_pnl", 0),
        reverse=True
    )[:10]

    if almost:
        lines.append("### Interessant maar niet gekwalificeerd:\n")
        for w in almost:
            reason = w.get("filter_reason", "onbekend")
            lines.append(f"- `{w['address'][:12]}...` — PnL: ${w.get('total_pnl',0):,.0f}, Vol: ${w.get('total_volume',0):,.0f}, Reden afwijzing: {reason}")

    lines.append("\n---\n")
    lines.append("## Top 3 Aanbeveling\n")
    lines.append("Voeg deze wallets toe aan `KNOWN_WHALES` in `whale_tracker.py`:\n")

    top3 = qualified_sorted[:3]
    for i, w in enumerate(top3, 1):
        lines.append(f"### #{i}: {w['name']}")
        lines.append(f"```")
        label = w["name"].replace(" ", "").replace("...", "")[:12]
        lines.append(f'"{label}": "{w["address"]}",')
        lines.append(f"```")
        lines.append(f"**Waarom:** PnL +${w['total_pnl']:,.0f} | Win rate {w['win_rate']*100:.1f}% | {w['dominant_market']} specialist")
        lines.append(f"**Strategie:** {w.get('strategy', 'n.v.t.')}\n")

    return "\n".join(lines)


def main():
    print("=" * 60)
    print("  POLYMARKET WHALE RESEARCH")
    print("=" * 60)

    # Stap 1: Wallets verzamelen
    all_wallets = collect_wallet_candidates()

    # Stap 2: Analyseer elk wallet
    print(f"\n=== STAP 2: WALLETS ANALYSEREN ({len(all_wallets)} stuks) ===")
    all_results = []
    qualified = []

    for i, (name, address) in enumerate(all_wallets.items(), 1):
        print(f"\n  [{i}/{len(all_wallets)}] Analyseren: {name} ({address[:10]}...)")
        w = analyze_wallet(name, address)

        # Stap 3: Kwaliteitsfilter
        ok, reason = passes_quality_filter(w)
        w["qualifies"] = ok
        w["filter_reason"] = reason

        if ok:
            print(f"    ✓ GEKWALIFICEERD — PnL: ${w['total_pnl']:,.0f}, WR: {w['win_rate']*100:.1f}%, Vol: ${w['total_volume']:,.0f}")
            qualified.append(w)
        else:
            pnl_str = f"${w['total_pnl']:,.0f}" if w.get("total_pnl") else "n/a"
            print(f"    ✗ Gefilterd: {reason} | PnL: {pnl_str}")

        all_results.append(w)

    print(f"\n=== RESULTATEN: {len(qualified)}/{len(all_results)} wallets gekwalificeerd ===")

    # Voeg strategy toe aan gekwalificeerden
    for w in qualified:
        w["strategy"] = detect_strategy(w, [])

    # Stap 4: Rapport genereren
    print("\n=== STAP 4: RAPPORT GENEREREN ===")
    report = generate_report(qualified, all_results)

    with open(OUTPUT_PATH, "w") as f:
        f.write(report)
    print(f"  Rapport opgeslagen: {OUTPUT_PATH}")

    # Samenvatting
    print("\n" + "=" * 60)
    print("  SAMENVATTING")
    print("=" * 60)
    print(f"  Wallets geanalyseerd:    {len(all_results)}")
    print(f"  Gekwalificeerd:          {len(qualified)}")
    print(f"  Rapport:                 {OUTPUT_PATH}")

    if qualified:
        sorted_q = sorted(qualified, key=lambda w: w["total_pnl"], reverse=True)
        print("\n  TOP TRADERS:")
        for w in sorted_q[:5]:
            print(f"  • {w['name'][:20]:20s} PnL: ${w['total_pnl']:>10,.0f}  WR: {w['win_rate']*100:4.1f}%  {w['dominant_market']}")
    else:
        print("\n  Geen wallets passeerden alle filters.")
        print("  Bekijk 'Interessant maar niet gekwalificeerd' in het rapport.")


if __name__ == "__main__":
    main()
