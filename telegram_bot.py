"""
Telegram Bot Command Handler — reageert op /commands.

Beschikbare commands:
  /portfolio  — P&L overzicht van het paper portfolio
  /trades     — Recente trades van vandaag
  /status     — Auto trader status
  /whales     — Whale posities
  /help       — Lijst van commands

Draait als achtergrond-thread via long polling (geen webhook nodig).
"""
import os
import time
import threading
import requests
import logging
from datetime import datetime, timezone

log = logging.getLogger("tg_bot")

_stop = threading.Event()
_offset = 0


def get_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def send(chat_id: str, text: str):
    token = get_token()
    if not token or token == "***":
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass


def handle_command(cmd: str, chat_id: str):
    cmd = cmd.lower().strip().split()[0]  # negeer argumenten

    if cmd in ("/crypto", "/btc", "/c"):
        try:
            from portfolio import load_portfolio
            WHALE_PF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "whale_portfolio.json")
            p = load_portfolio(WHALE_PF)
            open_pos   = p.open_positions
            closed_pos = p.closed_positions
            wins    = sum(1 for x in closed_pos if x.status == "won")
            losses  = sum(1 for x in closed_pos if x.status in ("lost", "sold"))
            real_pnl = round(sum(x.pnl for x in closed_pos), 2)
            unreal   = round(sum(x.unrealized_pnl() for x in open_pos), 2)
            equity   = round(p.cash + p.open_value + unreal, 2)

            msg = (
                f"₿ <b>Crypto Portfolio</b>\n\n"
                f"💰 Equity:     <b>${equity}</b>\n"
                f"💵 Cash:       ${round(p.cash,2)}\n"
                f"📈 Unrealized: {'+' if unreal>=0 else ''}${unreal}\n"
                f"✅ Realized:   {'+' if real_pnl>=0 else ''}${real_pnl}\n\n"
                f"📋 Open posities: {len(open_pos)}\n"
                f"🎯 Gesloten: {len(closed_pos)} | {wins}W / {losses}L\n"
                f"🏦 Start: ${p.starting_balance}"
            )

            # Top 3 open posities
            top = sorted(open_pos, key=lambda x: x.unrealized_pnl(), reverse=True)[:3]
            if top:
                msg += "\n\n<b>Top open:</b>"
                for pos in top:
                    upnl = pos.unrealized_pnl()
                    sign = "+" if upnl >= 0 else ""
                    msg += f"\n  {pos.direction} ${pos.amount:.0f} @ {pos.entry_price*100:.0f}% → {sign}${upnl:.2f} | {pos.question[:40]}"
        except Exception as e:
            msg = f"Fout: {e}"

    elif cmd in ("/portfolio", "/pnl", "/p"):
        try:
            from portfolio import get_stats, load_portfolio
            s = get_stats()
            pnl_sign = "+" if s["total_pnl"] >= 0 else ""

            # Tel whale vs model posities
            p = load_portfolio()
            whale_pos  = [x for x in p.positions if "WHALE" in x.get("note", "").upper()]
            model_pos  = [x for x in p.positions if "WHALE" not in x.get("note", "").upper()]
            whale_open = sum(1 for x in whale_pos if x["status"] == "open")
            model_open = sum(1 for x in model_pos if x["status"] == "open")

            msg = (
                f"📊 <b>Paper Portfolio</b>\n\n"
                f"💰 Equity:     <b>${s['total_equity']}</b>\n"
                f"💵 Cash:       ${s['cash']}\n"
                f"📈 Unrealized: {'+' if s['unrealized_pnl']>=0 else ''}${s['unrealized_pnl']}\n"
                f"✅ Realized:   {'+' if s['realized_pnl']>=0 else ''}${s['realized_pnl']}\n"
                f"📉 Totaal P&L: <b>{pnl_sign}${s['total_pnl']} ({pnl_sign}{s['total_pnl_pct']}%)</b>\n\n"
                f"📋 Trades: {s['trade_count']} | Open: {s['open_positions']}\n"
                f"🤖 Model: {model_open} open | 🐋 Whale: {whale_open} open\n"
                f"🎯 Win rate: {s['win_rate']}% ({s['wins']}W / {s['losses']}L)\n"
                f"🏦 Start: ${s['starting_balance']}"
            )
        except Exception as e:
            msg = f"Fout: {e}"

    elif cmd in ("/trades", "/t"):
        try:
            from auto_trader import state
            trades = list(reversed(state.trades_today[-10:]))
            if not trades:
                msg = "Geen trades vandaag."
            else:
                lines = []
                for t in trades:
                    icon = "✓" if t.success else "✗"
                    label = "[W]" if "WHALE" in t.note else "[M]"
                    lines.append(f"{icon}{label} {t.direction} ${t.amount:.1f} | {t.question[:40]}")
                msg = f"<b>Trades vandaag ({len(state.trades_today)})</b>\n\n" + "\n".join(lines)
        except Exception as e:
            msg = f"Fout: {e}"

    elif cmd in ("/status", "/s"):
        try:
            from auto_trader import state
            from portfolio import get_stats
            s = get_stats()
            d = state.to_dict()
            status_icon = "🟢" if d["enabled"] else "🔴"
            dry = "DRY RUN" if d["dry_run"] else "LIVE"
            msg = (
                f"{status_icon} <b>Auto Trader — {dry}</b>\n\n"
                f"• Status:      {d['status']}\n"
                f"• Min gap:     {d['min_gap']*100:.0f}%\n"
                f"• Budget over: ${d['budget_left']:.0f}\n"
                f"• Trades:      {d['trades_today']} vandaag\n"
                f"• Volgende:    {d['next_scan']} UTC\n"
                f"• Whale copy:  {'aan' if d.get('whale_copy') else 'uit'}\n\n"
                f"💼 Portfolio: ${s['total_equity']} equity | "
                f"{'+' if s['total_pnl']>=0 else ''}${s['total_pnl']} P&L"
            )
        except Exception as e:
            msg = f"Fout: {e}"

    elif cmd in ("/whales", "/w"):
        try:
            from whale_tracker import KNOWN_WHALES, fetch_whale_activity
            lines = []
            for name, addr in KNOWN_WHALES.items():
                trades = fetch_whale_activity(name, addr, limit=3)
                if trades:
                    t = trades[0]
                    lines.append(f"<b>{name}</b>: {t.side} {t.outcome} ${t.usdc_size:.0f} | {t.title[:35]}")
                else:
                    lines.append(f"<b>{name}</b>: geen recente trades")
            msg = "🐋 <b>Whale activiteit</b>\n\n" + "\n".join(lines)
        except Exception as e:
            msg = f"Fout: {e}"

    elif cmd in ("/help", "/h", "/start"):
        msg = (
            "🤖 <b>Polymarket Bot Commands</b>\n\n"
            "/portfolio — Weer portfolio P&L\n"
            "/crypto    — Crypto portfolio (BTC)\n"
            "/trades    — Trades van vandaag\n"
            "/status    — Auto trader status\n"
            "/whales    — Whale activiteit\n"
            "/help      — Dit bericht"
        )

    else:
        msg = f"Onbekend command: {cmd}\nStuur /help voor de lijst."

    send(chat_id, msg)


def _poll():
    global _offset
    token = get_token()
    if not token or token == "***":
        log.warning("Telegram token niet ingesteld — bot polling gestopt")
        return

    log.info("Telegram bot polling gestart")
    while not _stop.is_set():
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": _offset, "timeout": 20, "allowed_updates": ["message"]},
                timeout=25,
            )
            if r.status_code != 200:
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for update in updates:
                _offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if text.startswith("/") and chat_id:
                    log.info(f"Command: {text} van {chat_id}")
                    threading.Thread(
                        target=handle_command,
                        args=(text, chat_id),
                        daemon=True,
                    ).start()

        except requests.exceptions.ReadTimeout:
            continue
        except Exception as e:
            log.warning(f"Poll fout: {e}")
            time.sleep(5)

    log.info("Telegram bot polling gestopt")


def start():
    t = threading.Thread(target=_poll, daemon=True, name="tg-bot")
    t.start()


def stop():
    _stop.set()
