"""
Flow Scanner — detecteert gecoördineerde smart money bewegingen op Polymarket.

Signaal: 3+ onafhankelijke wallets kopen dezelfde kant op dezelfde markt
binnen een kort tijdvenster → mogelijk insider info of sterke conviction.

Run standalone:   venv/bin/python flow_scanner.py
Run als module:   from flow_scanner import FlowScanner; scanner.start()
"""

import time
import threading
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger("flow_scanner")

DATA_API = "https://data-api.polymarket.com"

# ── Configuratie ──────────────────────────────────────────────────────────────
POLL_INTERVAL   = 300        # seconden tussen scans (5 min)
TIME_WINDOW     = 1800       # tijdvenster in seconden (30 min)
MIN_WALLETS     = 6          # minimaal aantal verschillende wallets
MIN_WALLET_SIZE = 20         # minimale inzet per wallet in USDC
MIN_TOTAL_SIZE  = 500        # minimale totale flow in USDC
PRICE_MIN       = 0.08       # negeer markten die bijna resolved zijn
PRICE_MAX       = 0.92

# Afwijkende inzet detectie op kleine sports events (insider signaal)
OUTLIER_MIN_SIZE      = 500   # minimale inzet op één wallet om als outlier te tellen
OUTLIER_SMALL_EVENTS  = {     # keywords voor kleine/obscure events
    "challenger", "itf ", "futures", "qualifying",
    "segunda", "league two", "third division", "3. liga",
    "série c", "national league", "ekstraklasa",
}

# Categorieen die we actief volgen voor signalen
ACTIVE_CATEGORIES = {"weather", "sports_outlier"}
FETCH_LIMIT     = 500        # trades ophalen per poll

# Bekende bots / market makers om te negeren
KNOWN_BOTS = {
    "0x0000000000000000000000000000000000000000",
}


PROACTIVE_MAX_MOVE = 0.08   # max prijsbeweging vóór flow om als proactief te tellen (8%)


@dataclass
class FlowSignal:
    condition_id:  str
    title:         str
    slug:          str
    outcome:       str          # "Yes" of "No"
    price:         float        # huidige prijs
    wallets:       list[str]    # betrokken wallets
    total_size:    float        # totale USDC flow
    sizes:         list[float]  # per-wallet inzet
    first_trade:   str          # timestamp eerste trade
    last_trade:    str          # timestamp laatste trade
    category:      str          # sports / politics / crypto / weather / other
    signal_type:   str = "ONBEKEND"   # PROACTIEF / REACTIEF / ONBEKEND
    price_before:  float = 0.0        # prijs 30 min vóór de flow

    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}" if self.slug else ""

    def summary(self) -> str:
        n    = len(self.wallets)
        avg  = self.total_size / n
        icon = "🧠" if self.signal_type == "PROACTIEF" else "⚡" if self.signal_type == "REACTIEF" else "🚨"
        move = f" (prijs was {self.price_before*100:.0f}% → {self.price*100:.0f}%)" if self.price_before else ""
        return (
            f"{icon} <b>SMART MONEY [{self.signal_type}] [{self.category.upper()}]</b>\n"
            f"\n"
            f"📋 {self.title}\n"
            f"• Richting:   <b>{self.outcome.upper()}</b> @ {self.price*100:.0f}%{move}\n"
            f"• Wallets:    <b>{n} onafhankelijk</b>\n"
            f"• Totaal:     <b>${self.total_size:,.0f}</b> USDC\n"
            f"• Gem/wallet: ${avg:,.0f}\n"
            f"• Inzetten:   {', '.join(f'${s:.0f}' for s in sorted(self.sizes, reverse=True)[:6])}\n"
            f"• Tijdspan:   {self.first_trade} → {self.last_trade} UTC\n"
            + (f"• <a href=\"{self.url()}\">Bekijk op Polymarket</a>\n" if self.slug else "")
        )


def _categorize(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ["temperature", "weather", "rain", "snow", "celsius", "fahrenheit"]):
        return "weather"
    if any(x in t for x in ["bitcoin", "btc", "eth", "crypto", "sol", "price"]):
        return "crypto"
    if any(x in t for x in ["nba", "nfl", "mlb", "soccer", " vs ", "match", "finals", "playoff", "tournament", "masters", "win the"]):
        return "sports"
    if any(x in t for x in ["election", "president", "trump", "harris", "democrat", "republican", "senate", "minister"]):
        return "politics"
    return "other"


def _is_small_sports_event(title: str) -> bool:
    """Detecteert kleine/obscure sportevenementen — grote inzet hierop = insider signaal."""
    t = title.lower()
    return any(x in t for x in OUTLIER_SMALL_EVENTS)


def _has_outlier_bet(wallet_map: dict) -> bool:
    """True als één wallet een abnormaal grote inzet heeft t.o.v. de rest."""
    sizes = sorted(wallet_map.values(), reverse=True)
    if not sizes:
        return False
    # Eén wallet die minstens OUTLIER_MIN_SIZE inzet én 3x de gemiddelde inzet
    avg = sum(sizes) / len(sizes)
    return sizes[0] >= OUTLIER_MIN_SIZE and sizes[0] >= avg * 3


def fetch_recent_trades(limit: int = FETCH_LIMIT) -> list[dict]:
    """Haalt de meest recente globale trades op."""
    try:
        r = requests.get(
            f"{DATA_API}/trades",
            params={"limit": limit},
            timeout=12,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f"Trades ophalen mislukt: {e}")
    return []


def detect_signals(
    trades:       list[dict],
    seen_signals: set,
    cutoff_ts:    float,
) -> list[FlowSignal]:
    """
    Analyseert een lijst trades en detecteert gecoördineerde flow.
    Labelt elk signaal als PROACTIEF (prijs stabiel vóór flow)
    of REACTIEF (prijs al bewogen vóór flow).
    """
    # Bouw een prijshistorie per conditionId+outcomeIndex op basis van ALLE trades
    # (ook die buiten het tijdvenster vallen)
    price_history: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for t in trades:
        ts    = float(t.get("timestamp") or 0)
        price = float(t.get("price") or 0)
        key   = f"{t.get('conditionId')}_{t.get('outcomeIndex', 0)}"
        price_history[key].append((ts, price))

    # Groepeer binnen tijdvenster
    groups: dict[str, list[dict]] = defaultdict(list)

    for t in trades:
        ts = float(t.get("timestamp") or 0)
        if ts < cutoff_ts:
            continue

        wallet = t.get("proxyWallet", "").lower()
        if wallet in KNOWN_BOTS:
            continue

        if t.get("side") != "BUY":
            continue

        price = float(t.get("price") or 0)
        size  = float(t.get("size") or 0) * price  # USDC

        if size < MIN_WALLET_SIZE:
            continue
        if not (PRICE_MIN <= price <= PRICE_MAX):
            continue

        key = f"{t.get('conditionId')}_{t.get('outcomeIndex', 0)}"
        groups[key].append(t)

    signals = []
    for key, group in groups.items():
        # Unieke wallets
        wallet_map: dict[str, float] = {}
        for t in group:
            w    = t.get("proxyWallet", "").lower()
            p    = float(t.get("price") or 0)
            usdc = float(t.get("size") or 0) * p
            wallet_map[w] = wallet_map.get(w, 0) + usdc

        if len(wallet_map) < MIN_WALLETS:
            continue

        total_size = sum(wallet_map.values())
        if total_size < MIN_TOTAL_SIZE:
            continue

        ref     = group[0]
        cid     = ref.get("conditionId", "")
        title   = ref.get("title", "?")
        slug    = ref.get("slug", "") or ref.get("eventSlug", "")
        price   = float(ref.get("price") or 0)
        outcome = "Yes" if int(ref.get("outcomeIndex", 0)) == 0 else "No"

        signal_key = f"{cid}_{outcome}"
        if signal_key in seen_signals:
            continue

        timestamps = sorted(float(t.get("timestamp", 0)) for t in group)
        fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
        first_ts = timestamps[0]

        # ── Proactief vs reactief ─────────────────────────────────────────
        # Kijk naar prijzen 30-60 min VOOR de eerste flow trade
        pre_window_start = first_ts - 3600
        pre_window_end   = first_ts - 60   # even buffer voor de flow zelf
        pre_trades = [
            (ts, p) for ts, p in price_history[key]
            if pre_window_start <= ts <= pre_window_end
        ]

        signal_type  = "ONBEKEND"
        price_before = 0.0

        if pre_trades:
            # Oudste prijs in het pre-venster
            pre_trades.sort(key=lambda x: x[0])
            price_before = pre_trades[0][1]
            price_latest_pre = pre_trades[-1][1]
            price_move = abs(price_latest_pre - price_before)

            if price_move <= PROACTIVE_MAX_MOVE:
                signal_type = "PROACTIEF"   # prijs was stabiel → echt signaal
            else:
                signal_type = "REACTIEF"    # prijs bewoog al → traders reageren

        category = _categorize(title)

        # Sports outlier check: kleine event + grote afwijkende inzet = insider signaal
        if category == "sports" and _is_small_sports_event(title) and _has_outlier_bet(wallet_map):
            category = "sports_outlier"

        # Filter: alleen weather en sports_outlier zijn relevant — rest negeren
        if category not in ACTIVE_CATEGORIES:
            continue

        signals.append(FlowSignal(
            condition_id=cid,
            title=title,
            slug=slug,
            outcome=outcome,
            price=price,
            wallets=list(wallet_map.keys()),
            total_size=round(total_size, 2),
            sizes=list(wallet_map.values()),
            first_trade=fmt(timestamps[0]),
            last_trade=fmt(timestamps[-1]),
            category=category,
            signal_type=signal_type,
            price_before=round(price_before, 3),
        ))

    return signals


class FlowScanner:
    def __init__(self):
        self._stop        = threading.Event()
        self._seen        = set()    # al gemelde signalen (reset elke dag)
        self._last_reset  = datetime.now(timezone.utc).date()
        self._thread      = None
        self.signals_found = []      # voor dashboard gebruik

    def _reset_seen_daily(self):
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset:
            self._seen.clear()
            self._last_reset = today
            log.info("Flow scanner: dagelijkse reset seen-set")

    def _run(self):
        log.info("Flow scanner gestart")
        while not self._stop.is_set():
            try:
                self._reset_seen_daily()
                cutoff = time.time() - TIME_WINDOW

                trades  = fetch_recent_trades()
                signals = detect_signals(trades, self._seen, cutoff)

                for sig in signals:
                    self._seen.add(f"{sig.condition_id}_{sig.outcome}")
                    self.signals_found.append(sig)
                    log.info(f"FLOW SIGNAAL: {sig.title[:60]} | {sig.outcome} | {len(sig.wallets)} wallets | ${sig.total_size:.0f}")

                    # Telegram alert
                    try:
                        from alerts import send_telegram
                        send_telegram(sig.summary())
                    except Exception as e:
                        log.warning(f"Telegram alert mislukt: {e}")

            except Exception as e:
                log.warning(f"Flow scanner fout: {e}")

            self._stop.wait(POLL_INTERVAL)

        log.info("Flow scanner gestopt")

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="flow-scanner")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def recent_signals(self, n: int = 20) -> list[dict]:
        """Laatste N signalen als dict — voor dashboard."""
        return [
            {
                "title":       s.title,
                "outcome":     s.outcome,
                "price":       s.price,
                "wallets":     len(s.wallets),
                "total_size":  s.total_size,
                "category":    s.category,
                "signal_type": s.signal_type,
                "price_before": s.price_before,
                "first":       s.first_trade,
                "last":        s.last_trade,
                "url":         s.url(),
            }
            for s in reversed(self.signals_found[-n:])
        ]


# ── Standalone run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Polymarket Flow Scanner")
    parser.add_argument("--once",    action="store_true", help="Eenmalige scan, dan stoppen")
    parser.add_argument("--window",  type=int, default=TIME_WINDOW,   help="Tijdvenster in seconden")
    parser.add_argument("--wallets", type=int, default=MIN_WALLETS,   help="Min aantal wallets")
    parser.add_argument("--size",    type=int, default=MIN_TOTAL_SIZE, help="Min totale flow USDC")
    args = parser.parse_args()

    print(f"── Polymarket Flow Scanner ──────────────────────────────")
    print(f"  Tijdvenster:  {args.window // 60} minuten")
    print(f"  Min wallets:  {args.wallets}")
    print(f"  Min flow:     ${args.size}")
    print()

    cutoff  = time.time() - args.window
    trades  = fetch_recent_trades(limit=500)
    print(f"  {len(trades)} trades opgehaald")

    signals = detect_signals(trades, set(), cutoff)

    if not signals:
        print("  Geen signalen gevonden.")
    else:
        print(f"  {len(signals)} signaal(en) gevonden:\n")
        for s in sorted(signals, key=lambda x: x.total_size, reverse=True):
            print(s.summary())
            print()

    if not args.once:
        print(f"\nContinue polling elke {POLL_INTERVAL // 60} minuten...")
        scanner = FlowScanner()
        scanner.start()
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            scanner.stop()
            print("\nGestopt.")
