"""
Polymarket Monitor — toont live markten, prijzen en alerts.

Gebruik:
  python monitor.py              # top 20 markten op volume
  python monitor.py --sport      # alleen sport
  python monitor.py --crypto     # alleen crypto/tech
  python monitor.py --search nba # zoek op keyword
"""
import sys
import time
import json
import requests
from datetime import datetime

GAMMA_API = "https://gamma-api.polymarket.com/markets"

CATEGORIES = {
    "--sport":  ["nba", "nfl", "nhl", "soccer", "football", "basketball",
                 "tennis", "formula", "mls", "ufc", "boxing"],
    "--crypto": ["bitcoin", "btc", "eth", "crypto", "token", "opensea",
                 "coinbase", "sec", "etf"],
}


def get_markets(tag_filter: list[str] | None = None,
                search: str | None = None,
                limit: int = 25) -> list[dict]:
    r = requests.get(
        GAMMA_API,
        params={"limit": 100, "order": "volume24hr", "ascending": "false", "active": "true"},
        timeout=10,
    )
    markets = r.json()

    if tag_filter:
        markets = [
            m for m in markets
            if any(t in m.get("question", "").lower() for t in tag_filter)
        ]

    if search:
        markets = [
            m for m in markets
            if search.lower() in m.get("question", "").lower()
        ]

    return markets[:limit]


def parse_prices(market: dict) -> tuple[float, float] | None:
    outcomes = market.get("outcomes", "[]")
    prices   = market.get("outcomePrices", "[]")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
            prices   = json.loads(prices)
        except Exception:
            return None
    if len(prices) >= 2:
        try:
            return float(prices[0]), float(prices[1])
        except Exception:
            pass
    return None


def format_bar(price: float, width: int = 20) -> str:
    filled = int(price * width)
    return "█" * filled + "░" * (width - filled)


def display(markets: list[dict]):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── Polymarket Monitor [{ts}] ──────────────────────────────────")
    print(f"{'Vol 24h':>10}  {'YES':>5}  {'NO':>5}  Markt")
    print("─" * 72)

    for m in markets:
        vol  = float(m.get("volume24hr") or 0)
        q    = m.get("question", "?")
        p    = parse_prices(m)

        vol_str = f"${vol/1000:.0f}k" if vol >= 1000 else f"${vol:.0f}"

        if p:
            yes, no = p
            bar = format_bar(yes)
            print(f"{vol_str:>10}  {yes:>5.2f}  {no:>5.2f}  {q[:50]}")
        else:
            print(f"{vol_str:>10}  {'?':>5}  {'?':>5}  {q[:50]}")

    print()
    print("Ctrl+C om te stoppen | Herlaadt elke 30 seconden")


def main():
    args = sys.argv[1:]
    tag_filter = None
    search     = None

    for cat, tags in CATEGORIES.items():
        if cat in args:
            tag_filter = tags
            break

    if "--search" in args:
        idx = args.index("--search")
        if idx + 1 < len(args):
            search = args[idx + 1]

    print("Polymarket Monitor — laden...")

    try:
        while True:
            markets = get_markets(tag_filter=tag_filter, search=search)
            display(markets)
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nMonitor gestopt.")


if __name__ == "__main__":
    main()
