"""
Kelly Criterion voor Polymarket.

Formule:
  f* = (b × p - q) / b

  p = jouw geschatte kans (bijv. 0.70 = 70%)
  q = 1 - p
  b = (1 - marktprijs) / marktprijs  (de netto odds)

Aanbevolen: gebruik Quarter Kelly (0.25 × f*) om risico te beperken.
"""


def kelly(
    market_price: float,
    your_probability: float,
    bankroll: float,
    fraction: float = 0.25,
) -> dict:
    """
    Berekent de optimale inzetgrootte via Kelly Criterion.

    Args:
        market_price:     huidige prijs van YES (0.0 - 1.0)
        your_probability: jouw geschatte kans dat YES wint (0.0 - 1.0)
        bankroll:         totaal beschikbaar kapitaal in USDC
        fraction:         Kelly vermenigvuldiger (0.25 = Quarter Kelly)

    Returns:
        dict met aanbevolen inzet, verwacht rendement en uitleg
    """
    if not (0 < market_price < 1):
        return {"error": "Marktprijs moet tussen 0 en 1 liggen"}

    if not (0 < your_probability < 1):
        return {"error": "Jouw kans moet tussen 0 en 1 liggen"}

    p = your_probability
    q = 1 - p
    b = (1 - market_price) / market_price  # netto winst per ingezette dollar

    full_kelly = (b * p - q) / b
    frac_kelly = full_kelly * fraction

    if full_kelly <= 0:
        return {
            "bet": 0,
            "full_kelly_pct": 0,
            "frac_kelly_pct": 0,
            "edge": round((p - market_price) * 100, 1),
            "recommendation": "GEEN TRADE — negatieve verwachte waarde",
            "ev_per_dollar": round(p / market_price - 1, 4),
        }

    bet_amount = max(0, frac_kelly * bankroll)
    # Nooit meer dan 10% van bankroll per trade (veiligheidsgrens)
    bet_amount = min(bet_amount, bankroll * 0.10)

    edge = (p - market_price) * 100
    ev   = p / market_price - 1  # verwacht rendement per dollar

    return {
        "bet":              round(bet_amount, 2),
        "full_kelly_pct":   round(full_kelly * 100, 1),
        "frac_kelly_pct":   round(frac_kelly * 100, 1),
        "edge":             round(edge, 1),
        "ev_per_dollar":    round(ev * 100, 1),
        "recommendation":   _advice(edge, frac_kelly),
        "fraction_used":    fraction,
    }


def _advice(edge: float, frac_kelly: float) -> str:
    if edge < 3:
        return "ZWAKKE EDGE — overweeg te skippen (< 3%)"
    if edge < 8:
        return "MATIGE EDGE — klein inzetten"
    if edge < 15:
        return "GOEDE EDGE — normaal inzetten"
    return "STERKE EDGE — vol inzetten (check je aannames!)"


if __name__ == "__main__":
    # Voorbeeld
    print("── Kelly Calculator ────────────────────────")
    print("Markt:   US forces enter Iran (YES = 80¢)")
    print("Jij denkt: 70% kans")
    print()

    result = kelly(
        market_price=0.80,
        your_probability=0.70,
        bankroll=500,
        fraction=0.25,
    )

    for k, v in result.items():
        print(f"  {k}: {v}")

    print()
    print("── Tweede voorbeeld ────────────────────────")
    print("Markt:   Spurs vs Nuggets YES = 54¢")
    print("Jij denkt: 65% kans")
    print()

    result2 = kelly(
        market_price=0.54,
        your_probability=0.65,
        bankroll=500,
        fraction=0.25,
    )

    for k, v in result2.items():
        print(f"  {k}: {v}")
