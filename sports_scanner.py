"""
Sports Arbitrage Scanner — Polymarket vs Bookmakers.

Vergelijkt Polymarket sportsprijzen met 250+ bookmakers via The Odds API.
Signaleert kansen waar het verschil >= 8% is.

Setup:
  1. Haal gratis API key op via the-odds-api.com
  2. Zet in .env: ODDS_API_KEY=jouw_key
  3. Run: venv/bin/python sports_scanner.py

Documentatie: gedocumenteerde kansen van 10-13% per gevonden gelegenheid.
"""
import os
import json
import requests
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY  = os.getenv("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GAMMA_API     = "https://gamma-api.polymarket.com/markets"

# Minimaal verschil om te signaleren (8% = bewezen handelbare drempel)
MIN_GAP = float(os.getenv("MIN_GAP", "0.08"))

# Sports die Polymarket én bookmakers beiden aanbieden
SPORTS = [
    "soccer_spain_la_liga",
    "soccer_england_league1",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
    "tennis_atp_french_open",
]


@dataclass
class Opportunity:
    question:        str
    outcome:         str
    poly_price:      float
    book_price:      float
    gap:             float
    best_bookmaker:  str
    poly_volume:     float
    sport:           str

    def edge_label(self):
        if self.gap >= 0.15: return "STERK"
        if self.gap >= 0.08: return "GOED"
        return "ZWAK"


def fetch_bookmaker_odds(sport: str) -> list[dict]:
    """Haalt odds op voor een sport van 250+ bookmakers."""
    if not ODDS_API_KEY:
        raise ValueError("ODDS_API_KEY niet ingesteld in .env")

    r = requests.get(
        f"{ODDS_API_BASE}/sports/{sport}/odds",
        params={
            "apiKey":  ODDS_API_KEY,
            "regions": "eu",           # Europese bookmakers
            "markets": "h2h",          # Head-to-head (wedstrijdwinnaar)
            "oddsFormat": "decimal",
        },
        timeout=10,
    )

    if r.status_code == 401:
        raise ValueError("Ongeldige ODDS_API_KEY")
    if r.status_code == 422:
        return []  # Sport niet beschikbaar
    r.raise_for_status()
    return r.json()


def decimal_to_prob(decimal_odds: float) -> float:
    """Zet decimale odds om naar implied probability."""
    return round(1 / decimal_odds, 4)


def get_best_bookmaker_price(event: dict, team_name: str) -> tuple[float, str]:
    """
    Geeft de hoogste kans terug die een bookmaker geeft voor een team.
    Hoogste kans = bookmaker is meest bullish op dit team.
    """
    best_prob = 0.0
    best_book = ""

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                if team_name.lower() in outcome.get("name", "").lower():
                    prob = decimal_to_prob(outcome["price"])
                    if prob > best_prob:
                        best_prob = prob
                        best_book = bookmaker["title"]

    return best_prob, best_book


def fetch_polymarket_sports() -> list[dict]:
    """Haalt actieve sportmarkten op van Polymarket."""
    sport_keywords = [
        "win on", "vs.", "vs ", "beat", "champion",
        "nba", "mlb", "nhl", "la liga", "bundesliga",
        "serie a", "ligue", "premier league", "champions league"
    ]
    r = requests.get(
        GAMMA_API,
        params={"limit": 100, "order": "volume24hr", "ascending": "false", "active": "true"},
        timeout=10,
    )
    markets = r.json()

    return [
        m for m in markets
        if any(kw in m.get("question", "").lower() for kw in sport_keywords)
        and float(m.get("volume24hr") or 0) > 10_000
    ]


def match_market(poly_question: str, event: dict) -> str | None:
    """
    Probeert een Polymarket vraag te matchen met een bookmaker event.
    Alleen wedstrijd-specifieke markten (niet kampioenschap/seizoen).
    """
    q = poly_question.lower()

    # Sla seizoen/kampioenschap markten over — die matchen niet met dagelijkse wedstrijd odds
    skip_keywords = [
        "finals", "championship", "champion", "title", "season",
        "playoff", "super bowl", "stanley cup", "world series",
        "world cup", "copa", "golden glove", "mvp", "award",
        "draft", "sign", "trade", "transfer"
    ]
    if any(kw in q for kw in skip_keywords):
        return None

    # Moet een specifieke wedstrijd zijn (vandaag of morgen)
    match_keywords = ["win on 202", "vs.", " vs ", "beat "]
    if not any(kw in q for kw in match_keywords):
        return None

    home = event.get("home_team", "").lower()
    away = event.get("away_team", "").lower()

    home_parts = [p for p in home.split() if len(p) > 3]
    away_parts = [p for p in away.split() if len(p) > 3]

    # Beide teams moeten in de vraag voorkomen voor een betrouwbare match
    home_match = any(part in q for part in home_parts)
    away_match = any(part in q for part in away_parts)

    if not (home_match and away_match):
        return None

    # Welk team wint Polymarket YES voor?
    for part in home_parts:
        if part in q:
            # Check of de vraag over dit specifieke team gaat
            if f"will" in q:
                return event["home_team"]
    for part in away_parts:
        if part in q:
            if f"will" in q:
                return event["away_team"]

    return None


def scan() -> list[Opportunity]:
    """Hoofdscanner — vergelijkt Polymarket met bookmakers."""
    opportunities = []

    print("Polymarket sportmarkten ophalen...")
    poly_markets = fetch_polymarket_sports()
    print(f"  {len(poly_markets)} actieve sportmarkten gevonden")

    for sport in SPORTS:
        try:
            events = fetch_bookmaker_odds(sport)
        except ValueError as e:
            print(f"  Fout ({sport}): {e}")
            break
        except Exception as e:
            print(f"  Fout bij {sport}: {e}")
            continue

        for event in events:
            for poly in poly_markets:
                team = match_market(poly["question"], event)
                if not team:
                    continue

                # Polymarket prijs voor YES
                prices = poly.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    try: prices = json.loads(prices)
                    except: continue

                poly_price = float(prices[0]) if prices else 0
                if not (0.05 < poly_price < 0.95):
                    continue

                # Bookmaker prijs
                book_price, best_book = get_best_bookmaker_price(event, team)
                if book_price == 0:
                    continue

                gap = book_price - poly_price

                if gap >= MIN_GAP:
                    opportunities.append(Opportunity(
                        question=poly["question"],
                        outcome=team,
                        poly_price=poly_price,
                        book_price=book_price,
                        gap=gap,
                        best_bookmaker=best_book,
                        poly_volume=float(poly.get("volume24hr") or 0),
                        sport=sport,
                    ))

    return sorted(opportunities, key=lambda o: o.gap, reverse=True)


def display(opportunities: list[Opportunity]):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── Sports Arbitrage Scanner [{ts}] ──────────────────────────")

    if not opportunities:
        print("  Geen kansen gevonden (gap < {:.0f}%)".format(MIN_GAP * 100))
        print("  Tip: verlaag MIN_GAP in .env als je meer resultaten wil")
        return

    print(f"  {len(opportunities)} kans(en) gevonden:\n")

    for i, opp in enumerate(opportunities, 1):
        print(f"  [{opp.edge_label()}] {opp.question[:55]}")
        print(f"  {'':4} Team:      {opp.outcome}")
        print(f"  {'':4} Polymarket: {opp.poly_price*100:.0f}%")
        print(f"  {'':4} Bookmaker:  {opp.book_price*100:.0f}% ({opp.best_bookmaker})")
        print(f"  {'':4} GAP:        +{opp.gap*100:.1f}% ← koop YES op Polymarket")
        print(f"  {'':4} Volume:     ${opp.poly_volume:,.0f}/dag")
        print()


if __name__ == "__main__":
    if not ODDS_API_KEY:
        print("ODDS_API_KEY ontbreekt in .env")
        print("Haal een gratis key op via: the-odds-api.com")
        print("Zet dan in .env: ODDS_API_KEY=jouw_key")
        exit(1)

    print("── Polymarket Sports Arbitrage Scanner ──────────────────────")
    opps = scan()
    display(opps)
