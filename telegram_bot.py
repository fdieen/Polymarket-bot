"""
Telegram Bot Command Handler — reageert op /commands.

Beschikbare commands:
  /portfolio  — P&L overzicht van het live portfolio
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

    if cmd in ("/whale", "/crypto", "/w2", "/c"):
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

            winrate_line = (
                f"Win rate: {s['win_rate']}% ({s['wins']}W / {s['losses']}L)\n"
                if s['closed_positions'] > 0 else "Win rate: — (nog geen gesloten trades)\n"
            )
            pnl_line = (
                f"Totaal P&L: <b>{pnl_sign}${s['total_pnl']} ({pnl_sign}{s['total_pnl_pct']}%)</b>\n"
                if s['total_pnl'] != 0 else ""
            )
            msg = (
                f"📊 <b>Weather Portfolio</b>\n\n"
                f"💰 Equity:  <b>${s['total_equity']}</b>\n"
                f"💵 Cash:    ${s['cash']}\n"
                f"📦 Open:    ${s['open_value']} ({s['open_positions']} positie{'s' if s['open_positions']!=1 else ''})\n"
                + (f"📈 Unrealized: +${s['unrealized_pnl']}\n" if s['unrealized_pnl'] else "")
                + (f"✅ Realized: +${s['realized_pnl']}\n" if s['realized_pnl'] else "")
                + (pnl_line)
                + f"\n🤖 Model: {model_open} | 🐋 Whale: {whale_open} open\n"
                + winrate_line
                + f"🏦 Start: ${s['starting_balance']}"
            )
        except Exception as e:
            msg = f"Fout: {e}"

    elif cmd in ("/trades", "/t"):
        try:
            from portfolio import load_portfolio
            from datetime import date
            today = date.today().isoformat()
            p = load_portfolio()

            # Alle posities van vandaag (open + closed)
            today_trades = [
                pos for pos in p.positions
                if pos.get("timestamp", "").startswith(today)
            ]
            # Sorteer nieuwste eerst
            today_trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

            if not today_trades:
                # Toon laatste 10 trades als er vandaag niets is
                all_trades = sorted(p.positions, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
                header = f"<b>Laatste trades</b> (geen trades vandaag)\n\n"
            else:
                all_trades = today_trades[:10]
                header = f"<b>Trades vandaag ({len(today_trades)})</b>\n\n"

            if not all_trades:
                msg = "Nog geen trades."
            else:
                lines = []
                for pos in all_trades:
                    note  = pos.get("note", "")
                    label = "[W]" if "WHALE" in note.upper() else "[M]"
                    status = pos.get("status", "open")
                    icon  = "✅" if status == "won" else "❌" if status == "lost" else "⏳"
                    direction = pos.get("direction", "?")
                    amount    = float(pos.get("amount") or 0)
                    pnl       = float(pos.get("pnl") or 0)
                    ts        = pos.get("timestamp", "")[:16].replace("T", " ")
                    pnl_str   = f" | pnl={'+' if pnl>=0 else ''}${pnl:.2f}" if status in ("won","lost","sold") else ""
                    lines.append(f"{icon}{label} {direction} ${amount:.0f}{pnl_str}\n    {pos.get('question','')[:50]}")
                msg = header + "\n".join(lines)
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

    elif cmd in ("/whales", "/wh"):
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
            "/portfolio — Weather portfolio P&L\n"
            "/whale     — Whale copy portfolio\n"
            "/trades    — Recente trades\n"
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
                    # Whitelist: alleen toegestane chat IDs
                    allowed = [cid.strip() for cid in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if cid.strip()]
                    if allowed and chat_id not in allowed:
                        log.warning(f"Geblokkeerd: {chat_id} niet in whitelist")
                        send(chat_id, "Je hebt geen toegang tot deze bot.")
                        continue
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
