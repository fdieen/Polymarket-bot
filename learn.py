"""
Leermodule — analyseert trade-historiek en geeft aanbevelingen.

Groepeert trades op:
  - Vraagtype (exact / above / below)
  - Stad
  - Entry price range
  - Dagen vooruit (hoe ver van resolutiedatum)
  - Model-gap grootte

Run: python3 learn.py
"""
import json, re
from collections import defaultdict
from portfolio import load_portfolio


def analyze() -> dict:
    p = load_portfolio()
    resolved = [x for x in p.positions if x["status"] in ("won", "lost")]

    if len(resolved) < 5:
        print(f"Te weinig data ({len(resolved)} trades). Min 5 nodig.")
        return {}

    # ── Helpers ──────────────────────────────────────────────────────────────

    def condition_type(question: str) -> str:
        q = question.lower()
        if "or higher" in q or "or above" in q: return "above"
        if "or below" in q or "or lower" in q:  return "below"
        if "between" in q:                       return "between"
        return "exact"

    def city_from(question: str) -> str:
        m = re.search(r"in ([A-Z][a-zA-Z ]+?) (?:be|on)", question)
        return m.group(1).strip().lower() if m else "unknown"

    def entry_bucket(entry: float) -> str:
        if entry >= 0.80: return "≥80% (near-certain)"
        if entry >= 0.60: return "60-80%"
        if entry >= 0.40: return "40-60%"
        if entry >= 0.20: return "20-40%"
        return "<20% (long shot)"

    def gap_bucket(gap: float) -> str:
        ag = abs(gap)
        if ag >= 0.60: return "≥60%"
        if ag >= 0.50: return "50-60%"
        if ag >= 0.40: return "40-50%"
        return "<40%"

    # ── Groepeer ─────────────────────────────────────────────────────────────

    def make_bucket():
        return {"won": 0, "lost": 0, "pnl": 0.0, "invested": 0.0}

    by_type   = defaultdict(make_bucket)
    by_city   = defaultdict(make_bucket)
    by_entry  = defaultdict(make_bucket)
    by_dir    = defaultdict(make_bucket)
    by_gap    = defaultdict(make_bucket)

    for t in resolved:
        won = t["status"] == "won"
        pnl = t.get("pnl", 0)
        amt = t.get("amount", 0)

        def record(d, key):
            d[key]["won" if won else "lost"] += 1
            d[key]["pnl"] += pnl
            d[key]["invested"] += amt

        record(by_type,  condition_type(t["question"]))
        record(by_city,  city_from(t["question"]))
        record(by_entry, entry_bucket(t["entry_price"]))
        record(by_dir,   t["direction"])
        record(by_gap,   gap_bucket(t.get("gap", 0)))

    # ── Print ─────────────────────────────────────────────────────────────────

    def print_table(title: str, data: dict, min_trades: int = 2):
        print(f"\n{'─'*70}")
        print(f"  {title}")
        print(f"{'─'*70}")
        print(f"  {'Categorie':<22} {'W':>4} {'L':>4} {'WR%':>6} {'P&L':>8} {'ROI':>7}")
        print(f"  {'─'*22} {'─'*4} {'─'*4} {'─'*6} {'─'*8} {'─'*7}")
        rows = []
        for k, v in data.items():
            total = v["won"] + v["lost"]
            if total < min_trades:
                continue
            wr  = v["won"] / total * 100
            roi = v["pnl"] / v["invested"] * 100 if v["invested"] > 0 else 0
            rows.append((roi, k, v["won"], v["lost"], wr, v["pnl"], roi))
        for _, k, w, l, wr, pnl, roi in sorted(rows, key=lambda x: -x[0]):
            flag = " ✓" if roi > 0 else " ✗"
            print(f"  {k:<22} {w:>4} {l:>4} {wr:>5.0f}% ${pnl:>7.2f} {roi:>6.0f}%{flag}")

    total_trades = len(resolved)
    total_won    = sum(1 for t in resolved if t["status"] == "won")
    total_pnl    = sum(t.get("pnl", 0) for t in resolved)
    total_inv    = sum(t.get("amount", 0) for t in resolved)

    print(f"\n{'═'*70}")
    print(f"  LEERRAPPORT — {total_trades} afgeronde trades")
    print(f"{'═'*70}")
    print(f"  Win rate:  {total_won/total_trades*100:.0f}%  ({total_won}W / {total_trades-total_won}L)")
    print(f"  P&L:       ${total_pnl:+.2f}  op ${total_inv:.0f} ingezet  (ROI {total_pnl/total_inv*100:+.0f}%)")

    print_table("VRAAGTYPE",    by_type,  min_trades=2)
    print_table("RICHTING",     by_dir,   min_trades=2)
    print_table("ENTRY PRIJS",  by_entry, min_trades=2)
    print_table("GAP GROOTTE",  by_gap,   min_trades=2)
    print_table("STAD",         by_city,  min_trades=2)

    # ── Aanbevelingen ────────────────────────────────────────────────────────

    print(f"\n{'═'*70}")
    print("  AANBEVELINGEN (op basis van data)")
    print(f"{'═'*70}")

    recs = []

    # Verliesgevende entry ranges
    for k, v in by_entry.items():
        total = v["won"] + v["lost"]
        if total < 3: continue
        roi = v["pnl"] / v["invested"] * 100 if v["invested"] > 0 else 0
        if roi < -10:
            recs.append(f"  ✗ Vermijd entry range {k} (ROI {roi:.0f}%, {total} trades)")

    # Verliesgevende vraagtypen
    for k, v in by_type.items():
        total = v["won"] + v["lost"]
        if total < 3: continue
        roi = v["pnl"] / v["invested"] * 100 if v["invested"] > 0 else 0
        if roi < -10:
            recs.append(f"  ✗ Vermijd '{k}' vragen (ROI {roi:.0f}%, {total} trades)")

    # Winstgevende combinaties
    for k, v in by_entry.items():
        total = v["won"] + v["lost"]
        if total < 3: continue
        roi = v["pnl"] / v["invested"] * 100 if v["invested"] > 0 else 0
        if roi > 20:
            recs.append(f"  ✓ Focussen op entry range {k} (ROI {roi:.0f}%, {total} trades)")

    # Verliesgevende steden (≥3 trades)
    for k, v in by_city.items():
        total = v["won"] + v["lost"]
        if total < 3: continue
        wr = v["won"] / total * 100
        if wr < 40:
            recs.append(f"  ✗ Vermijd {k.title()} (win rate {wr:.0f}%, {total} trades)")

    if recs:
        for r in recs:
            print(r)
    else:
        print("  Nog niet genoeg data voor betrouwbare aanbevelingen.")
        print("  Minimaal 3 trades per categorie nodig.")

    return {"trades": total_trades, "win_rate": total_won/total_trades, "pnl": total_pnl}


if __name__ == "__main__":
    analyze()
