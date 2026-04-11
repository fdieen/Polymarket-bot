"""
Hurricane Scanner — NHC + NOAA vs Polymarket.

Data bronnen:
  1. NHC CurrentStorms.json — actieve stormen, positie, categorie, track
  2. NHC Forecast Advisory — 5-daagse track en intensiteit
  3. NOAA Seasonal Outlook (CPC) — seizoensverwachting
  4. Historische klimatologie — basiskanssen per maand

Markt types:
  - Seizoen: "Will there be X+ named storms in 2026?"
  - Cat5 landfall: "Will a Category 5 hurricane make landfall?"
  - Maand: "Will a hurricane make landfall in August?"
  - Storm-specifiek: "Will [naam] make landfall in [regio]?"

Run: venv/bin/python hurricane_scanner.py
"""
import re
import math
import json
import requests
from dataclasses import dataclass
from datetime import datetime, timezone

NHC_CURRENT   = "https://www.nhc.noaa.gov/CurrentStorms.json"
GAMMA_API     = "https://gamma-api.polymarket.com/markets"
NHC_TEXT_BASE = "https://www.nhc.noaa.gov/text/"

MIN_GAP = 0.10  # 10% minimaal

# Saffir-Simpson schaal (knots → categorie)
def wind_to_category(knots: int) -> int:
    if knots >= 137: return 5
    if knots >= 113: return 4
    if knots >= 96:  return 3
    if knots >= 83:  return 2
    if knots >= 64:  return 1
    return 0  # TD of TS

# Historische maandkansen voor hurricane landfall US (1851-2023)
# Bron: NOAA HURDAT2 statistieken
MONTHLY_LANDFALL_PROB = {
    1: 0.005, 2: 0.005, 3: 0.005, 4: 0.005, 5: 0.01,
    6: 0.04,  7: 0.07,  8: 0.25,  9: 0.40,  10: 0.20,
    11: 0.07, 12: 0.01,
}

# Historische kansen op minimaal N named storms in een Atlantisch seizoen
# Gebaseerd op 1991-2020 baseline (gemiddeld 14.4 named storms)
NAMED_STORM_PROBS = {
    # "X of meer" kansen op basis van historische verdeling
    5:  0.97,
    8:  0.85,
    10: 0.72,
    12: 0.58,
    14: 0.45,
    16: 0.32,
    18: 0.20,
    20: 0.11,
    22: 0.06,
    25: 0.02,
}

# NOAA 2026 seizoensverwachting (bij update: haal op van CPC website)
# NOAA publiceert elk jaar in mei — vul hier de outlook in zodra beschikbaar
NOAA_2026_OUTLOOK = {
    "named_storms_low": 17,
    "named_storms_high": 25,
    "hurricanes_low": 9,
    "hurricanes_high": 13,
    "major_low": 4,
    "major_high": 7,
    "probability_above_normal": 0.75,  # 75% kans op boven-normaal seizoen
    "source": "NOAA CPC (update verwacht mei 2026)",
}


@dataclass
class HurricaneOpportunity:
    question:    str
    market_type: str   # "seasonal", "monthly", "active_storm"
    poly_price:  float
    model_prob:  float
    gap:         float
    direction:   str
    volume:      float
    liquidity:   float
    basis:       str   # uitleg waarom model_prob dit zegt
    storm_info:  str   # storm naam / seizoensdata

    def label(self):
        if abs(self.gap) >= 0.25: return "STERK"
        if abs(self.gap) >= 0.12: return "GOED"
        return "ZWAK"


# ── NHC data ─────────────────────────────────────────────────────────────────

def fetch_active_storms() -> list[dict]:
    """Haalt actieve tropische stormen op van NHC (geen key nodig)."""
    try:
        r = requests.get(NHC_CURRENT, timeout=8)
        r.raise_for_status()
        data = r.json()
        return data.get("activeStorms", [])
    except Exception:
        return []


def parse_nhc_advisory(storm: dict) -> dict | None:
    """
    Haalt de 5-daagse track op uit het NHC forecast advisory.
    Geeft lijst van forecast posities met lat/lon/wind.
    """
    forecast = storm.get("forecastAdvisory", {})
    url = forecast.get("fullPath") or forecast.get("url")
    if not url:
        return None

    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None
        text = r.text

        # Parse forecast positions: "12H  14/0600Z 27.5N  78.2W   80 KT"
        positions = []
        for match in re.finditer(
            r'(\d+)H\s+\d+/\d+Z\s+([\d.]+)([NS])\s+([\d.]+)([EW])\s+(\d+)\s+KT',
            text
        ):
            hours = int(match.group(1))
            lat   = float(match.group(2)) * (1 if match.group(3) == 'N' else -1)
            lon   = float(match.group(4)) * (-1 if match.group(5) == 'W' else 1)
            wind  = int(match.group(6))
            positions.append({"hours": hours, "lat": lat, "lon": lon, "wind_kts": wind})

        return {"positions": positions} if positions else None
    except Exception:
        return None


def storm_hits_us(positions: list[dict]) -> tuple[float, str]:
    """
    Schat de kans dat een storm de VS raakt op basis van forecast track.
    Returns (probability, uitleg).
    US kustlijn bounding boxes (ruim).
    """
    # VS kustgebieden: Gulf Coast, East Coast, Florida
    us_zones = [
        {"name": "Florida",    "lat": (24.5, 31.2), "lon": (-87.6, -79.8)},
        {"name": "Gulf Coast", "lat": (25.8, 31.0), "lon": (-97.5, -84.5)},
        {"name": "East Coast", "lat": (25.0, 45.0), "lon": (-81.5, -66.0)},
    ]

    if not positions:
        return 0.2, "geen track data"

    # Check elke forecast positie
    min_dist_degrees = 999
    closest_zone = ""
    within_zone = False

    for pos in positions:
        for zone in us_zones:
            lat_in = zone["lat"][0] <= pos["lat"] <= zone["lat"][1]
            lon_in = zone["lon"][0] <= pos["lon"] <= zone["lon"][1]
            if lat_in and lon_in:
                within_zone = True
                closest_zone = zone["name"]
                break

            # Afstand tot rand van zone
            dlat = max(0, zone["lat"][0] - pos["lat"], pos["lat"] - zone["lat"][1])
            dlon = max(0, zone["lon"][0] - pos["lon"], pos["lon"] - zone["lon"][1])
            dist = math.sqrt(dlat**2 + dlon**2)
            if dist < min_dist_degrees:
                min_dist_degrees = dist
                closest_zone = zone["name"]

    if within_zone:
        # Storm track gaat door VS-gebied
        # Onzekerheid groeit met tijd: ±200km per dag
        lead_hours = next((p["hours"] for p in positions if within_zone), 48)
        confidence = max(0.5, 1.0 - (lead_hours / 120) * 0.4)
        return round(confidence, 2), f"track gaat door {closest_zone} (t+{lead_hours}h)"

    # Niet direct in zone maar check nabijheid
    if min_dist_degrees < 3:  # ~300km
        prob = max(0.15, 0.6 - min_dist_degrees * 0.1)
        return round(prob, 2), f"track passeert {min_dist_degrees:.1f}° bij {closest_zone}"

    return 0.05, f"track ver van VS ({min_dist_degrees:.1f}° afstand)"


def storm_makes_category(positions: list[dict], min_cat: int) -> tuple[float, str]:
    """
    Kans dat een storm minimaal categorie min_cat bereikt aan land.
    Gebaseerd op forecast intensiteit bij landfall-punt.
    """
    if not positions:
        return 0.3, "geen intensiteitsdata"

    max_wind = max((p["wind_kts"] for p in positions), default=0)
    current_cat = wind_to_category(max_wind)

    # Basiskans: als storm al in categorie of hoger → hoog
    if current_cat >= min_cat:
        prob = 0.65  # kan verzwakken voor landfall
        return prob, f"huidig max {max_wind}kt (cat {current_cat}), {prob*100:.0f}% kans cat{min_cat}+"
    else:
        deficit = (min_cat - current_cat)
        prob = max(0.05, 0.35 - deficit * 0.1)
        return prob, f"huidig cat {current_cat}, moet {deficit} categorie(ën) versterken"


# ── Seizoensanalyse ──────────────────────────────────────────────────────────

def seasonal_probability(question: str) -> tuple[float, str] | None:
    """
    Berekent modelkans voor seizoensmarkten.
    Bijv: "Will there be 18+ named storms in 2026?"
    """
    q = question.lower()

    # Named storms threshold
    m = re.search(r'(\d+)\+?\s+(?:or more\s+)?named storms?\s+in\s+(\d{4})', q)
    if m:
        threshold = int(m.group(1))
        year = int(m.group(2))

        # Interpoleer uit historische tabel
        thresholds = sorted(NAMED_STORM_PROBS.keys())
        if threshold <= thresholds[0]:
            prob = NAMED_STORM_PROBS[thresholds[0]]
        elif threshold >= thresholds[-1]:
            prob = NAMED_STORM_PROBS[thresholds[-1]]
        else:
            # Lineaire interpolatie
            for i, t in enumerate(thresholds[:-1]):
                if t <= threshold <= thresholds[i+1]:
                    p1 = NAMED_STORM_PROBS[t]
                    p2 = NAMED_STORM_PROBS[thresholds[i+1]]
                    frac = (threshold - t) / (thresholds[i+1] - t)
                    prob = p1 + frac * (p2 - p1)
                    break
            else:
                prob = 0.3

        # Aanpassing voor NOAA outlook
        outlook = NOAA_2026_OUTLOOK
        if year == 2026:
            mid_forecast = (outlook["named_storms_low"] + outlook["named_storms_high"]) / 2
            if threshold > mid_forecast:
                prob *= 0.85  # conservatiever
            elif threshold < mid_forecast:
                prob = min(0.97, prob * 1.10)

        basis = f"historisch: {prob*100:.0f}% kans op {threshold}+ named storms | NOAA outlook {outlook['named_storms_low']}-{outlook['named_storms_high']} (2026)"
        return round(prob, 3), basis

    # "Will there be X+ hurricanes?"
    m = re.search(r'(\d+)\+?\s+(?:or more\s+)?hurricanes?\s+in\s+(\d{4})', q)
    if m:
        threshold = int(m.group(1))
        # Atlantic gemiddeld 7 hurricanes/seizoen
        import statistics
        prob = max(0.02, min(0.97, 1 - (threshold - 7) * 0.12 + 0.5))
        basis = f"Atlantisch gemiddeld 7 hurricanes/seizoen | NOAA 2026: {NOAA_2026_OUTLOOK['hurricanes_low']}-{NOAA_2026_OUTLOOK['hurricanes_high']}"
        return round(prob, 3), basis

    # "Will there be a Category 5 hurricane?"
    if "category 5" in q or "cat 5" in q:
        # Historisch: cat5 in Atlantisch bekken ~37% van de jaren (1950-2024)
        if "landfall" in q and ("lower 48" in q or "united states" in q or " us " in q):
            prob = 0.08  # cat5 maakt zelden US landfall (~8% per jaar)
            basis = "historisch: ~8% kans cat5 US-landfall per seizoen (1950-2024)"
        elif "landfall" in q:
            prob = 0.15  # cat5 ergens aan land
            basis = "historisch: ~15% kans cat5 landfall ergens per seizoen"
        else:
            prob = 0.37  # cat5 vormt zich ergens
            basis = "historisch: ~37% kans op cat5 storm per seizoen"
        return round(prob, 3), basis

    # Maandelijkse kansen: "Will a hurricane make landfall in [month]?"
    months = ["january","february","march","april","may","june",
              "july","august","september","october","november","december"]
    for i, month in enumerate(months):
        if month in q and ("landfall" in q or "make landfall" in q):
            prob = MONTHLY_LANDFALL_PROB.get(i+1, 0.05)
            basis = f"historisch gemiddelde: {prob*100:.0f}% kans op hurricane landfall in {month.capitalize()}"
            return round(prob, 3), basis

    return None


# ── Polymarket fetch ─────────────────────────────────────────────────────────

def fetch_hurricane_markets() -> list[dict]:
    """Haalt hurricane/storm markten op — alleen eerste 500 (top op liquiditeit)."""
    keywords = [
        "hurricane", "tropical storm", "named storm", "cyclone",
        "category 5", "category 4", "landfall", "typhoon",
        "atlantic season", "hurricane season"
    ]
    try:
        r = requests.get(
            GAMMA_API,
            params={"limit": 500, "active": "true",
                    "order": "liquidity", "ascending": "false"},
            timeout=10,
        )
        r.raise_for_status()
        results = []
        for m in r.json():
            q = m.get("question", "").lower()
            if any(k in q for k in keywords):
                if "nhl" in q or "nba" in q or "carolina" in q:
                    continue
                results.append(m)
        return results
    except Exception:
        return []


# ── Hoofdscanner ─────────────────────────────────────────────────────────────

def scan() -> list[HurricaneOpportunity]:
    """Combineert NHC-data + historische stats met Polymarket-markten."""
    opportunities = []

    print("NHC actieve stormen ophalen...")
    storms = fetch_active_storms()
    print(f"  {len(storms)} actieve storm(en)" if storms else "  Geen actieve stormen (buiten seizoen)")

    # Bouw storm-lookup: naam → track + data
    storm_data = {}
    for storm in storms:
        name = storm.get("name", "").lower()
        wind_kts = int(storm.get("intensity") or 0)
        cat = wind_to_category(wind_kts)
        print(f"  → {name.upper()} | Cat{cat} | {wind_kts}kt | {storm.get('headline', '')[:60]}")
        track = parse_nhc_advisory(storm)
        storm_data[name] = {
            "wind_kts": wind_kts,
            "category": cat,
            "lat": storm.get("latitudeNumeric"),
            "lon": storm.get("longitudeNumeric"),
            "movement_dir": storm.get("movementDir"),
            "movement_speed": storm.get("movementSpeed"),
            "headline": storm.get("headline", ""),
            "track": track,
        }

    print("\nPolymarket hurricane-markten ophalen...")
    markets = fetch_hurricane_markets()
    print(f"  {len(markets)} hurricane-markten gevonden")

    for market in markets:
        q = market.get("question", "")
        q_lower = q.lower()

        prices = market.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: continue
        poly_price = float(prices[0]) if prices else 0
        if not (0.01 < poly_price < 0.99):
            continue

        liq = float(market.get("liquidity") or 0)
        vol = float(market.get("volume24hr") or 0)

        model_prob = None
        basis = ""
        market_type = "unknown"
        storm_info = ""

        # 1. Seizoens- of maandmarkt
        result = seasonal_probability(q)
        if result:
            model_prob, basis = result
            market_type = "seasonal"
            storm_info = "seizoensanalyse"

        # 2. Storm-specifieke markt
        if model_prob is None:
            for storm_name, data in storm_data.items():
                if storm_name in q_lower:
                    track_positions = data["track"]["positions"] if data["track"] else []
                    cat = data["category"]
                    wind = data["wind_kts"]

                    if "category 5" in q_lower or "cat 5" in q_lower or "category 4" in q_lower:
                        min_cat = 5 if "category 5" in q_lower else 4
                        model_prob, basis = storm_makes_category(track_positions, min_cat)
                    elif "landfall" in q_lower:
                        model_prob, basis = storm_hits_us(track_positions)
                    else:
                        model_prob = 0.5
                        basis = "geen specifieke conditie herkend"

                    market_type = "active_storm"
                    storm_info = f"{storm_name.upper()} Cat{cat} {wind}kt"
                    break

        if model_prob is None:
            continue

        gap = model_prob - poly_price
        if abs(gap) >= MIN_GAP:
            opportunities.append(HurricaneOpportunity(
                question=q,
                market_type=market_type,
                poly_price=poly_price,
                model_prob=round(model_prob, 3),
                gap=round(gap, 3),
                direction="BUY YES" if gap > 0 else "BUY NO",
                volume=vol,
                liquidity=liq,
                basis=basis,
                storm_info=storm_info,
            ))

    return sorted(opportunities, key=lambda o: abs(o.gap), reverse=True)


def display(opportunities: list[HurricaneOpportunity]):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── Hurricane Scanner [{ts}] ──────────────────────────────")

    if not opportunities:
        print("  Geen kansen gevonden")
        print("  (Hurricane seizoen: juni t/m november)")
        return

    print(f"  {len(opportunities)} kans(en) gevonden:\n")
    for opp in opportunities:
        sign = "+" if opp.gap > 0 else ""
        print(f"  [{opp.label()}] {opp.question[:65]}")
        print(f"  {'':4} Type:       {opp.market_type} | {opp.storm_info}")
        print(f"  {'':4} Basis:      {opp.basis[:80]}")
        print(f"  {'':4} Polymarket: {opp.poly_price*100:.1f}% YES")
        print(f"  {'':4} Model:      {opp.model_prob*100:.1f}% YES")
        print(f"  {'':4} GAP:        {sign}{opp.gap*100:.1f}% → {opp.direction}")
        print(f"  {'':4} Liquiditeit: ${opp.liquidity:,.0f}")
        print()


if __name__ == "__main__":
    print("── Polymarket Hurricane Scanner ─────────────────────────")
    opps = scan()
    display(opps)
