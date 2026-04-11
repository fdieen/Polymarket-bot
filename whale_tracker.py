"""
Whale Tracker — Volg slimme traders op Polymarket.

Haalt recente trades en open posities op van bekende winstgevende wallets.
Laat zien wat zij nu kopen/verkopen, zodat je kansen kunt kopiëren.

Bekende wallets worden bijgehouden in KNOWN_WHALES dict.
Voeg eigen adressen toe via fetch_whale_profile().

Run: venv/bin/python whale_tracker.py
"""
import json
import requests
from dataclasses import dataclass
from datetime import datetime, timezone

DATA_API = "https://data-api.polymarket.com"

# Bekende winstgevende wallets (weather/temperature specialisten)
KNOWN_WHALES = {
    "ColdMath": "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11",  # temperatuur specialist
}


def discover_weather_traders(min_temp_trades: int = 5) -> dict[str, str]:
    """
    Zoekt actieve temperature traders via ColdMath's sociale netwerk.
    Haalt profielen op van mensen die dezelfde markten handelen.
    Returns {username_or_addr: wallet_address}
    """
    discovered = {}

    # Stap 1: haal ColdMath's recente trades op
    coldmath = "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11"
    try:
        r = requests.get(
            f"{DATA_API}/activity",
            params={"user": coldmath, "limit": 50},
            timeout=10,
        )
        r.raise_for_status()
        trades = r.json()
    except Exception:
        return discovered

    # Stap 2: voor elke temperature markt, zoek andere traders
    temp_cids = list({
        t.get("conditionId", "")
        for t in trades
        if "temperature" in t.get("title", "").lower() and t.get("conditionId")
    })[:10]

    seen_wallets = set(KNOWN_WHALES.values())

    for cid in temp_cids:
        try:
            r2 = requests.get(
                f"{DATA_API}/activity",
                params={"market": cid, "limit": 30},
                timeout=8,
            )
            if r2.status_code != 200:
                continue
            for t in r2.json():
                wallet = t.get("proxyWallet", "")
                if wallet and wallet not in seen_wallets and len(wallet) == 42:
                    # Check of dit een actieve temp trader is
                    seen_wallets.add(wallet)
                    discovered[wallet[:8] + "..."] = wallet
        except Exception:
            continue

    return discovered


@dataclass
class WhalePosition:
    wallet_name:  str
    title:        str
    outcome:      str   # "Yes" of "No"
    size:         float
    avg_price:    float
    cur_price:    float
    current_value: float
    cash_pnl:     float
    direction:    str   # "BUY YES" of "BUY NO"
    condition_id: str
    slug:         str
    end_date:     str


@dataclass
class WhaleTrade:
    wallet_name:  str
    timestamp:    str
    side:         str   # "BUY" of "SELL"
    title:        str
    price:        float
    usdc_size:    float
    outcome:      str
    condition_id: str


def fetch_whale_positions(name: str, address: str) -> list[WhalePosition]:
    """Haalt open posities op van een whale."""
    try:
        r = requests.get(
            f"{DATA_API}/positions",
            params={"user": address, "limit": 100, "sizeThreshold": 0.1},
            timeout=10,
        )
        r.raise_for_status()
        positions = []
        for p in r.json():
            val = float(p.get("currentValue") or 0)
            if val < 0.5:  # filter kleine/afgeronde posities
                continue
            cur = float(p.get("curPrice") or 0)
            # Skip bijna-afgeronde markten
            if cur in (0.0, 1.0):
                continue
            outcome = p.get("outcome", "Yes")
            positions.append(WhalePosition(
                wallet_name=name,
                title=p.get("title", "?"),
                outcome=outcome,
                size=float(p.get("size") or 0),
                avg_price=float(p.get("avgPrice") or 0),
                cur_price=cur,
                current_value=val,
                cash_pnl=float(p.get("cashPnl") or 0),
                direction=f"BUY {outcome.upper()}",
                condition_id=p.get("conditionId", ""),
                slug=p.get("slug", ""),
                end_date=(p.get("endDate") or "")[:10],
            ))
        return sorted(positions, key=lambda p: p.current_value, reverse=True)
    except Exception as e:
        print(f"  Fout bij posities {name}: {e}")
        return []


def fetch_whale_activity(name: str, address: str, limit: int = 30) -> list[WhaleTrade]:
    """Haalt recente trades op van een whale."""
    try:
        r = requests.get(
            f"{DATA_API}/activity",
            params={"user": address, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        trades = []
        for t in r.json():
            size = float(t.get("usdcSize") or 0)
            if size < 1.0:  # filter micro-trades
                continue
            ts = t.get("timestamp", 0)
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            trades.append(WhaleTrade(
                wallet_name=name,
                timestamp=dt,
                side=t.get("side", "?"),
                title=t.get("title", "?"),
                price=float(t.get("price") or 0),
                usdc_size=size,
                outcome="Yes" if t.get("outcomeIndex", 0) == 0 else "No",
                condition_id=t.get("conditionId", ""),
            ))
        return trades
    except Exception as e:
        print(f"  Fout bij activiteit {name}: {e}")
        return []


def fetch_whale_stats(address: str) -> dict:
    """Haalt portfolio statistieken op (profit, trades, etc.)."""
    try:
        r = requests.get(
            f"{DATA_API}/profile",
            params={"address": address},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                data = data[0]
            return data
    except Exception:
        pass
    return {}


def fetch_all_whales() -> dict:
    """
    Haalt posities en activiteit op voor alle bekende whales.
    Returns dict: {name: {positions: [...], trades: [...], stats: {...}}}
    """
    result = {}
    for name, address in KNOWN_WHALES.items():
        print(f"  {name} ophalen...")
        positions = fetch_whale_positions(name, address)
        trades    = fetch_whale_activity(name, address, limit=20)
        result[name] = {
            "address":   address,
            "positions": positions,
            "trades":    trades,
        }
    return result


def add_whale(name: str, address: str):
    """Voegt een nieuwe whale toe aan de tracker."""
    KNOWN_WHALES[name] = address


def lookup_by_username(username: str) -> str | None:
    """
    Zoekt walletadres op via Polymarket profiel-URL.
    Parseert de HTML voor het proxyWallet adres.
    """
    import re
    try:
        r = requests.get(
            f"https://polymarket.com/profile/%40{username.lower()}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        matches = re.findall(r'"proxyWallet":"(0x[a-fA-F0-9]{40})"', r.text)
        return matches[0] if matches else None
    except Exception:
        return None


def display(whale_data: dict):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── Whale Tracker [{ts}] ───────────────────────────────────")

    for name, data in whale_data.items():
        address = data["address"]
        positions = data["positions"]
        trades = data["trades"]

        print(f"\n  ── {name} ({address[:10]}...{address[-4:]}) ─────────────")

        # Recente trades
        print(f"\n  RECENTE TRADES ({len(trades)}):")
        for t in trades[:10]:
            sign = "↑" if t.side == "BUY" else "↓"
            print(f"    {sign} {t.timestamp} | {t.side} {t.outcome:3} ${t.usdc_size:7.2f} @ {t.price*100:.0f}% | {t.title[:55]}")

        # Open posities
        print(f"\n  OPEN POSITIES ({len(positions)}):")
        for p in positions[:10]:
            pnl_sign = "+" if p.cash_pnl >= 0 else ""
            print(f"    {p.outcome:3} ${p.current_value:7.2f} @ {p.cur_price*100:.0f}% | pnl={pnl_sign}${p.cash_pnl:.2f} | {p.title[:55]}")


if __name__ == "__main__":
    print("── Polymarket Whale Tracker ─────────────────────────────")
    print("Whales ophalen...")
    data = fetch_all_whales()
    display(data)
