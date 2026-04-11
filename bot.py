"""
Polymarket Bot — hoofdloop.

Gebruik:
  python bot.py            # live modus
  python bot.py --dry-run  # alleen scannen, geen orders plaatsen
"""
import sys
import time
import os
from datetime import datetime
from dotenv import load_dotenv

from client import get_client
from strategy import find_opportunities, execute_opportunity

load_dotenv()

SCAN_INTERVAL     = int(os.getenv("SCAN_INTERVAL", "30"))
MAX_TOTAL_EXPOSURE = float(os.getenv("MAX_TOTAL_EXPOSURE", "50"))
MAX_OPEN_POSITIONS = 3  # Max aantal gelijktijdige posities

DRY_RUN = "--dry-run" in sys.argv


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def get_open_position_count(client) -> int:
    try:
        orders = client.get_orders()
        return len(orders) if orders else 0
    except Exception:
        return 0


def main():
    log("── Polymarket Bot gestart ──────────────────")
    if DRY_RUN:
        log("MODE: dry-run (geen echte orders)")
    else:
        log("MODE: live")
    log(f"Scan interval: {SCAN_INTERVAL}s | Max exposure: ${MAX_TOTAL_EXPOSURE}")
    log("────────────────────────────────────────────")

    try:
        client = get_client()
        log("Verbinding met Polymarket OK")
    except EnvironmentError as e:
        log(f"FOUT: {e}")
        sys.exit(1)

    while True:
        try:
            log("Markten scannen...")

            # Check huidige open posities
            open_positions = get_open_position_count(client)
            if open_positions >= MAX_OPEN_POSITIONS:
                log(f"Max posities bereikt ({open_positions}/{MAX_OPEN_POSITIONS}) — wachten")
                time.sleep(SCAN_INTERVAL)
                continue

            opportunities = find_opportunities(client)

            if not opportunities:
                log("Geen kansen gevonden met huidige instellingen")
            else:
                log(f"{len(opportunities)} kans(en) gevonden:")
                for i, opp in enumerate(opportunities[:5], 1):
                    log(f"  {i}. {opp.market_question[:60]}")
                    log(f"     Spread: {opp.spread:.3f} | Buy: {opp.our_buy_price} | Sell: {opp.our_sell_price}")

                # Neem de beste kans
                best = opportunities[0]
                if DRY_RUN:
                    log(f"DRY-RUN: zou handelen in '{best.market_question[:50]}'")
                else:
                    log(f"Handelend in: {best.market_question[:50]}")
                    success = execute_opportunity(client, best)
                    if success:
                        log("Orders geplaatst")
                    else:
                        log("Orders mislukt")

        except KeyboardInterrupt:
            log("Bot gestopt")
            if not DRY_RUN:
                log("Openstaande orders annuleren...")
                try:
                    client.cancel_all()
                    log("Alle orders geannuleerd")
                except Exception as e:
                    log(f"Fout bij annuleren: {e}")
            break
        except Exception as e:
            log(f"Onverwachte fout: {e}")

        log(f"Wachten {SCAN_INTERVAL}s...\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
