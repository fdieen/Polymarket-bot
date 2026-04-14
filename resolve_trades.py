"""
Betrouwbare trade-resolutie via Polymarket CLOB API.

Twee stappen:
  1. check_order_fills()  — kijk of GTC limit orders gevuld zijn
  2. resolve_open_trades() — kijk of gevulde posities gewonnen/verloren hebben

Run: python3 resolve_trades.py
Run: python3 resolve_trades.py --dry   (alleen rapporteren)
"""
import json, requests
from datetime import datetime, timezone
from portfolio import load_portfolio, save_portfolio, resolve_position_inline, PORTFOLIO_FILE

CLOB  = "https://clob.polymarket.com/markets"
GAMMA = "https://gamma-api.polymarket.com/markets"


def fetch_market_status(condition_id: str, market_id: str) -> dict | None:
    """
    Haalt marktdata op. Probeert eerst CLOB (condition_id), dan Gamma (market_id).
    Returns dict met: yes_price, no_price, resolved, closed
    """
    # 1. CLOB API via condition_id
    if condition_id:
        try:
            r = requests.get(f"{CLOB}/{condition_id}", timeout=8)
            if r.status_code == 200:
                m = r.json()
                tokens = m.get("tokens", [])
                yes_tok = next((t for t in tokens if t.get("outcome","").lower() == "yes"), None)
                no_tok  = next((t for t in tokens if t.get("outcome","").lower() == "no"),  None)
                yes_price = float(yes_tok["price"]) if yes_tok else None
                no_price  = float(no_tok["price"])  if no_tok  else None
                closed = (
                    bool(m.get("closed")) or
                    m.get("accepting_orders") == False or
                    bool(m.get("resolved"))
                )
                if yes_price is not None:
                    return {
                        "yes_price": yes_price,
                        "no_price":  no_price if no_price is not None else 1 - yes_price,
                        "closed":    closed,
                        "source":    "clob",
                    }
        except Exception:
            pass

    # 2. Gamma API via market_id
    if market_id:
        try:
            r = requests.get(f"{GAMMA}/{market_id}", timeout=8)
            if r.status_code == 200:
                m = r.json()
                if isinstance(m, list):
                    m = m[0] if m else {}
                prices_raw = m.get("outcomePrices", "[]")
                if isinstance(prices_raw, str):
                    prices_raw = json.loads(prices_raw)
                yes_price = float(prices_raw[0]) if prices_raw else None
                no_price  = float(prices_raw[1]) if len(prices_raw) > 1 else None
                closed = bool(m.get("closed")) or bool(m.get("resolved"))
                if yes_price is not None:
                    return {
                        "yes_price": yes_price,
                        "no_price":  no_price if no_price is not None else 1 - yes_price,
                        "closed":    closed,
                        "source":    "gamma",
                    }
        except Exception:
            pass

    return None


def check_order_fills(dry_run: bool = False) -> dict:
    """
    Controleert of openstaande GTC limit orders gevuld zijn.
    Zet status van 'pending_fill' naar 'open' zodra gevuld.
    """
    try:
        from auto_trader import get_clob_client
        client = get_clob_client()
    except Exception as e:
        print(f"  CLOB client niet beschikbaar: {e}")
        return {"checked": 0, "filled": 0}

    p = load_portfolio()
    pending = [x for x in p.positions if x.get("order_id") and not x.get("order_filled", False)]

    if not pending:
        return {"checked": 0, "filled": 0}

    filled = 0
    for pos in pending:
        oid = pos["order_id"]
        try:
            order = client.get_order(oid)
            status = order.get("status", "") if isinstance(order, dict) else ""
            size_matched = float(order.get("size_matched", 0) or 0)
            size_total   = float(order.get("original_size", order.get("size", 0)) or 0)

            if status in ("matched", "MATCHED") or (size_matched > 0 and size_matched >= size_total * 0.99):
                # Volledig gevuld — update werkelijke fill prijs en shares
                fill_price = float(order.get("price", pos["entry_price"]))
                actual_size = size_matched if size_matched > 0 else size_total
                actual_amount = round(fill_price * actual_size, 4)

                if not dry_run:
                    pos["order_filled"] = True
                    pos["entry_price"]  = round(fill_price, 4)
                    pos["shares"]       = round(actual_size, 4)
                    pos["amount"]       = actual_amount
                    pos["current_price"] = fill_price

                print(f"  [GEVULD] {pos['id']} @ {fill_price:.2%} | {actual_size:.1f} shares | {pos['question'][:50]}")
                filled += 1

            elif status in ("cancelled", "CANCELLED", "canceled"):
                if not dry_run:
                    pos["status"] = "cancelled"
                    pos["note"]   = pos.get("note", "") + " [GTC-CANCELLED]"
                print(f"  [GEANNULEERD] {pos['id']} | {pos['question'][:50]}")

            else:
                print(f"  [WACHT] {pos['id']} status={status} matched={size_matched:.1f}/{size_total:.1f} | {pos['question'][:45]}")

        except Exception as e:
            print(f"  FOUT {pos['id']}: {e}")

    if not dry_run and filled > 0:
        save_portfolio(p)

    return {"checked": len(pending), "filled": filled}


def resolve_open_trades(dry_run: bool = False) -> dict:
    """
    Controleert alle open posities en resolveert ze als de markt gesloten is.
    dry_run=True: alleen rapporteren, niets opslaan.
    """
    p = load_portfolio()
    open_pos = [x for x in p.positions if x["status"] == "open"]

    if not open_pos:
        print("Geen open posities.")
        return {"checked": 0, "resolved": 0, "updated": 0, "no_data": 0}

    checked = resolved = updated = no_data = 0

    for pos in open_pos:
        cid = pos.get("condition_id", "")
        mid = pos.get("market_id", "")
        direction = pos["direction"]  # YES of NO

        if not cid and not mid:
            print(f"  SKIP {pos['id']}: geen condition_id of market_id")
            no_data += 1
            continue

        status = fetch_market_status(cid, mid)
        checked += 1

        if status is None:
            print(f"  FOUT {pos['id']}: API geeft geen data [{pos['question'][:50]}]")
            no_data += 1
            continue

        yes_p = status["yes_price"]
        no_p  = status["no_price"]
        cur_p = yes_p if direction == "YES" else no_p
        closed = status["closed"]

        pos["current_price"] = round(cur_p, 4)
        updated += 1

        # Bepaal of de markt resolved is
        is_resolved = closed or yes_p >= 0.98 or yes_p <= 0.02

        if is_resolved:
            # Echte uitkomst: YES wint als yes_price → 1
            yes_won = yes_p >= 0.98
            if direction == "YES":
                won = yes_won
            else:  # NO
                won = not yes_won

            if dry_run:
                outcome = "WON" if won else "LOST"
                pnl_est = round((1.0 if won else 0.0 - pos["entry_price"]) * pos["shares"], 2)
                print(f"  [{outcome}] {pos['id']} | yes={yes_p:.2f} dir={direction} | "
                      f"P&L ~${pnl_est:+.2f} | {pos['question'][:55]}")
            else:
                resolve_position_inline(pos, won, p)
                outcome = "WON" if won else "LOST"
                print(f"  [{outcome}] {pos['id']} | {pos['question'][:55]}")
            resolved += 1
        else:
            print(f"  [OPEN] {pos['id']} | yes={yes_p:.2f} cur={cur_p:.2f} | {pos['question'][:55]}")

    if not dry_run and resolved > 0:
        save_portfolio(p)
        print(f"\n{resolved} posities resolved en opgeslagen.")
    elif dry_run:
        print(f"\n[DRY RUN] {resolved} posities zouden resolved worden.")

    print(f"Totaal: {checked} gecheckt | {resolved} resolved | {updated} bijgewerkt | {no_data} geen data")
    return {"checked": checked, "resolved": resolved, "updated": updated, "no_data": no_data}


if __name__ == "__main__":
    import sys
    dry = "--dry" in sys.argv
    print(f"=== TRADE RESOLUTIE {'(DRY RUN) ' if dry else ''}===\n")

    print("Stap 1: GTC order fills checken...")
    fill_result = check_order_fills(dry_run=dry)
    print(f"  {fill_result['filled']}/{fill_result['checked']} orders gevuld\n")

    print("Stap 2: Open posities resolven...")
    resolve_open_trades(dry_run=dry)
