"""
Morning Report — dagelijks overzicht van kansen, portfolio en whale activiteit.

Draait scan(), haalt portfolio stats op en whale activiteit, en stuurt
een tekstrapport via Telegram.

Run: venv/bin/python morning_report.py
"""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


def _get_yesterday_equity() -> float | None:
    """Leest portfolio_log.jsonl en geeft de equity van gisteren terug (of None)."""
    import pathlib
    import json
    from datetime import date, timedelta

    log_path = pathlib.Path(__file__).parent / "data" / "portfolio_log.jsonl"
    if not log_path.exists():
        return None

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    best = None
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts") or entry.get("timestamp", "")
                    if ts[:10] == yesterday:
                        equity = entry.get("equity") or entry.get("total_equity")
                        if equity is not None:
                            best = float(equity)
                except Exception:
                    continue
    except Exception:
        pass
    return best


def generate_report() -> str:
    from weather_scanner import scan
    from portfolio import get_stats
    from whale_tracker import KNOWN_WHALES, fetch_whale_activity
    import telegram_bot

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Morning Report — {now}", ""]

    # Portfolio stats
    try:
        stats = get_stats()
        pnl_sign = "+" if stats["total_pnl"] >= 0 else ""
        lines.append("PORTFOLIO")
        lines.append(f"  Equity:   ${stats['total_equity']}")
        lines.append(f"  Cash:     ${stats['cash']}")
        lines.append(f"  P&L:      {pnl_sign}${stats['total_pnl']} ({pnl_sign}{stats['total_pnl_pct']}%)")
        lines.append(f"  Win rate: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L)")
        lines.append(f"  Open:     {stats['open_positions']} posities")

        # Vergelijking met gisteren
        yesterday_eq = _get_yesterday_equity()
        if yesterday_eq is not None:
            today_eq = float(stats["total_equity"])
            delta = today_eq - yesterday_eq
            delta_pct = (delta / yesterday_eq * 100) if yesterday_eq else 0
            delta_sign = "+" if delta >= 0 else ""
            lines.append(f"  Gisteren: ${yesterday_eq:.2f} → vandaag: ${today_eq:.2f} ({delta_sign}{delta_pct:.1f}%)")

        lines.append("")
    except Exception as e:
        lines.append(f"Portfolio fout: {e}")
        lines.append("")

    # Scan kansen
    model_shifts_found = []
    try:
        opps = scan()
        lines.append(f"KANSEN VANDAAG ({len(opps)} gevonden)")
        top3 = opps[:3]
        if top3:
            for opp in top3:
                sign = "+" if opp.gap > 0 else ""
                lines.append(f"  [{opp.label()}] {opp.city} {opp.date}")
                lines.append(f"    {opp.direction} | gap={sign}{opp.gap*100:.1f}% | vol=${opp.volume:,.0f}")
                lines.append(f"    {opp.question[:65]}")
                # MOS bias info
                try:
                    from weather_sources import CITY_META, get_mos_bias
                    city_lower = opp.city.lower()
                    meta = CITY_META.get(city_lower)
                    if meta:
                        month = int(opp.date[5:7])
                        mos = get_mos_bias(city_lower, month, meta[1], meta[2])
                        if abs(mos) > 1.0:
                            mos_sign = "+" if mos > 0 else ""
                            lines.append(f"    MOS: {mos_sign}{mos:.2f}°C")
                except Exception:
                    pass
                # Model shift info
                if hasattr(opp, "model_shift") and opp.model_shift != 0.0:
                    shift_sign = "+" if opp.model_shift > 0 else ""
                    lines.append(f"    Model shift: {shift_sign}{opp.model_shift:.1f}°C")
                    model_shifts_found.append(f"{opp.city}: {shift_sign}{opp.model_shift:.1f}°C")
        else:
            lines.append("  Geen kansen gevonden vandaag.")
        lines.append("")
    except Exception as e:
        lines.append(f"Scan fout: {e}")
        lines.append("")

    # Model shifts samenvatting
    if model_shifts_found:
        lines.append(f"MODEL SHIFTS GEDETECTEERD ({len(model_shifts_found)})")
        for ms in model_shifts_found:
            lines.append(f"  ⚡ {ms}")
        lines.append("")

    # Whale activiteit — ColdMath laatste trades
    try:
        coldmath_addr = KNOWN_WHALES.get("ColdMath", "")
        if coldmath_addr:
            trades = fetch_whale_activity("ColdMath", coldmath_addr, limit=5)
            lines.append("WHALE ACTIVITEIT (ColdMath)")
            if trades:
                for t in trades:
                    lines.append(f"  {t.timestamp} | {t.side} {t.outcome} ${t.usdc_size:.0f} @ {t.price*100:.0f}%")
                    lines.append(f"    {t.title[:60]}")
            else:
                lines.append("  Geen recente trades.")
        lines.append("")
    except Exception as e:
        lines.append(f"Whale fout: {e}")
        lines.append("")

    return "\n".join(lines)


def send_report():
    import telegram_bot

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        print("TELEGRAM_CHAT_ID niet ingesteld in .env — rapport niet verstuurd")
        return

    report = generate_report()
    print(report)
    telegram_bot.send(chat_id, report)
    print("Rapport verstuurd via Telegram.")


if __name__ == "__main__":
    send_report()
