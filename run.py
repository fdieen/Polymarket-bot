"""
Polymarket Bot — headless runner.

Start auto trader + Telegram bot zonder dashboard.
Gebruik dit als je de bot op de achtergrond wilt draaien.

  python run.py
  python run.py --dry-run   (expliciet dry run)
  python run.py --live      (live trading, vereist wallet keys)
"""
import sys
import time
import threading
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")


def _price_updater():
    """Updatet portfolioprijzen elke 5 minuten en resolved afgelopen markten."""
    from portfolio import update_position_prices
    while True:
        try:
            result = update_position_prices()
            if result["resolved"] > 0:
                log.info(f"Auto-resolved: {result['resolved']} posities")
        except Exception as e:
            log.warning(f"Price update fout: {e}")
        time.sleep(300)


def main():
    # Dry run bepalen
    if "--live" in sys.argv:
        dry_run = False
    elif "--dry-run" in sys.argv:
        dry_run = True
    else:
        dry_run = True  # default veilig

    mode = "DRY RUN" if dry_run else "LIVE TRADING"
    log.info(f"── Polymarket Bot gestart [{mode}] ──────────────")

    # Auto trader instellen
    from auto_trader import state, start as start_trader
    state.config.dry_run = dry_run
    state.config.enabled = True
    start_trader()
    log.info(f"Auto trader gestart (min_gap={state.config.min_gap*100:.0f}%, interval={state.config.scan_interval}s)")

    # Telegram bot starten
    try:
        from telegram_bot import start as start_tg
        start_tg()
        log.info("Telegram bot gestart — /status, /portfolio, /trades, /whales")
    except Exception as e:
        log.warning(f"Telegram bot niet gestart: {e}")

    # Portfolio price updater
    threading.Thread(target=_price_updater, daemon=True, name="price-updater").start()
    log.info("Price updater gestart (elke 5 min)")

    log.info("Bot draait — Ctrl+C om te stoppen")
    log.info("────────────────────────────────────────────────────")

    try:
        while True:
            time.sleep(60)
            # Elke minuut: log korte status
            from auto_trader import state as s
            log.info(f"Status: {s.status} | budget_left=${s.budget_left:.0f} | trades={len(s.trades_today)}")
    except KeyboardInterrupt:
        log.info("Bot gestopt")
        from auto_trader import stop
        stop()


if __name__ == "__main__":
    main()
