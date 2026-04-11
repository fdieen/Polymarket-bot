"""
F1 Weather Scanner — Polymarket vs Regenvoorspelling.

Haalt het F1-racekalender op, checkt het weer voor elk circuit,
en vergelijkt Polymarket-odds met historische nat-prestaties per coureur.

Logica:
  - Als regen > 50% verwacht: rijders die historisch goed presteren in nat
    zijn vaak ondergeprijsd op Polymarket (markt prijst droog scenario)
  - Rijders die slecht presteren in nat zijn vaak overgeprijsd

Run: venv/bin/python f1_weather.py
"""
import json
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

JOLPICA_API  = "https://api.jolpi.ca/ergast/f1"
OPENMETEO    = "https://api.open-meteo.com/v1/forecast"
GAMMA_API    = "https://gamma-api.polymarket.com/markets"

# Hoeveel dagen vooruit kijken voor aankomende races
LOOKAHEAD_DAYS = 35

# Regen drempel: boven dit % is regen 'significant'
RAIN_THRESHOLD = 40

# Historische natte performance van coureurs (aanpassing t.o.v. droog, in %-punt)
# + = presteert beter in nat, - = slechter in nat
# Gebaseerd op historische resultaten 2010-2024
WET_DELTA = {
    "hamilton":      +14,   # Britse regen-meester
    "alonso":        +10,   # ervaren, precieze rijstijl
    "norris":        +9,    # bewezen in nat (2021 Rusland, 2023 Brazilië)
    "verstappen":    +5,    # snel in alle omstandigheden
    "russell":       +4,    # kalm en consistent in nat
    "sainz":         +2,    # solid in nat
    "leclerc":       -4,    # inconsistent in nat, te agressief
    "piastri":       -2,    # weinig ervaring in nat
    "perez":         -9,    # aantoonbaar zwak in nat
    "stroll":        -5,    # crash-gevoelig in nat
}


@dataclass
class F1Opportunity:
    race_name:    str
    circuit:      str
    race_date:    str
    rain_pct:     int
    rain_mm:      float
    driver:       str
    poly_price:   float
    wet_delta:    int
    adj_price:    float
    gap:          float
    direction:    str   # "BUY" of "SELL"
    question:     str

    def label(self):
        if abs(self.gap) >= 15: return "STERK"
        if abs(self.gap) >= 8:  return "GOED"
        return "ZWAK"


def fetch_upcoming_races() -> list[dict]:
    """Haalt aankomende F1-races op voor de komende LOOKAHEAD_DAYS dagen."""
    year = datetime.now().year
    r = requests.get(f"{JOLPICA_API}/{year}/races.json", timeout=10)
    if r.status_code != 200:
        # Probeer vorig jaar als huidig jaar nog niet beschikbaar is
        r = requests.get(f"{JOLPICA_API}/{year-1}/races.json", timeout=10)
        r.raise_for_status()

    races = r.json()["MRData"]["RaceTable"]["Races"]
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=LOOKAHEAD_DAYS)

    upcoming = []
    for race in races:
        race_date = datetime.strptime(race["date"], "%Y-%m-%d").date()
        if today <= race_date <= cutoff:
            upcoming.append({
                "name":    race["raceName"],
                "circuit": race["Circuit"]["circuitName"],
                "date":    race["date"],
                "lat":     float(race["Circuit"]["Location"]["lat"]),
                "lon":     float(race["Circuit"]["Location"]["long"]),
                "time":    race.get("time", "13:00:00Z"),
            })

    return upcoming


def fetch_rain_forecast(lat: float, lon: float, date: str) -> tuple[int, float, bool]:
    """
    Geeft (max_regenpercentage, totale_neerslag_mm, weer_beschikbaar) voor een datum.
    Open-Meteo heeft maximaal 16 dagen vooruit.
    """
    r = requests.get(
        OPENMETEO,
        params={
            "latitude":  lat,
            "longitude": lon,
            "daily":     "precipitation_probability_max,precipitation_sum",
            "forecast_days": 16,
            "timezone":  "auto",
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()["daily"]

    for i, d in enumerate(data["time"]):
        if d == date:
            rain_pct = data["precipitation_probability_max"][i] or 0
            rain_mm  = data["precipitation_sum"][i] or 0.0
            return int(rain_pct), float(rain_mm), True

    return 0, 0.0, False  # datum buiten voorspellingsvenster


def fetch_f1_polymarkets() -> list[dict]:
    """Haalt Polymarket F1-markten op (rijder wint race)."""
    keywords = ["f1", "formula 1", "formula one", "grand prix", "verstappen",
                "hamilton", "norris", "leclerc", "russell", "alonso",
                "sainz", "piastri", "perez"]

    r = requests.get(
        GAMMA_API,
        params={"limit": 100, "order": "volume24hr", "ascending": "false", "active": "true"},
        timeout=10,
    )
    markets = r.json()

    return [
        m for m in markets
        if any(kw in m.get("question", "").lower() for kw in keywords)
        and float(m.get("volume24hr") or 0) > 1_000
    ]


def match_driver(question: str) -> str | None:
    """Detecteert welke coureur een Polymarket vraag over gaat."""
    q = question.lower()
    for driver in WET_DELTA:
        if driver in q:
            return driver
    return None


def scan() -> list[F1Opportunity]:
    """Hoofdscanner: combineert F1 kalender, weer en Polymarket odds."""
    opportunities = []

    print("F1 kalender ophalen...")
    races = fetch_upcoming_races()
    if not races:
        print("  Geen aankomende races in de komende", LOOKAHEAD_DAYS, "dagen")
        return []
    print(f"  {len(races)} aankomende race(s) gevonden")

    print("Polymarket F1-markten ophalen...")
    poly_markets = fetch_f1_polymarkets()
    print(f"  {len(poly_markets)} F1-markten gevonden")

    for race in races:
        print(f"\nWeer ophalen voor {race['name']} ({race['date']})...")
        rain_pct, rain_mm, weer_ok = fetch_rain_forecast(race["lat"], race["lon"], race["date"])
        if not weer_ok:
            print(f"  Weer nog niet beschikbaar (race > 16 dagen weg)")
            continue
        print(f"  Regenvoorspelling: {rain_pct}% kans, {rain_mm}mm neerslag")

        for market in poly_markets:
            q = market.get("question", "")

            # Check of deze markt over deze race gaat
            race_keywords = [
                word.lower() for word in race["name"].split()
                if len(word) > 3 and word.lower() not in ("grand", "prix")
            ]
            if not any(kw in q.lower() for kw in race_keywords):
                continue

            driver = match_driver(q)
            if not driver:
                continue

            # Polymarket prijs ophalen
            prices = market.get("outcomePrices", "[]")
            if isinstance(prices, str):
                try: prices = json.loads(prices)
                except: continue

            poly_price = float(prices[0]) if prices else 0
            if not (0.02 < poly_price < 0.98):
                continue

            delta = WET_DELTA.get(driver, 0)

            # Gecorrigeerde prijs op basis van regen impact
            # Schaal de aanpassing met regenintensiteit (0-100%)
            rain_factor = rain_pct / 100
            adj_delta   = delta * rain_factor
            adj_price   = max(0.02, min(0.98, poly_price + adj_delta / 100))

            gap = (adj_price - poly_price) * 100  # in %-punt

            # Alleen signaleren als:
            # 1. Er significant regen verwacht wordt
            # 2. Er een betekenisvolle aanpassing is (>= 5%)
            if rain_pct >= RAIN_THRESHOLD and abs(gap) >= 5:
                direction = "BUY YES" if gap > 0 else "BUY NO"
                opportunities.append(F1Opportunity(
                    race_name=race["name"],
                    circuit=race["circuit"],
                    race_date=race["date"],
                    rain_pct=rain_pct,
                    rain_mm=rain_mm,
                    driver=driver.capitalize(),
                    poly_price=poly_price,
                    wet_delta=delta,
                    adj_price=round(adj_price, 3),
                    gap=round(gap, 1),
                    direction=direction,
                    question=q,
                ))

    return sorted(opportunities, key=lambda o: abs(o.gap), reverse=True)


def display(opportunities: list[F1Opportunity]):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── F1 Weather Scanner [{ts}] ─────────────────────────────")

    if not opportunities:
        print("  Geen kansen gevonden")
        print("  (Geen regen verwacht OF geen Polymarket F1-markten actief)")
        return

    print(f"  {len(opportunities)} kans(en) gevonden:\n")

    for opp in opportunities:
        sign   = "+" if opp.gap > 0 else ""
        detail = "beter in nat" if opp.wet_delta > 0 else "slechter in nat"
        print(f"  [{opp.label()}] {opp.question[:60]}")
        print(f"  {'':4} Race:        {opp.race_name} — {opp.race_date}")
        print(f"  {'':4} Regen:       {opp.rain_pct}% kans, {opp.rain_mm}mm")
        print(f"  {'':4} Coureur:     {opp.driver} ({detail}, wet delta: {opp.wet_delta:+d}%)")
        print(f"  {'':4} Polymarket:  {opp.poly_price*100:.0f}%")
        print(f"  {'':4} Regen-adj:   {opp.adj_price*100:.0f}% ({sign}{opp.gap:.1f}%)")
        print(f"  {'':4} Actie:       {opp.direction}")
        print()


if __name__ == "__main__":
    print("── F1 Weather Arbitrage Scanner ─────────────────────────")
    opps = scan()
    display(opps)
