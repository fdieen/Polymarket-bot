"""
Paper Trading Portfolio — virtueel account voor P&L tracking.

Start met 100 USDC, registreert alle gesimuleerde trades,
berekent realized + unrealized P&L en houdt posities bij.

Opgeslagen in portfolio.json zodat data bewaard blijft na herstart.
"""
import json
import os
import threading
import requests
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import Optional

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")
STARTING_BALANCE = 100.0
GAMMA_API = "https://gamma-api.polymarket.com/markets"

_lock = threading.Lock()


@dataclass
class Position:
    id:           str       # unieke ID
    question:     str
    direction:    str       # "YES" of "NO"
    amount:       float     # ingezet USDC
    entry_price:  float     # prijs bij inzet (0-1)
    shares:       float     # aantal shares gekocht
    model_prob:   float     # model kans bij trade
    gap:          float     # gap bij trade
    timestamp:    str
    condition_id: str = ""
    market_id:    str = ""        # numeriek Gamma API id voor prijsupdates
    order_id:     str = ""        # Polymarket CLOB order ID (GTC limit order)
    order_filled: bool = False    # True zodra het limit order gevuld is
    note:         str = ""        # extra label, bijv. "[WHALE:ColdMath]" of "[MODEL]"
    status:       str = "open"    # open / won / lost / cancelled / pending_fill
    exit_price:   float = 0.0
    pnl:          float = 0.0     # gerealiseerde P&L
    resolved_at:  str = ""
    current_price: float = 0.0   # live prijs voor unrealized

    def unrealized_pnl(self) -> float:
        if self.status != "open":
            return 0.0
        # Waarde van positie nu vs inzet
        current_value = self.shares * self.current_price
        return round(current_value - self.amount, 3)

    def to_dict(self):
        d = asdict(self)
        d["unrealized_pnl"] = round(self.unrealized_pnl(), 3)
        return d


@dataclass
class Portfolio:
    starting_balance: float = STARTING_BALANCE
    cash:             float = STARTING_BALANCE
    positions:        list  = field(default_factory=list)   # list van Position dicts
    trade_count:      int   = 0
    created_at:       str   = ""

    @property
    def open_positions(self) -> list[Position]:
        return [Position(**{k: v for k, v in p.items() if k != "unrealized_pnl"})
                for p in self.positions if p["status"] == "open"]

    @property
    def closed_positions(self) -> list[Position]:
        return [Position(**{k: v for k, v in p.items() if k != "unrealized_pnl"})
                for p in self.positions if p["status"] in ("won", "lost", "sold")]

    @property
    def realized_pnl(self) -> float:
        return round(sum(p["pnl"] for p in self.positions if p["status"] in ("won", "lost", "sold")), 3)

    @property
    def unrealized_pnl(self) -> float:
        return round(sum(
            Position(**{k: v for k, v in p.items() if k != "unrealized_pnl"}).unrealized_pnl()
            for p in self.positions if p["status"] == "open"
        ), 3)

    @property
    def total_pnl(self) -> float:
        return round(self.realized_pnl + self.unrealized_pnl, 3)

    @property
    def open_value(self) -> float:
        return round(sum(p["amount"] for p in self.positions if p["status"] == "open"), 2)

    @property
    def total_equity(self) -> float:
        return round(self.cash + self.open_value + self.unrealized_pnl, 2)

    def win_rate(self) -> float:
        closed = self.closed_positions
        if not closed:
            return 0.0
        wins = sum(1 for p in closed if p.pnl > 0)
        return round(wins / len(closed) * 100, 1)


# ── Opslaan / laden ───────────────────────────────────────────────────────────

def load_portfolio(portfolio_file: str = None) -> Portfolio:
    path = portfolio_file or PORTFOLIO_FILE
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            p = Portfolio(
                starting_balance=data.get("starting_balance", STARTING_BALANCE),
                cash=data.get("cash", STARTING_BALANCE),
                positions=data.get("positions", []),
                trade_count=data.get("trade_count", 0),
                created_at=data.get("created_at", ""),
            )
            return p
        except Exception:
            pass
    # Nieuw portfolio
    p = Portfolio()
    p.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    save_portfolio(p, portfolio_file=path)
    return p


def save_portfolio(p: Portfolio, portfolio_file: str = None):
    path = portfolio_file or PORTFOLIO_FILE
    with open(path, "w") as f:
        json.dump({
            "starting_balance": p.starting_balance,
            "cash":             p.cash,
            "positions":        p.positions,
            "trade_count":      p.trade_count,
            "created_at":       p.created_at,
        }, f, indent=2)


# ── Trades registreren ────────────────────────────────────────────────────────

def record_trade(
    question:       str,
    direction:      str,   # "YES" of "NO"
    amount:         float,
    entry_price:    float,
    model_prob:     float,
    gap:            float,
    condition_id:   str = "",
    market_id:      str = "",
    order_id:       str = "",
    note:           str = "",
    portfolio_file: str = None,
) -> dict:
    """
    Registreert een nieuwe paper trade in het portfolio.
    Returns de nieuwe positie als dict.
    """
    with _lock:
        p = load_portfolio(portfolio_file)

        if amount > p.cash:
            return {"error": f"Onvoldoende cash (${p.cash:.2f} beschikbaar)"}

        shares = amount / entry_price if entry_price > 0 else 0

        p.trade_count += 1
        pos = Position(
            id=f"T{p.trade_count:04d}",
            question=question,
            direction=direction,
            amount=amount,
            entry_price=entry_price,
            shares=round(shares, 4),
            model_prob=model_prob,
            gap=gap,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            condition_id=condition_id,
            market_id=market_id,
            order_id=order_id,
            order_filled=order_id == "",  # direct filled als geen order_id (bijv. dry run)
            note=note,
            current_price=entry_price,
        )

        p.cash -= amount
        p.positions.append(pos.to_dict())
        save_portfolio(p, portfolio_file=portfolio_file)

        return pos.to_dict()


def sell_position(position_id: str, exit_price: float = None) -> dict:
    """Verkoop een positie tussentijds tegen huidige marktprijs."""
    with _lock:
        p = load_portfolio()
        for pos_dict in p.positions:
            if pos_dict["id"] == position_id and pos_dict["status"] == "open":
                pos = Position(**{k: v for k, v in pos_dict.items() if k != "unrealized_pnl"})
                price = exit_price if exit_price is not None else pos.current_price
                proceeds = round(pos.shares * price, 3)
                pnl = round(proceeds - pos.amount, 3)

                pos_dict.update({
                    "status":        "sold",
                    "exit_price":    round(price, 4),
                    "pnl":           pnl,
                    "resolved_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "current_price": round(price, 4),
                })
                p.cash += proceeds
                save_portfolio(p)
                return pos_dict
        return {"error": "Positie niet gevonden"}


def resolve_position(position_id: str, won: bool) -> dict:
    """Sluit een positie af als gewonnen (prijs→1) of verloren (prijs→0)."""
    with _lock:
        p = load_portfolio()
        for pos_dict in p.positions:
            if pos_dict["id"] == position_id and pos_dict["status"] == "open":
                pos = Position(**{k: v for k, v in pos_dict.items() if k != "unrealized_pnl"})
                exit_price = 1.0 if won else 0.0
                pnl = round((exit_price - pos.entry_price) * pos.shares, 3)

                pos_dict.update({
                    "status":      "won" if won else "lost",
                    "exit_price":  exit_price,
                    "pnl":         pnl,
                    "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "current_price": exit_price,
                })

                # Cash terug + winst
                if won:
                    p.cash += pos.amount + pnl  # inzet + winst
                # Bij verlies: inzet is al afgetrokken bij trade

                save_portfolio(p)
                return pos_dict
        return {"error": "Positie niet gevonden"}


def backup_portfolio(portfolio_file: str = None, label: str = "") -> str:
    """
    Maakt een timestamped backup van het portfolio bestand.
    Slaat op in data/backups/portfolio_YYYYMMDD_HHMMSS[_label].json
    Returns het pad van de backup.
    """
    src = portfolio_file or PORTFOLIO_FILE
    if not os.path.exists(src):
        return ""
    backup_dir = os.path.join(os.path.dirname(src), "data", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    base = os.path.splitext(os.path.basename(src))[0]
    dest = os.path.join(backup_dir, f"{base}_{ts}{suffix}.json")
    import shutil
    shutil.copy2(src, dest)
    return dest


def reset_portfolio(starting_balance: float = STARTING_BALANCE) -> Portfolio:
    """Reset portfolio naar beginstand. Maakt automatisch een backup."""
    with _lock:
        if os.path.exists(PORTFOLIO_FILE):
            backup_portfolio(label="pre_reset")
            os.remove(PORTFOLIO_FILE)
        p = Portfolio(starting_balance=starting_balance, cash=starting_balance)
        p.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        save_portfolio(p)
        return p


# ── Prijzen updaten ───────────────────────────────────────────────────────────

def update_position_prices(portfolio_file: str = None):
    """
    Haalt actuele prijzen op voor alle open posities.
    Detecteert automatisch resolved markten (prijs = 0 of 1).
    """
    with _lock:
        p = load_portfolio(portfolio_file)
        open_pos = [pos for pos in p.positions if pos["status"] == "open"]
        if not open_pos:
            return {"updated": 0, "resolved": 0}

        updated  = 0
        resolved = 0

        for pos_dict in open_pos:
            mid = pos_dict.get("market_id", "")
            cid = pos_dict.get("condition_id", "")
            direction = pos_dict["direction"]

            try:
                if mid:
                    # Gamma API op basis van market_id
                    r = requests.get(f"{GAMMA_API}/{mid}", timeout=6)
                    if r.status_code != 200:
                        continue
                    market = r.json()
                    if isinstance(market, list):
                        if not market:
                            continue
                        market = market[0]

                    prices_raw = market.get("outcomePrices", "[]")
                    if isinstance(prices_raw, str):
                        prices_raw = json.loads(prices_raw)

                    yes_price = float(prices_raw[0]) if prices_raw else 0.5
                    no_price  = float(prices_raw[1]) if len(prices_raw) > 1 else 1 - yes_price
                    cur_price = yes_price if direction == "YES" else no_price
                    is_closed = market.get("closed", False) or market.get("resolved", False)

                elif cid:
                    # CLOB API fallback op basis van condition_id
                    r = requests.get(
                        f"https://clob.polymarket.com/markets/{cid}",
                        timeout=6,
                    )
                    if r.status_code != 200:
                        continue
                    market = r.json()
                    tokens = market.get("tokens", [])
                    yes_tok = next((t for t in tokens if t.get("outcome","").lower() == "yes"), None)
                    no_tok  = next((t for t in tokens if t.get("outcome","").lower() == "no"), None)
                    yes_price = float(yes_tok["price"]) if yes_tok else 0.5
                    no_price  = float(no_tok["price"])  if no_tok  else 1 - yes_price
                    cur_price = yes_price if direction == "YES" else no_price
                    is_closed = bool(market.get("closed")) or bool(market.get("accepting_orders") == False)
                else:
                    continue

                pos_dict["current_price"] = round(cur_price, 4)
                updated += 1

                # Auto-resolve: officieel gesloten OF prijs bijna zeker (≥98% / ≤2%)
                if is_closed or cur_price >= 0.98 or cur_price <= 0.02:
                    if cur_price >= 0.98:
                        resolve_position_inline(pos_dict, True, p)
                        resolved += 1
                    elif cur_price <= 0.02:
                        resolve_position_inline(pos_dict, False, p)
                        resolved += 1

            except Exception:
                continue

        save_portfolio(p, portfolio_file=portfolio_file)
        return {"updated": updated, "resolved": resolved}


def resolve_position_inline(pos_dict: dict, won: bool, p: Portfolio):
    """Resolveert een positie (intern gebruik, zonder extra lock)."""
    pos = Position(**{k: v for k, v in pos_dict.items() if k != "unrealized_pnl"})
    exit_price = 1.0 if won else 0.0
    pnl = round((exit_price - pos.entry_price) * pos.shares, 3)
    pos_dict.update({
        "status":      "won" if won else "lost",
        "exit_price":  exit_price,
        "pnl":         pnl,
        "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "current_price": exit_price,
    })
    if won:
        p.cash += pos.amount + pnl


def get_stats() -> dict:
    """Geeft een volledig overzicht van het portfolio."""
    save_daily_snapshot()
    p = load_portfolio()

    open_pos    = p.open_positions
    closed_pos  = p.closed_positions
    wins        = [c for c in closed_pos if c.status == "won"]
    losses      = [c for c in closed_pos if c.status == "lost"]

    return {
        "starting_balance": p.starting_balance,
        "cash":             round(p.cash, 2),
        "open_value":       round(p.open_value, 2),
        "total_equity":     round(p.total_equity, 2),
        "realized_pnl":     round(p.realized_pnl, 3),
        "unrealized_pnl":   round(p.unrealized_pnl, 3),
        "total_pnl":        round(p.total_pnl, 3),
        "total_pnl_pct":    round(p.total_pnl / p.starting_balance * 100, 1),
        "trade_count":      p.trade_count,
        "open_positions":   len(open_pos),
        "closed_positions": len(closed_pos),
        "wins":             len(wins),
        "losses":           len(losses),
        "win_rate":         p.win_rate(),
        "created_at":       p.created_at,
        "positions":        [pos.to_dict() for pos in
                             sorted(open_pos, key=lambda x: x.timestamp, reverse=True)[:30]]
                           + [pos.to_dict() for pos in
                             sorted(closed_pos, key=lambda x: x.timestamp, reverse=True)],
    }


_PORTFOLIO_LOG = os.path.join(os.path.dirname(__file__), "data", "portfolio_log.jsonl")


def save_daily_snapshot():
    """
    Slaat een dagelijkse portfolio snapshot op naar data/portfolio_log.jsonl.
    Wordt alleen opgeslagen als er vandaag nog geen snapshot is.
    """
    os.makedirs(os.path.dirname(_PORTFOLIO_LOG), exist_ok=True)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check of er vandaag al een snapshot is
    if os.path.exists(_PORTFOLIO_LOG):
        try:
            with open(_PORTFOLIO_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("date") == today_str:
                        return  # Vandaag al opgeslagen
        except Exception:
            pass

    p = load_portfolio()
    snapshot = {
        "date":           today_str,
        "equity":         round(p.total_equity, 2),
        "cash":           round(p.cash, 2),
        "total_pnl":      round(p.total_pnl, 3),
        "open_positions": len(p.open_positions),
        "win_rate":       p.win_rate(),
    }
    try:
        with open(_PORTFOLIO_LOG, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    print("── Paper Portfolio ──────────────────────────────────────")
    stats = get_stats()
    print(f"  Startkapitaal: ${stats['starting_balance']}")
    print(f"  Cash:          ${stats['cash']}")
    print(f"  Equity:        ${stats['total_equity']}")
    print(f"  P&L:           ${stats['total_pnl']} ({stats['total_pnl_pct']}%)")
    print(f"  Trades:        {stats['trade_count']} ({stats['wins']}W / {stats['losses']}L)")
    print(f"  Win rate:      {stats['win_rate']}%")
    print(f"  Aangemaakt:    {stats['created_at']}")
