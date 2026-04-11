"""
Auto Trader — automatisch handelen op weather scanner kansen.

Draait als achtergrond-thread in het dashboard.
Scant elke N minuten, plaatst orders bij gap >= drempel.

Config via dashboard of .env:
  AUTO_TRADE=false           schakel in/uit
  AUTO_MIN_GAP=0.20          minimaal 20% gap vereist
  AUTO_MAX_TRADE=25          max $25 per trade
  AUTO_DAILY_BUDGET=200      max $200 per dag totaal
  AUTO_SCAN_INTERVAL=600     elke 10 minuten scannen
  AUTO_DRY_RUN=true          simuleer trades (geen echte orders)
"""
import os
import json
import time
import threading
import logging
from datetime import datetime, timezone, date
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("auto_trader")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUTO] %(message)s",
    datefmt="%H:%M:%S",
)

# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class AutoConfig:
    enabled:       bool  = os.getenv("AUTO_TRADE", "true").lower() != "false"
    min_gap:       float = float(os.getenv("AUTO_MIN_GAP",  "0.70"))
    max_trade:     float = float(os.getenv("AUTO_MAX_TRADE", "25"))
    daily_budget:  float = float(os.getenv("AUTO_DAILY_BUDGET", "200"))
    scan_interval: int   = int(os.getenv("AUTO_SCAN_INTERVAL", "600"))
    dry_run:       bool  = os.getenv("AUTO_DRY_RUN", "true").lower() != "false"
    kelly_fraction:    float = 0.25  # Quarter Kelly
    min_trade_amount:  float = 5.0   # minimaal $5 per trade
    max_deployed_pct:  float = 0.90  # max 90% van equity in open posities
    whale_copy:        bool  = True   # copieer whale trades
    whale_min_size:    float = 150.0  # minimale whale trade om te kopiëren ($) — alleen HIGH conviction
    whale_max_age_h:   float = 2.0    # max leeftijd whale trade (uur)
    ladder_enabled:    bool  = True   # ladder trading: meerdere buckets per stad/datum
    tail_bets:         bool  = False  # Hans323-stijl tail bets (<5¢ buckets met model >8%) — uit: geeft onrealistisch hoge returns
    tail_bet_amount:   float = 3.0    # vaste inzet per tail bet ($)


# ── State (thread-safe) ───────────────────────────────────────────────────────

class AutoTraderState:
    def __init__(self):
        self._lock          = threading.RLock()  # reentrant: zelfde thread mag meerdere keren locken
        self.config         = AutoConfig()
        self.running        = False
        self.status         = "idle"          # idle / scanning / trading / error
        self.last_scan      = None
        self.next_scan      = None
        self.trades_today   = []              # lijst van AutoTrade
        self.traded_markets = set()           # conditionId's al gehandeld vandaag
        self._daily_reset   = date.today()
        self.log_entries    = []              # laatste 50 log regels voor UI

    def _check_daily_reset(self):
        today = date.today()
        if today != self._daily_reset:
            self.trades_today.clear()
            self.traded_markets.clear()
            self._daily_reset = today

    @property
    def spent_today(self) -> float:
        with self._lock:
            return sum(t.amount for t in self.trades_today)

    @property
    def budget_left(self) -> float:
        daily_left = max(0, self.config.daily_budget - self.spent_today)
        try:
            from portfolio import load_portfolio
            p = load_portfolio()
            # Hard cap: gebaseerd op starting_balance, niet op huidige equity.
            # Voorkomt dat grote winsten automatisch meer ruimte openen.
            max_deployable = p.starting_balance * self.config.max_deployed_pct
            room_left = max(0, max_deployable - p.open_value)
            available = min(p.cash, room_left, p.starting_balance)
        except Exception:
            available = daily_left
        return min(daily_left, available)

    def add_log(self, msg: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            entry = f"{ts} {msg}"
            self.log_entries.append(entry)
            if len(self.log_entries) > 100:
                self.log_entries.pop(0)
        log.info(msg)

    def to_dict(self) -> dict:
        with self._lock:
            self._check_daily_reset()
            return {
                "enabled":       self.config.enabled,
                "running":       self.running,
                "status":        self.status,
                "dry_run":       self.config.dry_run,
                "min_gap":       self.config.min_gap,
                "max_trade":     self.config.max_trade,
                "daily_budget":  self.config.daily_budget,
                "scan_interval": self.config.scan_interval,
                "kelly_fraction":    self.config.kelly_fraction,
                "max_deployed_pct": self.config.max_deployed_pct,
                "whale_copy":       self.config.whale_copy,
                "whale_min_size":  self.config.whale_min_size,
                "whale_max_age_h": self.config.whale_max_age_h,
                "ladder_enabled":  self.config.ladder_enabled,
                "tail_bets":       self.config.tail_bets,
                "last_scan":     self.last_scan,
                "next_scan":     self.next_scan,
                "spent_today":   round(self.spent_today, 2),
                "budget_left":   round(self.budget_left, 2),
                "trades_today":  len(self.trades_today),
                "log":           list(reversed(self.log_entries[-30:])),
                "recent_trades": [t.to_dict() for t in reversed(self.trades_today[-10:])],
            }


@dataclass
class AutoTrade:
    timestamp:    str
    question:     str
    direction:    str
    poly_price:   float
    model_prob:   float
    gap:          float
    amount:       float
    dry_run:      bool
    success:      bool
    condition_id: str = ""
    note:         str = ""

    def to_dict(self):
        return {
            "timestamp":  self.timestamp,
            "question":   self.question[:65],
            "direction":  self.direction,
            "poly_price": self.poly_price,
            "model_prob": self.model_prob,
            "gap":        round(self.gap * 100, 1),
            "amount":     self.amount,
            "dry_run":    self.dry_run,
            "success":    self.success,
            "note":       self.note,
        }


# Globale state — gedeeld met dashboard
state = AutoTraderState()


# ── Trade uitvoering ──────────────────────────────────────────────────────────

def get_clob_client():
    """Maakt CLOB client aan vanuit .env credentials."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    from py_clob_client.constants import POLYGON

    pk         = os.getenv("PK")
    api_key    = os.getenv("CLOB_API_KEY")
    secret     = os.getenv("CLOB_SECRET")
    passphrase = os.getenv("CLOB_PASS_PHRASE")

    if not all([pk, api_key, secret, passphrase]):
        raise ValueError(".env incompleet — PK, CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE vereist")

    return ClobClient(
        host="https://clob.polymarket.com",
        key=pk,
        chain_id=POLYGON,
        creds=ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase),
    )


def execute_trade(opportunity, amount: float, dry_run: bool) -> tuple[bool, str]:
    """
    Voert een trade uit. Returns (success, note).
    Bij dry_run: simuleert alleen.
    """
    from weather_scanner import WeatherOpportunity

    direction    = opportunity.direction  # "BUY YES" of "BUY NO"
    outcome      = "Yes" if "YES" in direction else "No"
    condition_id = getattr(opportunity, "condition_id", "")

    if dry_run:
        return True, f"[DRY RUN] {direction} ${amount:.2f} @ {opportunity.poly_price*100:.0f}%"

    if not condition_id:
        return False, "Geen conditionId beschikbaar"

    try:
        from py_clob_client.clob_types import MarketOrderArgs
        client   = get_clob_client()
        market   = client.get_market(condition_id)
        tokens   = market.get("tokens", []) if isinstance(market, dict) else []
        token_id = None
        for t in tokens:
            if outcome.lower() in t.get("outcome", "").lower():
                token_id = t["token_id"]
                break

        if not token_id:
            return False, f"Token niet gevonden voor {outcome}"

        order = client.create_market_order(MarketOrderArgs(token_id=token_id, amount=amount))
        resp  = client.post_order(order, orderType="FOK")
        return True, f"Order OK: {resp}"

    except Exception as e:
        return False, f"Order fout: {e}"


def calculate_trade_size(opportunity, config: AutoConfig) -> float:
    """
    Kelly-gebaseerde positiegrootte.
    Bankroll is altijd gebaseerd op starting_balance (niet huidige cash),
    zodat winstkompounding de betgrootte niet opblaast.
    """
    from kelly import kelly

    poly_price  = opportunity.poly_price
    model_prob  = opportunity.model_prob
    budget_left = state.budget_left

    if budget_left <= 0:
        return 0

    # Gebruik starting_balance als Kelly-bankroll referentie.
    # Dit voorkomt dat een grote winst de volgende bet automatisch opblaast.
    try:
        from portfolio import load_portfolio
        _ref_bankroll = load_portfolio().starting_balance
    except Exception:
        _ref_bankroll = config.daily_budget

    # Voor NO trades: herspiegel de prijzen
    if "NO" in opportunity.direction:
        poly_price = 1 - poly_price
        model_prob = 1 - model_prob

    result = kelly(
        market_price=poly_price,
        your_probability=model_prob,
        bankroll=_ref_bankroll,
        fraction=config.kelly_fraction,
    )

    bet = result.get("bet", 0)
    # Hard cap: nooit meer dan max_trade, ongeacht bankroll of winsten
    return min(bet, config.max_trade, budget_left)


# ── Ladder trading ───────────────────────────────────────────────────────────

def execute_ladder_group(group: list, cfg: AutoConfig):
    """
    Ladder trading: koop meerdere buckets voor dezelfde stad/datum.
    Budget wordt verdeeld naar rato van gap-grootte.
    Elke individuele bet is kleiner (max cfg.max_trade / 2).
    """
    if not group:
        return

    total_gap   = sum(abs(o.gap) for o in group)
    total_budget = min(cfg.max_trade * 1.5, state.budget_left)  # iets meer dan single bet
    traded = 0

    city = group[0].city
    date = group[0].date
    state.add_log(f"🪜 LADDER {city} {date} — {len(group)} buckets, budget ${total_budget:.0f}")

    for opp in sorted(group, key=lambda o: abs(o.gap), reverse=True):
        if state.budget_left <= 1:
            break

        # Check of al open
        market_key = opp.question
        if market_key in state.traded_markets:
            continue
        try:
            from portfolio import load_portfolio
            _pf = load_portfolio()
            _cid = getattr(opp, "condition_id", "")
            if any(
                p["status"] == "open" and (
                    p["question"] == opp.question or
                    (_cid and p.get("condition_id") == _cid)
                )
                for p in _pf.positions
            ):
                state.traded_markets.add(market_key)
                continue
        except Exception:
            pass

        # Proportioneel budget o.b.v. gap
        weight = abs(opp.gap) / total_gap if total_gap > 0 else 1 / len(group)
        amount = round(min(total_budget * weight, cfg.max_trade / 2, state.budget_left), 2)

        if amount < cfg.min_trade_amount:
            amount = cfg.min_trade_amount

        if amount > state.budget_left:
            break

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        success, note = execute_trade(opp, amount, cfg.dry_run)

        trade = AutoTrade(
            timestamp=ts, question=opp.question, direction=opp.direction,
            poly_price=opp.poly_price, model_prob=opp.model_prob, gap=opp.gap,
            amount=amount if success else 0,
            dry_run=cfg.dry_run, success=success,
            note=f"[LADDER] {note}",
        )
        with state._lock:
            state.trades_today.append(trade)
            if success:
                state.traded_markets.add(market_key)

        sign = "+" if opp.gap > 0 else ""
        state.add_log(
            f"  {'✓' if success else '✗'} {opp.direction} ${amount:.2f} "
            f"gap={sign}{opp.gap*100:.1f}% | {opp.question[:45]}"
        )

        if success:
            try:
                from portfolio import record_trade
                outcome = "YES" if "YES" in opp.direction else "NO"
                price   = opp.poly_price if outcome == "YES" else (1 - opp.poly_price)
                record_trade(
                    question=opp.question, direction=outcome, amount=amount,
                    entry_price=price, model_prob=opp.model_prob, gap=opp.gap,
                    condition_id=getattr(opp, "condition_id", ""),
                    market_id=getattr(opp, "market_id", ""),
                    note="[LADDER]",
                )
            except Exception:
                pass
            try:
                from alerts import notify_auto_trade
                notify_auto_trade(trade, dry_run=cfg.dry_run)
            except Exception:
                pass
        traded += 1

    if traded:
        state.add_log(f"  Ladder klaar — {traded} buckets geplaatst")


def execute_tail_bets(all_opportunities: list, cfg: AutoConfig):
    """
    Hans323-stijl tail bets: buckets op <5¢ met model >8%.
    Kleine vaste inzet, grote multiplier als ze kloppen.
    """
    tail_candidates = [
        o for o in all_opportunities
        if o.poly_price < 0.05
        and o.model_prob > 0.08
        and (o.model_prob - o.poly_price) > 0.03
    ]

    if not tail_candidates:
        return

    state.add_log(f"🎯 TAIL BETS: {len(tail_candidates)} kandidaten gevonden")

    for opp in tail_candidates[:3]:  # max 3 tail bets per cyclus
        if state.budget_left < cfg.tail_bet_amount:
            break

        market_key = f"tail:{opp.question}"
        if market_key in state.traded_markets:
            continue

        # Check al open
        try:
            from portfolio import load_portfolio
            _pf = load_portfolio()
            _cid = getattr(opp, "condition_id", "")
            if any(
                p["status"] == "open" and (
                    p["question"] == opp.question or
                    (_cid and p.get("condition_id") == _cid)
                )
                for p in _pf.positions
            ):
                state.traded_markets.add(market_key)
                continue
        except Exception:
            pass

        amount  = cfg.tail_bet_amount
        ts      = datetime.now(timezone.utc).strftime("%H:%M:%S")
        success, note = execute_trade(opp, amount, cfg.dry_run)

        trade = AutoTrade(
            timestamp=ts, question=opp.question, direction=opp.direction,
            poly_price=opp.poly_price, model_prob=opp.model_prob, gap=opp.gap,
            amount=amount if success else 0,
            dry_run=cfg.dry_run, success=success,
            note=f"[TAIL] {note}",
        )
        with state._lock:
            state.trades_today.append(trade)
            if success:
                state.traded_markets.add(market_key)

        multiple = round(1 / opp.poly_price, 0) if opp.poly_price > 0 else 0
        state.add_log(
            f"{'✓' if success else '✗'} [TAIL] BUY YES ${amount:.0f} @ {opp.poly_price*100:.1f}¢ "
            f"({multiple:.0f}× potentieel) | {opp.question[:45]}"
        )

        if success:
            try:
                from portfolio import record_trade
                record_trade(
                    question=opp.question, direction="YES", amount=amount,
                    entry_price=opp.poly_price, model_prob=opp.model_prob, gap=opp.gap,
                    condition_id=getattr(opp, "condition_id", ""),
                    market_id=getattr(opp, "market_id", ""),
                    note="[TAIL]",
                )
            except Exception:
                pass


# ── Whale copy trading ────────────────────────────────────────────────────────

def run_whale_copy():
    """Kopieert recente whale trades die nog niet in ons portfolio zitten."""
    cfg = state.config
    if not cfg.whale_copy:
        return

    from whale_tracker import fetch_whale_activity
    from datetime import datetime, timezone, timedelta
    import json as _json, os as _os

    # Laad whales uit whales.json (weather specialists), filter op crypto whales
    _WHALES_FILE = _os.path.join(_os.path.dirname(__file__), "data", "whales.json")
    _CRYPTO_KEYWORDS = {"btc", "bitcoin", "crypto", "eth", "floor"}
    try:
        with open(_WHALES_FILE) as _f:
            _whale_list = _json.load(_f)
        # Alleen weather whales voor de auto-trader (geen crypto-specifieke whales)
        WEATHER_WHALES = {
            w["name"]: w["address"] for w in _whale_list
            if not any(kw in w.get("note", "").lower() for kw in _CRYPTO_KEYWORDS)
        }
    except Exception:
        from whale_tracker import KNOWN_WHALES as WEATHER_WHALES

    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.whale_max_age_h)
    traded = 0

    for whale_name, address in WEATHER_WHALES.items():
        try:
            trades = fetch_whale_activity(whale_name, address, limit=20)
        except Exception as e:
            state.add_log(f"Whale {whale_name} fout: {e}")
            continue

        for t in trades:
            if state.budget_left <= 1:
                break

            # Leeftijdscheck
            try:
                trade_dt = datetime.strptime(t.timestamp, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                if trade_dt < cutoff:
                    continue
            except Exception:
                continue

            # Minimale grootte
            if t.usdc_size < cfg.whale_min_size:
                continue

            # Alleen BUY orders kopiëren (geen sells)
            if t.side != "BUY":
                continue

            # Weather whales: alleen temperatuurmarkten kopiëren
            if "temperature" not in t.title.lower():
                continue

            # Niet twee keer dezelfde markt
            market_key = f"whale:{t.condition_id}"
            if market_key in state.traded_markets:
                continue

            # Trade sizing: schaalt mee met whale convictie
            # Hoe groter de whale trade, hoe hoger ons percentage én plafond
            if t.usdc_size >= 300:
                pct, cap_mult, conviction = 0.20, 3.0, "HOOG"
            elif t.usdc_size >= 150:
                pct, cap_mult, conviction = 0.17, 2.5, "HOOG"
            elif t.usdc_size >= 75:
                pct, cap_mult, conviction = 0.14, 2.0, "MATIG"
            elif t.usdc_size >= 35:
                pct, cap_mult, conviction = 0.12, 1.5, "MATIG"
            else:
                pct, cap_mult, conviction = 0.10, 1.0, "LAAG"

            max_for_trade = cfg.max_trade * cap_mult
            amount = min(t.usdc_size * pct, max_for_trade, state.budget_left)
            amount = round(amount, 2)
            if amount < 1:
                continue

            # Prijs ophalen voor portfolio registratie
            direction = "YES" if t.outcome == "Yes" else "NO"
            entry_price = t.price if direction == "YES" else (1 - t.price)

            # Tail bets altijd overslaan — entry <10¢ geeft onrealistisch hoge multipliers
            if entry_price < 0.10:
                state.add_log(f"SKIP tail bet {whale_name}: entry {entry_price*100:.1f}¢ < 10¢")
                continue

            # Simuleer of voer uit
            class _FakeOpp:
                pass
            opp = _FakeOpp()
            opp.question     = t.title
            opp.direction    = f"BUY {direction}"
            opp.poly_price   = t.price
            opp.model_prob   = t.price
            opp.gap          = 0.0
            opp.condition_id = t.condition_id
            opp.market_id    = ""
            success, note = execute_trade(opp, amount, cfg.dry_run)

            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            trade_rec = AutoTrade(
                timestamp=ts, question=t.title, direction=f"BUY {direction}",
                poly_price=t.price, model_prob=t.price, gap=0.0,
                amount=amount if success else 0,
                dry_run=cfg.dry_run, success=success,
                note=f"[WHALE:{whale_name}] {note}",
            )
            with state._lock:
                state.trades_today.append(trade_rec)
                if success:
                    state.traded_markets.add(market_key)

            state.add_log(
                f"{'✓' if success else '✗'} [WHALE {whale_name}] [{conviction}] BUY {direction} "
                f"${amount:.2f} (whale: ${t.usdc_size:.0f}) | {t.title[:45]}"
            )

            if success:
                try:
                    from portfolio import record_trade
                    record_trade(
                        question=t.title, direction=direction,
                        amount=amount, entry_price=entry_price,
                        model_prob=t.price, gap=0.0,
                        condition_id=t.condition_id, market_id="",
                        note=f"[WHALE:{whale_name}:{conviction}]",
                    )
                except Exception:
                    pass
                try:
                    from alerts import send_telegram
                    sign = "🧪" if cfg.dry_run else "✅"
                    conviction_icon = {"HOOG": "🔥", "MATIG": "📈", "LAAG": "📊"}.get(conviction, "📊")
                    send_telegram(
                        f"{sign} <b>WHALE COPY — {whale_name}</b>\n"
                        f"📋 {t.title[:65]}\n"
                        f"• Actie: <b>BUY {direction}</b>\n"
                        f"• Bedrag: <b>${amount:.2f}</b> (whale: ${t.usdc_size:.0f})\n"
                        f"• Convictie: {conviction_icon} <b>{conviction}</b> ({pct*100:.0f}% van whale)\n"
                        f"• Prijs: {t.price*100:.0f}%\n"
                        f"• Trade tijd: {t.timestamp}"
                    )
                except Exception:
                    pass

            traded += 1

    if traded:
        state.add_log(f"Whale copy klaar — {traded} trades gekopieerd")


# ── Scan + handelslogica ──────────────────────────────────────────────────────

def run_scan_and_trade():
    """Voert één scan-en-handel cyclus uit."""
    cfg = state.config
    state.status = "scanning"
    state.add_log(f"Scan gestart (min_gap={cfg.min_gap*100:.0f}%, budget_over=${state.budget_left:.0f})")

    # Scan weather markten
    try:
        from weather_scanner import scan
        opportunities = scan()
    except Exception as e:
        state.status = "error"
        state.add_log(f"Scan fout: {e}")
        return

    filtered = [o for o in opportunities if abs(o.gap) >= cfg.min_gap]
    state.add_log(f"Scan klaar — {len(opportunities)} kansen, {len(filtered)} >= {cfg.min_gap*100:.0f}% gap")

    # ── Tail bets (altijd, ook zonder filtered kansen) ─────────────────────────
    if cfg.tail_bets and opportunities:
        execute_tail_bets(opportunities, cfg)

    if not filtered:
        state.status = "idle"
        return

    state.status = "trading"
    traded = 0

    if cfg.ladder_enabled:
        # ── Ladder trading: groepeer per stad/datum ────────────────────────────
        from collections import defaultdict
        city_date_groups: dict = defaultdict(list)
        singles = []

        for opp in filtered:
            city = getattr(opp, "city", "")
            date = getattr(opp, "date", "")
            if city and date:
                city_date_groups[(city, date)].append(opp)
            else:
                singles.append(opp)

        for (city, date), group in city_date_groups.items():
            if state.budget_left <= 1:
                break
            if len(group) >= 2:
                # Meerdere buckets → ladder
                execute_ladder_group(group, cfg)
                traded += len(group)
            else:
                singles.append(group[0])

        # Verwerk singles normaal
        filtered_singles = singles
    else:
        filtered_singles = filtered

    for opp in filtered_singles:
        if state.budget_left <= 1:
            state.add_log("Dagbudget bereikt, stoppen")
            break

        market_key = opp.question
        # Also check tail-bet key: tail bets use f"tail:{opp.question}" so a market
        # could be traded as both a tail bet and a normal trade without this check.
        if market_key in state.traded_markets or f"tail:{market_key}" in state.traded_markets:
            continue
        try:
            from portfolio import load_portfolio
            _pf = load_portfolio()
            _cid = getattr(opp, "condition_id", "")
            _already = any(
                p["status"] == "open" and (
                    p["question"] == opp.question or
                    (_cid and p.get("condition_id") == _cid)
                )
                for p in _pf.positions
            )
            if _already:
                state.add_log(f"Skip {opp.question[:45]} — al open positie")
                state.traded_markets.add(market_key)
                continue
        except Exception:
            pass

        amount = calculate_trade_size(opp, cfg)
        if amount < cfg.min_trade_amount:
            state.add_log(f"Skip {opp.question[:40]} — bedrag te klein (${amount:.2f})")
            continue

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        success, note = execute_trade(opp, amount, cfg.dry_run)

        trade = AutoTrade(
            timestamp=ts, question=opp.question, direction=opp.direction,
            poly_price=opp.poly_price, model_prob=opp.model_prob, gap=opp.gap,
            amount=amount if success else 0,
            dry_run=cfg.dry_run, success=success, note=note,
        )

        with state._lock:
            state.trades_today.append(trade)
            if success:
                state.traded_markets.add(market_key)

        sign   = "+" if opp.gap > 0 else ""
        prefix = "[DRY]" if cfg.dry_run else "[LIVE]"
        state.add_log(f"{'✓' if success else '✗'} {prefix} {opp.direction} ${amount:.2f} | gap={sign}{opp.gap*100:.1f}% | {opp.question[:45]}")

        if success:
            try:
                from portfolio import record_trade
                outcome = "YES" if "YES" in opp.direction else "NO"
                price   = opp.poly_price if outcome == "YES" else (1 - opp.poly_price)
                record_trade(
                    question=opp.question, direction=outcome, amount=amount,
                    entry_price=price, model_prob=opp.model_prob, gap=opp.gap,
                    condition_id=getattr(opp, "condition_id", ""),
                    market_id=getattr(opp, "market_id", ""),
                    note="[MODEL]",
                )
            except Exception as e:
                state.add_log(f"Portfolio fout: {e}")
            try:
                from alerts import notify_auto_trade
                notify_auto_trade(trade, dry_run=cfg.dry_run)
            except Exception:
                pass

        traded += 1

        if not cfg.dry_run:
            time.sleep(0.5)

    state.add_log(f"Cyclus klaar — {traded} orders geplaatst")
    state.status = "idle"


# ── Achtergrond thread ────────────────────────────────────────────────────────

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _worker():
    state.add_log("Auto trader gestart")
    while not _stop_event.is_set():
        if state.config.enabled:
            try:
                with state._lock:
                    state._check_daily_reset()
                run_scan_and_trade()
                run_whale_copy()
            except Exception as e:
                state.status = "error"
                state.add_log(f"Worker fout: {e}")

        next_dt = datetime.now(timezone.utc)
        state.last_scan = next_dt.strftime("%H:%M:%S")
        from datetime import timedelta
        next_t = next_dt + timedelta(seconds=state.config.scan_interval)
        state.next_scan = next_t.strftime("%H:%M:%S")

        _stop_event.wait(timeout=state.config.scan_interval)

    state.add_log("Auto trader gestopt")
    state.running = False
    state.status  = "idle"


def start():
    """Start de achtergrond-thread."""
    global _thread
    if state.running:
        return
    _stop_event.clear()
    state.running = True
    _thread = threading.Thread(target=_worker, daemon=True, name="auto-trader")
    _thread.start()
    state.add_log("Thread gestart")


def stop():
    """Stop de achtergrond-thread."""
    _stop_event.set()
    state.running = False
    state.add_log("Stopsignaal gestuurd")


if __name__ == "__main__":
    print("Auto Trader — test modus (dry run)")
    state.config.enabled = True
    state.config.dry_run = True
    state.config.min_gap = 0.40
    run_scan_and_trade()
    for entry in state.log_entries:
        print(" ", entry)
    print(f"\nTrades gesimuleerd: {len(state.trades_today)}")
    for t in state.trades_today:
        print(f"  {t.direction} ${t.amount:.2f} | gap={t.gap*100:+.1f}% | {t.question[:60]}")
