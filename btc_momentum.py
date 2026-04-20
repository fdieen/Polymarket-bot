"""
BTC Momentum Trader — 4-Minute Rule

Strategie (gebaseerd op 12.000 trades analyse):
  - Volg BTC/USDT 1-minuut candles via Binance WebSocket
  - Als 4 opeenvolgende candles allemaal in dezelfde richting sluiten
  - → Zoek actieve Polymarket BTC markt die binnen 90 sec sluit
  - → Koop continuation (UP of DOWN)

Win rate historisch: 78% overall, 96.8% bij perfecte 4-van-4 setup
Breakeven op Polymarket (incl. fees): 51%

Run standalone:   venv/bin/python btc_momentum.py
Als module:       from btc_momentum import start, stop, get_state
"""

import json
import os
import time
import threading
import logging
import requests
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

import websocket

log = logging.getLogger("btc_momentum")

# ── Configuratie ──────────────────────────────────────────────────────────────
BINANCE_WS     = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"
GAMMA_API      = "https://gamma-api.polymarket.com"
CLOB_API       = "https://clob.polymarket.com"

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "data", "btc_momentum_portfolio.json")
STARTING_CASH  = 100.0   # apart budget voor momentum trades
TRADE_AMOUNT   = 10.0    # vast bedrag per trade
MIN_ENTRY_SEC  = 15      # minimaal zoveel seconden voor sluiting om te kunnen instappen
MAX_ENTRY_SEC  = 90      # maximaal zoveel seconden voor sluiting (anders te vroeg)
DRY_RUN        = True    # altijd dry run tenzij expliciet uitgezet

# Reversal configuratie
REVERSAL_MIN_PRICE    = 0.10   # minimaal 10¢ entry voor verliezende kant
REVERSAL_MAX_PRICE    = 0.40   # maximaal 40¢ entry
REVERSAL_AMOUNT       = 8.0    # iets kleiner bedrag dan momentum (hoger risico)
MIN_MOVE_FOR_REVERSAL = 50.0   # minimale BTC move in $ voor reversal
REVERSAL_SIGNAL_TTL   = 300    # reversal mag max 5 min na momentum signal


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Candle:
    open_time: int    # unix ms
    open: float
    close: float
    closed: bool


@dataclass
class MomentumSignal:
    direction: str    # "UP" of "DOWN"
    candles: int      # aantal opeenvolgende candles (4)
    move_usd: float   # totale prijs beweging in $
    ts: str


@dataclass
class MomentumTrade:
    ts: str
    direction: str
    amount: float
    entry_price: float
    question: str
    condition_id: str
    dry_run: bool
    success: bool
    note: str = ""
    pnl: float = 0.0
    status: str = "open"
    strategy: str = "momentum"   # "momentum" of "reversal"


@dataclass
class ReversalSignal:
    direction: str       # te kopen richting (TEGENGESTELD aan momentum)
    orig_direction: str  # originele momentum richting
    move_usd: float      # grootte van de originele BTC move
    ts: str
    ts_unix: float       # voor TTL check


# ── State ─────────────────────────────────────────────────────────────────────

class BtcMomentumState:
    def __init__(self):
        self.running        = False
        self.dry_run        = DRY_RUN
        self.candles: list[Candle] = []
        self.last_signal: Optional[MomentumSignal] = None
        self.last_btc_price: float = 0.0
        self.trades: list[MomentumTrade] = []
        self.log_entries: list[str] = []
        self.traded_this_window: str = ""      # "HH:MM" van het 5-min window
        self.last_reversal_signal: Optional[ReversalSignal] = None
        self.reversal_traded_this_window: str = ""
        self._lock = threading.Lock()
        self._ws: Optional[websocket.WebSocketApp] = None

    def add_log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        with self._lock:
            self.log_entries.insert(0, entry)
            if len(self.log_entries) > 200:
                self.log_entries.pop()
        log.info(msg)

    def to_dict(self) -> dict:
        wins   = [t for t in self.trades if t.status == "won"]
        losses = [t for t in self.trades if t.status in ("lost", "sold")]
        closed = wins + losses
        wr     = round(len(wins) / len(closed) * 100, 1) if closed else 0.0
        pnl    = round(sum(t.pnl for t in closed), 2)

        rev_trades = [t for t in self.trades if t.strategy == "reversal"]
        rev_wins   = [t for t in rev_trades if t.status == "won"]
        rev_closed = [t for t in rev_trades if t.status in ("won", "lost", "sold")]
        rev_wr     = round(len(rev_wins) / len(rev_closed) * 100, 1) if rev_closed else 0.0

        return {
            "running":      self.running,
            "dry_run":      self.dry_run,
            "btc_price":    self.last_btc_price,
            "last_signal":  self.last_signal.__dict__ if self.last_signal else None,
            "last_reversal": self.last_reversal_signal.__dict__ if self.last_reversal_signal else None,
            "trades_today": len([t for t in self.trades
                                 if t.ts[:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d")]),
            "total_trades": len(self.trades),
            "win_rate":     wr,
            "total_pnl":    pnl,
            "reversal_trades": len(rev_trades),
            "reversal_win_rate": rev_wr,
            "log":          self.log_entries[:20],
        }


_state = BtcMomentumState()


def get_state() -> BtcMomentumState:
    return _state


# ── Momentum detectie ─────────────────────────────────────────────────────────

def _current_5min_window() -> str:
    """Geeft HH:MM string van het huidige 5-minuten window."""
    now = datetime.now(timezone.utc)
    window_min = (now.minute // 5) * 5
    return now.strftime(f"%H:{window_min:02d}")


def check_momentum(candles: list[Candle]) -> Optional[MomentumSignal]:
    """Controleer of de laatste 4 gesloten candles momentum tonen."""
    closed = [c for c in candles if c.closed]
    if len(closed) < 4:
        return None

    last4 = closed[-4:]
    ups   = sum(1 for c in last4 if c.close > c.open)
    downs = sum(1 for c in last4 if c.close < c.open)

    if ups == 4:
        move = round(last4[-1].close - last4[0].open, 2)
        return MomentumSignal("UP", 4, move, datetime.now(timezone.utc).strftime("%H:%M:%S"))
    if downs == 4:
        move = round(last4[0].open - last4[-1].close, 2)
        return MomentumSignal("DOWN", 4, move, datetime.now(timezone.utc).strftime("%H:%M:%S"))

    return None


# ── Reversal detectie ────────────────────────────────────────────────────────

def check_reversal(
    candles: list[Candle],
    last_signal: Optional[MomentumSignal],
) -> Optional[ReversalSignal]:
    """
    Detecteer reversal kans: na een sterke 4-candle streak,
    signaleert de eerste tegengestelde candle ('dead price') een reversal.

    Voorwaarden:
    - Er was een recente momentum signal (< REVERSAL_SIGNAL_TTL seconden geleden)
    - De move was groot genoeg (>= MIN_MOVE_FOR_REVERSAL)
    - De laatste gesloten candle gaat TEGEN de richting van het momentum
    """
    if not last_signal:
        return None

    # TTL check: signal mag niet te oud zijn
    try:
        signal_ts = datetime.strptime(last_signal.ts, "%H:%M:%S").replace(
            year=datetime.now().year,
            month=datetime.now().month,
            day=datetime.now().day,
            tzinfo=timezone.utc,
        )
        age = (datetime.now(timezone.utc) - signal_ts).total_seconds()
        if age > REVERSAL_SIGNAL_TTL:
            return None
    except Exception:
        return None

    # Move groot genoeg?
    if abs(last_signal.move_usd) < MIN_MOVE_FOR_REVERSAL:
        return None

    closed = [c for c in candles if c.closed]
    if len(closed) < 5:
        return None

    last = closed[-1]
    is_down_candle = last.close < last.open
    is_up_candle   = last.close > last.open

    if last_signal.direction == "UP" and is_down_candle:
        return ReversalSignal(
            direction    = "DOWN",
            orig_direction = "UP",
            move_usd     = last_signal.move_usd,
            ts           = datetime.now(timezone.utc).strftime("%H:%M:%S"),
            ts_unix      = time.time(),
        )
    if last_signal.direction == "DOWN" and is_up_candle:
        return ReversalSignal(
            direction    = "UP",
            orig_direction = "DOWN",
            move_usd     = last_signal.move_usd,
            ts           = datetime.now(timezone.utc).strftime("%H:%M:%S"),
            ts_unix      = time.time(),
        )

    return None


# ── Polymarket markt zoeken ───────────────────────────────────────────────────

def find_btc_market(direction: str) -> Optional[dict]:
    """
    Zoek actieve Polymarket BTC 5-min markt via series btc-up-or-down-5m.
    Berekent het huidige 5-min window timestamp en fetcht die specifieke markt.
    """
    import time as _time
    try:
        now = datetime.now(timezone.utc)

        # Probeer huidige én volgende window (voor als we net op de grens zitten)
        for offset in (0, 300, -300):
            ts = (int(_time.time()) // 300) * 300 + offset
            slug = f"btc-updown-5m-{ts}"

            r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=8)
            if not r.ok or not r.json():
                continue

            event = r.json()[0]
            end = event.get("endDate") or ""
            if not end:
                continue

            end_dt    = datetime.fromisoformat(end.replace("Z", "+00:00"))
            secs_left = (end_dt - now).total_seconds()

            if not (MIN_ENTRY_SEC < secs_left < MAX_ENTRY_SEC):
                continue

            # Haal markt op uit event
            markets = event.get("markets", [])
            if not markets:
                continue
            m = markets[0]

            # Voeg direction token toe
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            outcomes  = json.loads(m.get("outcomes", '["Up","Down"]'))

            idx = 0 if direction == "UP" else 1
            m["_direction_token"] = token_ids[idx] if idx < len(token_ids) else ""
            m["_direction_outcome"] = outcomes[idx] if idx < len(outcomes) else direction
            m["_secs_left"] = round(secs_left)
            return m

        return None

    except Exception as e:
        _state.add_log(f"Market search fout: {e}")
        return None


def _get_market_price(market: dict, direction: str) -> float:
    """Haal huidige prijs op via outcomePrices in market data."""
    try:
        prices   = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
        outcomes = json.loads(market.get("outcomes", '["Up","Down"]'))
        idx = 0 if direction == "UP" else 1
        p = float(prices[idx]) if idx < len(prices) else 0.5
        return p if 0 < p < 1 else 0.5
    except Exception:
        return 0.5


# ── Trade uitvoeren ───────────────────────────────────────────────────────────

def _execute_trade(market: dict, direction: str, dry_run: bool) -> tuple[bool, str, float]:
    """
    Voert trade uit. Returns (success, note, entry_price).
    Hergebruikt CLOB client uit auto_trader.
    """
    cond_id  = market.get("conditionId", "")
    token_id = market.get("_direction_token", "")
    outcome  = market.get("_direction_outcome", direction)
    price    = _get_market_price(market, direction)

    if dry_run:
        return True, f"[DRY] BUY {outcome} @ {price*100:.0f}% ({market.get('_secs_left',0)}s left)", price

    if not token_id:
        return False, "Geen token_id beschikbaar", price

    try:
        from auto_trader import get_clob_client
        from py_clob_client_v2.clob_types import MarketOrderArgs

        client = get_clob_client()
        order  = client.create_market_order(MarketOrderArgs(token_id=token_id, amount=TRADE_AMOUNT))
        resp   = client.post_order(order, orderType="FOK")
        return True, f"Order OK: {resp}", price

    except Exception as e:
        return False, f"Order fout: {e}", price


def _record_trade(trade: MomentumTrade):
    """Sla trade op in portfolio JSON."""
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)

    try:
        with open(PORTFOLIO_FILE) as f:
            pf = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pf = {"starting_cash": STARTING_CASH, "cash": STARTING_CASH, "trades": []}

    pf["cash"] = round(pf["cash"] - trade.amount, 2)
    pf["trades"].append({
        "ts":          trade.ts,
        "direction":   trade.direction,
        "amount":      trade.amount,
        "entry_price": trade.entry_price,
        "question":    trade.question,
        "condition_id": trade.condition_id,
        "dry_run":     trade.dry_run,
        "success":     trade.success,
        "note":        trade.note,
        "pnl":         0.0,
        "status":      "open",
    })

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(pf, f, indent=2)


# ── Hoofd trade logica ────────────────────────────────────────────────────────

def _check_and_trade():
    """Controleer momentum en trade als conditie vervuld is."""
    signal = check_momentum(_state.candles)

    if not signal:
        return

    _state.last_signal = signal

    # Niet twee keer in hetzelfde 5-min window traden
    window = _current_5min_window()
    if window == _state.traded_this_window:
        return

    _state.add_log(
        f"Signal {signal.direction} (4x) — "
        f"move=${signal.move_usd:+.0f} — zoek markt..."
    )

    market = find_btc_market(signal.direction)
    if not market:
        _state.add_log(f"Geen markt gevonden ({signal.direction}, window={window})")
        return

    title   = market.get("question", "?")
    cond_id = market.get("conditionId", "")

    success, note, price = _execute_trade(market, signal.direction, _state.dry_run)

    trade = MomentumTrade(
        ts           = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        direction    = signal.direction,
        amount       = TRADE_AMOUNT if success else 0.0,
        entry_price  = price,
        question     = title,
        condition_id = cond_id,
        dry_run      = _state.dry_run,
        success      = success,
        note         = note,
    )

    with _state._lock:
        _state.trades.append(trade)
        if success:
            _state.traded_this_window = window

    icon = "✓" if success else "✗"
    _state.add_log(
        f"{icon} {signal.direction} ${TRADE_AMOUNT:.0f} @ {price*100:.0f}% | {title[:50]}"
    )

    if success:
        _record_trade(trade)

        # Telegram notificatie
        try:
            from telegram_bot import send
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").split(",")[0].strip()
            dry_tag = "[DRY] " if _state.dry_run else ""
            send(chat_id,
                f"⚡ {dry_tag}<b>BTC Momentum</b> — {signal.direction}\n"
                f"• Move: ${signal.move_usd:+.0f} (4 candles)\n"
                f"• Entry: {price*100:.0f}%\n"
                f"• {title[:60]}"
            )
        except Exception:
            pass


# ── Reversal trade logica ─────────────────────────────────────────────────────

def _check_and_trade_reversal():
    """
    Controleer reversal kans en trade als:
    1. Er een recente momentum signal was
    2. De huidige candle de richting breekt
    3. De verliezende kant 10-40¢ noteert op Polymarket
    """
    signal = check_reversal(_state.candles, _state.last_signal)

    if not signal:
        return

    _state.last_reversal_signal = signal

    # Niet twee keer in hetzelfde 5-min window
    window = _current_5min_window()
    if window == _state.reversal_traded_this_window:
        return

    _state.add_log(
        f"Reversal {signal.direction} — na {signal.orig_direction} move "
        f"${signal.move_usd:+.0f} — zoek markt 10-40¢..."
    )

    # Zoek markt: verliezende kant moet 10-40¢ zijn
    market = find_btc_market(signal.direction)
    if not market:
        _state.add_log(f"Reversal: geen markt gevonden ({signal.direction})")
        return

    price = _get_market_price(market, signal.direction)

    # Harde prijsfilter: 10-40¢
    if not (REVERSAL_MIN_PRICE <= price <= REVERSAL_MAX_PRICE):
        _state.add_log(
            f"Reversal SKIP: {signal.direction} prijs {price*100:.0f}¢ "
            f"buiten 10-40¢ range"
        )
        return

    title   = market.get("question", "?")
    cond_id = market.get("conditionId", "")

    success, note, entry_price = _execute_trade(market, signal.direction, _state.dry_run)

    trade = MomentumTrade(
        ts           = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        direction    = signal.direction,
        amount       = REVERSAL_AMOUNT if success else 0.0,
        entry_price  = entry_price,
        question     = title,
        condition_id = cond_id,
        dry_run      = _state.dry_run,
        success      = success,
        note         = f"[REVERSAL na {signal.orig_direction}] {note}",
        strategy     = "reversal",
    )

    with _state._lock:
        _state.trades.append(trade)
        if success:
            _state.reversal_traded_this_window = window

    icon = "✓" if success else "✗"
    _state.add_log(
        f"{icon} [REVERSAL] {signal.direction} ${REVERSAL_AMOUNT:.0f} "
        f"@ {entry_price*100:.0f}¢ | {title[:45]}"
    )

    if success:
        _record_trade(trade)

        try:
            from telegram_bot import send
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").split(",")[0].strip()
            dry_tag = "[DRY] " if _state.dry_run else ""
            send(chat_id,
                f"↩️ {dry_tag}<b>BTC Reversal</b> — {signal.direction}\n"
                f"• Na: {signal.orig_direction} move ${signal.move_usd:+.0f}\n"
                f"• Entry: {entry_price*100:.0f}¢ (verliezende kant)\n"
                f"• {title[:60]}"
            )
        except Exception:
            pass


# ── Binance WebSocket ─────────────────────────────────────────────────────────

def _on_message(ws, message):
    try:
        k = json.loads(message).get("k", {})
        candle = Candle(
            open_time = k["t"],
            open      = float(k["o"]),
            close     = float(k["c"]),
            closed    = bool(k["x"]),
        )
        with _state._lock:
            _state.last_btc_price = candle.close
            if candle.closed:
                _state.candles.append(candle)
                if len(_state.candles) > 20:
                    _state.candles.pop(0)

        if candle.closed:
            _check_and_trade()
            _check_and_trade_reversal()

    except Exception as e:
        log.warning(f"WS parse fout: {e}")


def _on_error(ws, error):
    _state.add_log(f"WS fout: {error}")


def _on_close(ws, code, msg):
    _state.add_log(f"WS gesloten ({code})")
    if _state.running:
        _state.add_log("Herverbinden in 5s...")
        time.sleep(5)
        _start_ws()


def _on_open(ws):
    _state.add_log("Binance WebSocket verbonden — BTC/USDT 1m")


def _start_ws():
    ws = websocket.WebSocketApp(
        BINANCE_WS,
        on_message = _on_message,
        on_error   = _on_error,
        on_close   = _on_close,
        on_open    = _on_open,
    )
    _state._ws = ws
    ws.run_forever(ping_interval=30, ping_timeout=10)


# ── Start / Stop ──────────────────────────────────────────────────────────────

def start(dry_run: bool = True):
    """Start de BTC momentum trader in een achtergrond thread."""
    if _state.running:
        return
    _state.running  = True
    _state.dry_run  = dry_run
    mode = "DRY RUN" if dry_run else "LIVE"
    _state.add_log(f"BTC Momentum Trader gestart ({mode})")
    threading.Thread(target=_start_ws, daemon=True, name="btc-momentum").start()


def stop():
    """Stop de trader."""
    _state.running = False
    if _state._ws:
        _state._ws.close()
    _state.add_log("BTC Momentum Trader gestopt")


# ── Dashboard API helpers ─────────────────────────────────────────────────────

def api_status() -> dict:
    return _state.to_dict()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("BTC Momentum Trader — DRY RUN test")
    print(f"Trade bedrag: ${TRADE_AMOUNT} | Entry window: {MIN_ENTRY_SEC}-{MAX_ENTRY_SEC}s voor sluiting")
    print("Ctrl+C om te stoppen\n")

    start(dry_run=True)

    try:
        while True:
            time.sleep(10)
            s = _state
            candles_info = f"{len([c for c in s.candles if c.closed])} candles"
            price_info   = f"BTC ${s.last_btc_price:,.0f}" if s.last_btc_price else "wachten..."
            signal_info  = f"Signal: {s.last_signal.direction}" if s.last_signal else "geen signal"
            print(f"  {price_info} | {candles_info} | {signal_info} | trades: {len(s.trades)}")
    except KeyboardInterrupt:
        stop()
        print("\nGestopt.")
