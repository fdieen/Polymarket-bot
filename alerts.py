"""
Alerts — Telegram notificaties bij grote kansen.

Setup:
  1. Maak een bot via @BotFather op Telegram → /newbot
  2. Kopieer het token
  3. Stuur je bot een bericht, dan: https://api.telegram.org/bot<TOKEN>/getUpdates
  4. Zet in .env:
       TELEGRAM_BOT_TOKEN=1234567890:AABBccDDeeff...
       TELEGRAM_CHAT_ID=123456789

Gebruik:
  from alerts import send_alert, notify_opportunity
  notify_opportunity(opportunity)
"""
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Meerdere chat IDs mogelijk: TELEGRAM_CHAT_ID=123,456,789
_raw_ids = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_ids.split(",") if cid.strip()]
TELEGRAM_CHAT_ID  = TELEGRAM_CHAT_IDS[0] if TELEGRAM_CHAT_IDS else ""

# Minimale gap om te notificeren (kan ook via .env)
ALERT_MIN_GAP = float(os.getenv("ALERT_MIN_GAP", "0.40"))


def telegram_configured() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_IDS)


def send_telegram(message: str) -> bool:
    """Stuurt een Telegram bericht naar alle ontvangers. Returns True als minstens 1 lukt."""
    if not telegram_configured():
        return False
    success = False
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id":    chat_id,
                    "text":       message,
                    "parse_mode": "HTML",
                },
                timeout=8,
            )
            if r.status_code == 200:
                success = True
        except Exception:
            continue
    return success


def notify_opportunity(opp, source: str = "WEER") -> bool:
    """
    Stuurt een Telegram alert voor een gevonden kans.
    Werkt met WeatherOpportunity, F1Opportunity, HurricaneOpportunity.
    """
    if abs(opp.gap) < ALERT_MIN_GAP:
        return False

    sign     = "+" if opp.gap > 0 else ""
    label    = opp.label() if hasattr(opp, 'label') else "?"
    ts       = datetime.now().strftime("%H:%M")
    gap_pct  = f"{sign}{opp.gap*100:.1f}%"
    poly_pct = f"{opp.poly_price*100:.0f}%"
    model_pct = f"{opp.model_prob*100:.0f}%"
    direction = opp.direction

    # Emoji op basis van sterkte
    emoji = "🔥" if label == "STERK" else "📈" if label == "GOED" else "📊"

    msg = (
        f"{emoji} <b>POLYMARKET KANS [{label}]</b> — {ts}\n"
        f"\n"
        f"<b>{source} ARB</b>\n"
        f"📋 {opp.question}\n"
        f"\n"
        f"• Polymarket:  <b>{poly_pct}</b> YES\n"
        f"• Model:       <b>{model_pct}</b> YES\n"
        f"• GAP:         <b>{gap_pct}</b>\n"
        f"• Actie:       <b>{direction}</b>\n"
    )

    # Extra velden per type
    if hasattr(opp, 'forecast_temp'):
        msg += f"• Voorspelling: {opp.forecast_temp}°{opp.unit}\n"
    if hasattr(opp, 'rain_pct'):
        msg += f"• Regen: {opp.rain_pct}% kans\n"
    if hasattr(opp, 'storm_info') and opp.storm_info:
        msg += f"• Storm: {opp.storm_info}\n"

    if hasattr(opp, 'volume'):
        msg += f"• Volume: ${float(opp.volume):,.0f}/dag\n"

    return send_telegram(msg)


def notify_auto_trade(trade, dry_run: bool = False) -> bool:
    """Stuurt een alert wanneer de auto-trader een order plaatst."""
    if not telegram_configured():
        return False

    sign = "+" if trade.gap > 0 else ""
    ts   = datetime.now().strftime("%H:%M")
    note = trade.note or ""

    # ── Whale copy trade — ander formaat ───────────────────────────────────
    # note formaat: [WHALE:naam:conviction]
    if note.startswith("[WHALE:"):
        try:
            parts = note.strip("[]").split(":")
            whale_name  = parts[1] if len(parts) > 1 else "?"
            conviction  = parts[2].upper() if len(parts) > 2 else "?"
            conviction_nl = {"HIGH": "HOOG", "MEDIUM": "MIDDEL", "LOW": "LAAG"}.get(conviction, conviction)
        except Exception:
            whale_name = "?"
            conviction_nl = "?"

        msg = (
            f"🐋 [WHALE COPY] {whale_name} — {conviction_nl} convictie\n"
            f"\n"
            f"📋 {trade.question[:65]}\n"
            f"• Actie: <b>{trade.direction}</b> | <b>${trade.amount:.2f}</b>\n"
            f"• Whale bet: ${getattr(trade, 'whale_amount', trade.amount):.0f}\n"
            f"• GAP: <b>{sign}{trade.gap*100:.1f}%</b>\n"
        )
        # Slug link
        slug = getattr(trade, "slug", "")
        if slug:
            msg += f"• <a href=\"https://polymarket.com/event/{slug}\">Bekijk op Polymarket</a>\n"
        return send_telegram(msg)

    # ── Standaard auto trade ────────────────────────────────────────────────
    prefix = "🧪 [DRY RUN]" if dry_run else "✅ [LIVE TRADE]"

    msg = (
        f"{prefix} Auto Trader — {ts}\n"
        f"\n"
        f"📋 {trade.question[:65]}\n"
        f"• Actie:  <b>{trade.direction}</b>\n"
        f"• Bedrag: <b>${trade.amount:.2f}</b> USDC\n"
        f"• GAP:    <b>{sign}{trade.gap*100:.1f}%</b>\n"
        f"• Model:  {trade.model_prob*100:.0f}% | Poly: {trade.poly_price*100:.0f}%\n"
    )

    # Horizon (dagen vooruit)
    try:
        from datetime import date as _date
        trade_date = getattr(trade, "date", None)
        if trade_date:
            days_ahead = (_date.fromisoformat(trade_date) - _date.today()).days
            msg += f"• Horizon: {days_ahead} dag{'en' if days_ahead != 1 else ''}\n"
    except Exception:
        pass

    # MOS bias
    try:
        from weather_sources import CITY_META, get_mos_bias
        city = getattr(trade, "city", "").lower()
        trade_date = getattr(trade, "date", None)
        meta = CITY_META.get(city)
        if meta and trade_date:
            month = int(trade_date[5:7])
            mos = get_mos_bias(city, month, meta[1], meta[2])
            if abs(mos) > 0.5:
                mos_sign = "+" if mos > 0 else ""
                msg += f"• MOS correctie: {mos_sign}{mos:.2f}°C\n"
    except Exception:
        pass

    # Slug link
    slug = getattr(trade, "slug", "")
    if slug:
        msg += f"• <a href=\"https://polymarket.com/event/{slug}\">Bekijk op Polymarket</a>\n"

    if note and not note.startswith("["):
        msg += f"• Note: {note}\n"

    return send_telegram(msg)


def notify_daily_summary(trades: list, spent: float) -> bool:
    """Dagelijkse samenvatting."""
    if not trades:
        return False
    ts = datetime.now().strftime("%Y-%m-%d")
    wins = sum(1 for t in trades if t.success)
    msg = (
        f"📊 <b>Dagoverzicht {ts}</b>\n"
        f"\n"
        f"• Trades: {len(trades)} ({wins} succesvol)\n"
        f"• Besteed: ${spent:.2f} USDC\n"
        f"\n"
        f"<b>Top trades:</b>\n"
    )
    for t in sorted(trades, key=lambda x: abs(x.gap), reverse=True)[:5]:
        sign = "+" if t.gap > 0 else ""
        msg += f"  {sign}{t.gap:.1f}% — {t.question[:45]}\n"
    return send_telegram(msg)


def test_connection() -> dict:
    """Test of Telegram correct geconfigureerd is."""
    if not TELEGRAM_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN niet ingesteld in .env"}
    if not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID niet ingesteld in .env"}

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe",
            timeout=8,
        )
        if r.status_code == 200:
            bot_name = r.json().get("result", {}).get("username", "?")
            # Test bericht sturen
            ok = send_telegram("✅ Polymarket Bot verbonden — alerts actief!")
            return {"ok": ok, "bot": bot_name, "chat_id": TELEGRAM_CHAT_ID}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:100]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    print("── Telegram Alert Test ──────────────────────────────────")
    result = test_connection()
    if result["ok"]:
        print(f"✓ Verbonden als @{result['bot']}")
        print(f"  Chat ID: {result['chat_id']}")
    else:
        print(f"✗ Fout: {result['error']}")
        print()
        print("Setup instructies:")
        print("  1. Open Telegram → zoek @BotFather")
        print("  2. Stuur /newbot → volg instructies")
        print("  3. Kopieer token naar .env: TELEGRAM_BOT_TOKEN=xxx")
        print("  4. Stuur je nieuwe bot een bericht")
        print("  5. Open: https://api.telegram.org/bot<TOKEN>/getUpdates")
        print("  6. Kopieer chat.id naar .env: TELEGRAM_CHAT_ID=xxx")
