"""
Weather Temperature Scanner — Polymarket vs Weermodellen.

Haalt temperatuurmarkten op van Polymarket en vergelijkt ze met
nauwkeurige weervoorspellingen (Open-Meteo, zelfde model als KNMI/Buienradar).

Markten zoals:
  "Will the highest temperature in Chicago be between 50-51°F on April 5?"
  "Will the highest temperature in Seoul be 15°C or higher on April 7?"

Run: venv/bin/python weather_scanner.py
"""
import json
import re
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

GAMMA_API  = "https://gamma-api.polymarket.com/markets"
OPENMETEO  = "https://api.open-meteo.com/v1/forecast"

MIN_GAP    = 0.40   # minimaal 40%-punt verschil — backtest: < 40% geeft < 47% accuracy (verlies)
MIN_VOLUME = 50.0   # minimaal $50/dag volume — illiquide markten hebben slechte spreads

# Steden → coördinaten
CITIES = {
    "amsterdam":     (52.374,  4.890),
    "ankara":        (39.920, 32.854),
    "athens":        (37.983, 23.728),
    "austin":        (30.267, -97.743),
    "bangkok":       (13.756, 100.502),
    "barcelona":     (41.386,  2.170),
    "beijing":       (39.929, 116.388),
    "berlin":        (52.517, 13.388),
    "buenos aires":  (-34.603,-58.382),
    "cairo":         (30.044, 31.236),
    "chengdu":       (30.659, 104.065),
    "chicago":       (41.881, -87.628),
    "dallas":        (32.783, -96.800),
    "denver":        (39.739,-104.984),
    "dubai":         (25.204, 55.270),
    "helsinki":      (60.169, 24.935),
    "hong kong":     (22.320, 114.170),
    "istanbul":      (41.015, 28.980),
    "jakarta":       (-6.211, 106.845),
    "johannesburg":  (-26.195, 28.034),
    "karachi":       (24.861, 67.010),
    "kuala lumpur":  (3.140,  101.687),
    "lagos":         (6.455,   3.384),
    "lima":          (-12.046,-77.043),
    "london":        (51.509,  -0.118),
    "los angeles":   (34.052,-118.244),
    "lucknow":       (26.847,  80.947),
    "madrid":        (40.416,  -3.703),
    "miami":         (25.774, -80.194),
    "milan":         (45.464,   9.190),
    "montreal":      (45.508, -73.554),
    "moscow":        (55.751,  37.616),
    "mumbai":        (19.076,  72.878),
    "munich":        (48.137,  11.575),
    "nairobi":       (-1.286,  36.817),
    "new york":      (40.713, -74.006),
    "oslo":          (59.913,  10.752),
    "panama city":   (8.994,  -79.519),
    "paris":         (48.853,   2.350),
    "rome":          (41.902,  12.496),
    "san francisco": (37.774,-122.419),
    "santiago":      (-33.457, -70.648),
    "sao paulo":     (-23.550, -46.633),
    "seoul":         (37.566, 126.978),
    "shanghai":      (31.228, 121.474),
    "singapore":     (1.352,  103.820),
    "stockholm":     (59.329,  18.069),
    "sydney":        (-33.868, 151.209),
    "taipei":        (25.047, 121.517),
    "tehran":        (35.694,  51.421),
    "tokyo":         (35.689, 139.692),
    "toronto":       (43.653, -79.383),
    "vienna":        (48.208,  16.373),
    "warsaw":        (52.229,  21.012),
    "zurich":        (47.377,   8.540),
    "mexico city":   (19.436, -99.072),
    "shenzhen":      (22.640, 113.811),
    "tel aviv":      (32.009,  34.887),
    "wellington":    (-41.327, 174.805),
}


@dataclass
class WeatherOpportunity:
    question:     str
    city:         str
    date:         str
    condition:    str      # "above", "below", "between", "exact"
    temp_low:     float
    temp_high:    float
    unit:         str      # "C" of "F"
    poly_price:   float    # Polymarket YES kans
    forecast_temp: float   # voorspelde max temperatuur
    model_prob:   float    # onze kans dat YES klopt
    gap:          float    # model_prob - poly_price
    direction:    str      # "BUY YES" of "BUY NO"
    volume:       float
    condition_id:      str   = ""
    slug:              str   = ""
    market_id:         str   = ""
    model_shift:       float = 0.0
    consistency_bonus: float = 0.0

    def label(self):
        if abs(self.gap) >= 0.30: return "STERK"
        if abs(self.gap) >= 0.15: return "GOED"
        return "ZWAK"


def parse_temperature_question(question: str) -> dict | None:
    """
    Parst een Polymarket temperatuurvraag.
    Ondersteunde formaten:
      - "... be 18°C on April 4"
      - "... be 15°C or higher on April 7"
      - "... be 63°F or below on April 6"
      - "... be between 50-51°F on April 5"
      - "... be 32°C or higher on April 5"
    """
    q = question.lower()

    if "highest temperature in" not in q:
        return None

    # Stad
    city = None
    for c in sorted(CITIES.keys(), key=len, reverse=True):
        if c in q:
            city = c
            break
    if not city:
        return None

    # Datum
    date_match = re.search(
        r'on (january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:,?\s+(\d{4}))?',
        q
    )
    if not date_match:
        return None

    month_str = date_match.group(1)
    day       = int(date_match.group(2))
    year      = int(date_match.group(3)) if date_match.group(3) else datetime.now().year
    months    = ["january","february","march","april","may","june",
                 "july","august","september","october","november","december"]
    month_num = months.index(month_str) + 1
    try:
        date = datetime(year, month_num, day).strftime("%Y-%m-%d")
    except ValueError:
        return None

    # Temperatuur + eenheid + conditie
    unit = "F" if "°f" in q or "f on" in q or "°f or" in q else "C"

    # Between X-Y
    between = re.search(r'between\s+([\d.]+)[–\-]([\d.]+)', q)
    if between:
        return {"city": city, "date": date, "unit": unit,
                "condition": "between",
                "temp_low": float(between.group(1)),
                "temp_high": float(between.group(2))}

    # Exact (bijv. "be 18°c on")
    exact = re.search(r'be ([\d.]+)(?:°[cf])? on', q)
    if exact and "or higher" not in q and "or below" not in q and "or lower" not in q:
        t = float(exact.group(1))
        return {"city": city, "date": date, "unit": unit,
                "condition": "exact", "temp_low": t - 0.5, "temp_high": t + 0.5}

    # Above / higher
    above = re.search(r'([\d.]+)(?:°[cf])?\s+or\s+(?:higher|above)', q)
    if above:
        return {"city": city, "date": date, "unit": unit,
                "condition": "above", "temp_low": float(above.group(1)), "temp_high": 999}

    # Below / lower
    below = re.search(r'([\d.]+)(?:°[cf])?\s+or\s+(?:below|lower)', q)
    if below:
        return {"city": city, "date": date, "unit": unit,
                "condition": "below", "temp_low": -999, "temp_high": float(below.group(1))}

    return None


def fetch_forecast_full(city: str, date: str) -> dict | None:
    """
    Haalt alle relevante weerdata op voor een stad op een datum.
    Returns dict met temp_max, temp_min, precip_mm, precip_pct, wind_max, weathercode.
    """
    coords = CITIES.get(city)
    if not coords:
        return None

    r = requests.get(
        OPENMETEO,
        params={
            "latitude":      coords[0],
            "longitude":     coords[1],
            "daily": ",".join([
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "precipitation_probability_max",
                "windspeed_10m_max",
                "weathercode",
            ]),
            "forecast_days": 16,
            "timezone":      "auto",
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()["daily"]

    for i, d in enumerate(data["time"]):
        if d == date:
            return {
                "temp_max":    data["temperature_2m_max"][i],
                "temp_min":    data["temperature_2m_min"][i],
                "precip_mm":   data["precipitation_sum"][i] or 0.0,
                "precip_pct":  data["precipitation_probability_max"][i] or 0,
                "wind_max":    data["windspeed_10m_max"][i] or 0.0,
                "weathercode": data["weathercode"][i] or 0,
            }
    return None


def fetch_forecast_temp(city: str, date: str) -> float | None:
    """Backwards-compat wrapper."""
    result = fetch_forecast_full(city, date)
    return result["temp_max"] if result else None


def fetch_all_city_temps(date: str | None = None) -> dict:
    """
    Haalt huidige max-temperatuur (vandaag) op voor ALLE steden.
    Gebruikt voor de heatmap. Returns {city: temp_c}.
    """
    from datetime import datetime, timezone
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    results = {}
    for city, coords in CITIES.items():
        try:
            r = requests.get(
                OPENMETEO,
                params={
                    "latitude":      coords[0],
                    "longitude":     coords[1],
                    "daily":         "temperature_2m_max,precipitation_probability_max",
                    "forecast_days": 3,
                    "timezone":      "auto",
                },
                timeout=8,
            )
            if r.status_code != 200:
                continue
            data = r.json()["daily"]
            for i, d in enumerate(data["time"]):
                if d == date:
                    results[city] = {
                        "temp_c":    data["temperature_2m_max"][i],
                        "rain_pct":  data["precipitation_probability_max"][i] or 0,
                        "lat":       coords[0],
                        "lon":       coords[1],
                    }
                    break
        except Exception:
            continue
    return results


def to_celsius(temp: float, unit: str) -> float:
    if unit == "F":
        return (temp - 32) * 5 / 9
    return temp


def model_probability(forecast_c: float, parsed: dict, spread: float = 2.0, days_ahead: int = 4) -> float:
    """
    Berekent de kans dat YES klopt op basis van weermodel.

    Sigma (onzekerheid) is dynamisch:
      - Basiswaarde 1.8°C
      - +0.25 per dag vooruit (onzekerheid neemt toe)
      - +0.5 * model_spread (meer modelverdeeldheid = meer onzekerheid)
      - Minimum 1.0°C, maximum 4.0°C
    """
    import math

    sigma = max(1.0, min(4.0, 1.8 + 0.15 * days_ahead + 0.5 * spread))

    low_c  = to_celsius(parsed["temp_low"],  parsed["unit"])
    high_c = to_celsius(parsed["temp_high"], parsed["unit"])

    def normal_cdf(x):
        return 0.5 * (1 + math.erf((x - forecast_c) / (sigma * math.sqrt(2))))

    cond = parsed["condition"]
    if cond == "above":
        return 1 - normal_cdf(low_c)
    elif cond == "below":
        return normal_cdf(high_c)
    elif cond in ("between", "exact"):
        return normal_cdf(high_c) - normal_cdf(low_c)
    return 0.5


def fetch_temperature_markets() -> list[dict]:
    """Haalt actieve temperatuurmarkten op van Polymarket.
    Scant max 10 pagina's (5000 markten) parallel voor snelheid."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_page(offset):
        try:
            r = requests.get(
                GAMMA_API,
                params={"limit": 500, "offset": offset, "order": "liquidity",
                        "ascending": "false", "active": "true"},
                timeout=12,
            )
            batch = r.json()
            return [
                m for m in batch
                if "highest temperature in" in m.get("question", "").lower()
                and float(m.get("liquidity") or 0) > 0
            ]
        except Exception:
            return []

    results = []
    offsets = list(range(0, 5000, 500))  # 10 pagina's parallel
    with ThreadPoolExecutor(max_workers=5) as exe:
        futures = {exe.submit(fetch_page, off): off for off in offsets}
        for future in as_completed(futures):
            results.extend(future.result())

    # Dedupliceer op conditionId
    seen = set()
    unique = []
    for m in results:
        cid = m.get("conditionId", m.get("question", ""))
        if cid not in seen:
            seen.add(cid)
            unique.append(m)
    return unique


def scan() -> list[WeatherOpportunity]:
    """Hoofdscanner: vergelijkt weermodellen met Polymarket temperatuurmarkten.
    Gebruikt ThreadPoolExecutor voor parallelle weeropvragen (veel sneller)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from weather_sources import multi_source_forecast

    print("Polymarket temperatuurmarkten ophalen...")
    markets = fetch_temperature_markets()
    print(f"  {len(markets)} temperatuurmarkten gevonden")

    from datetime import timedelta
    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    # Minimaal 2 dagen vooruit — vandaag/morgen kan al bijna gesloten zijn door tijdzone
    min_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")

    # Filter eerst: alleen parseerbare toekomstige markten met handelbare prijs
    candidates = []
    for market in markets:
        q      = market.get("question", "")
        parsed = parse_temperature_question(q)
        if not parsed:
            continue
        if parsed["date"] < min_date:   # vandaag/morgen overslaan (tijdzone + te weinig tijd)
            continue
        prices = market.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: continue
        poly_price = float(prices[0]) if prices else 0
        if not (0.01 < poly_price < 0.99):
            continue
        # Volume filter: min $500 liquiditeit (dunne markten = ruis)
        liquidity = float(market.get("liquidity") or 0)
        if liquidity < 500:
            continue
        candidates.append((market, parsed, poly_price))

    print(f"  {len(candidates)} kandidaat-markten na filtering")

    # Unieke (stad, datum) combinaties ophalen — niet per markt
    city_date_pairs = list({(p["city"], p["date"]) for _, p, _ in candidates})
    print(f"  {len(city_date_pairs)} unieke stad/datum combinaties, parallel ophalen...")

    # Parallelle weeropvraag: max 10 gelijktijdig
    forecasts = {}
    with ThreadPoolExecutor(max_workers=10) as exe:
        future_map = {
            exe.submit(multi_source_forecast, city, date): (city, date)
            for city, date in city_date_pairs
        }
        for future in as_completed(future_map):
            city, date = future_map[future]
            try:
                result = future.result(timeout=15)
                forecasts[(city, date)] = result
            except Exception:
                pass

    print(f"  {len(forecasts)} weerprognoses opgehaald")

    opportunities = []
    for market, parsed, poly_price in candidates:
        key = (parsed["city"], parsed["date"])
        fc  = forecasts.get(key)
        if not fc or "error" in fc:
            continue
        if fc["confidence"] == "LAAG":
            continue

        forecast_c   = fc["consensus"]
        raw_spread   = fc.get("spread", 2.0)
        spread_source = fc.get("spread_source", "inter-model")
        # Ensemble P10-P90 heeft grotere schaal dan inter-model sigma:
        # P10-P90 ≈ 2.56σ, dus we converteren naar effectieve sigma
        spread = raw_spread / 2.56 if spread_source == "ensemble" else raw_spread
        n_sources  = fc.get("n_sources", 0)

        # Minimaal 3 modellen vereist voor betrouwbare consensus
        if n_sources < 3:
            continue

        # Dagen vooruit berekenen voor sigma-calibratie
        try:
            from datetime import date as _date
            days_ahead = (_date.fromisoformat(parsed["date"]) - _date.today()).days
        except Exception:
            days_ahead = 4

        model_prob = model_probability(forecast_c, parsed, spread=spread, days_ahead=days_ahead)

        # ── Seasonal ensemble blending (voor markten > 7 dagen vooruit) ──────
        # Bij lange horizons verliest het deterministische model voorspelkracht.
        # Blend met de 50-member seasonal ensemble als klimaat-prior.
        if days_ahead >= 8:
            from weather_sources import get_seasonal_prob
            low_c_s  = to_celsius(parsed["temp_low"],  parsed["unit"])
            high_c_s = to_celsius(parsed["temp_high"], parsed["unit"])
            thr_c    = low_c_s if parsed["condition"] == "above" else high_c_s
            seasonal = get_seasonal_prob(parsed["city"].lower(), parsed["date"], thr_c, parsed["condition"])
            if seasonal is not None:
                # Gewicht seasonal stijgt met dagen vooruit: dag 8 → 20%, dag 14 → 50%
                w_seasonal = min(0.5, (days_ahead - 7) * 0.075)
                w_model    = 1 - w_seasonal
                model_prob = round(w_model * model_prob + w_seasonal * seasonal, 3)

        gap = model_prob - poly_price

        # Extra kwaliteitscheck: hoe ver zit de forecast van de threshold?
        # Hoe verder, hoe meer zekerheid (minder kans op grens-overschrijding door ruis)
        low_c  = to_celsius(parsed["temp_low"],  parsed["unit"])
        high_c = to_celsius(parsed["temp_high"], parsed["unit"])
        if parsed["condition"] == "above":
            threshold_dist = abs(forecast_c - low_c)
        elif parsed["condition"] == "below":
            threshold_dist = abs(forecast_c - high_c)
        else:  # between / exact
            threshold_dist = min(abs(forecast_c - low_c), abs(forecast_c - high_c))

        # Verhoog effectieve gap-drempel als forecast dicht bij de grens zit
        # (< 1°C afstand = heel onzeker, < 2°C = matig)
        if threshold_dist < 1.0:
            effective_min_gap = MIN_GAP * 2.0   # dubbele drempel bij grensgeval
        elif threshold_dist < 2.0:
            effective_min_gap = MIN_GAP * 1.5
        else:
            effective_min_gap = MIN_GAP

        # ── Model shift detectie ─────────────────────────────────────────────
        model_shift_val = 0.0
        try:
            from weather_sources import detect_model_shift
            shift_info = detect_model_shift(parsed["city"], parsed["date"])
            if shift_info:
                model_shift_val = shift_info["shift"] if shift_info["direction"] == "warmer" else -shift_info["shift"]
        except Exception:
            pass

        volume = float(market.get("volume24hr") or 0)
        if abs(gap) >= effective_min_gap and volume >= MIN_VOLUME:
            display_temp = forecast_c if parsed["unit"] == "C" else forecast_c * 9/5 + 32
            q = market.get("question", "")
            opportunities.append(WeatherOpportunity(
                question=q,
                city=parsed["city"].title(),
                date=parsed["date"],
                condition=parsed["condition"],
                temp_low=parsed["temp_low"],
                temp_high=parsed["temp_high"],
                unit=parsed["unit"],
                poly_price=poly_price,
                forecast_temp=round(display_temp, 1),
                model_prob=round(model_prob, 3),
                gap=round(gap, 3),
                direction="BUY YES" if gap > 0 else "BUY NO",
                volume=volume,
                condition_id=market.get("conditionId", ""),
                slug=market.get("slug", ""),
                market_id=str(market.get("id", "")),
                model_shift=model_shift_val,
            ))

    # ── Consistentie check: monotone kansen per stad/datum ───────────────────
    # Hogere drempel moet lagere kans hebben (monotoon dalend).
    # Inversie = mispricing → verhoog de gap voor ranking.
    from collections import defaultdict
    city_date_groups: dict = defaultdict(list)
    for opp in opportunities:
        if opp.condition in ("above", "below"):
            city_date_groups[(opp.city, opp.date)].append(opp)

    for group_opps in city_date_groups.values():
        if len(group_opps) < 2:
            continue
        # Sorteer op drempeltemperatuur (oplopend)
        sorted_group = sorted(group_opps, key=lambda o: to_celsius(o.temp_low, o.unit))
        # Check monotoniteit: hogere drempel ("above") = lagere kans
        for i in range(len(sorted_group) - 1):
            lo = sorted_group[i]
            hi = sorted_group[i + 1]
            # Beide moeten "above" zijn voor consistentie check op drempel
            if lo.condition == "above" and hi.condition == "above":
                # lo heeft lagere drempel → moet hogere kans hebben
                if lo.poly_price < hi.poly_price:
                    # Inversie: lo is goedkoper dan hi terwijl drempel lager is
                    lo.consistency_bonus = 0.05

    opportunities = sorted(opportunities, key=lambda o: abs(o.gap) + o.consistency_bonus, reverse=True)

    # Sla scan resultaten op naar data/scan_log.jsonl
    import json as _json
    import pathlib as _pathlib
    _log_path = _pathlib.Path(__file__).parent / "data" / "scan_log.jsonl"
    _pathlib.Path(_log_path.parent).mkdir(exist_ok=True)
    _record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "opportunities": [
            {
                "question":   o.question,
                "city":       o.city,
                "date":       o.date,
                "poly_price": o.poly_price,
                "model_prob": o.model_prob,
                "gap":        o.gap,
                "direction":  o.direction,
                "volume":     o.volume,
            }
            for o in opportunities
        ],
    }
    try:
        with open(_log_path, "a") as _f:
            _f.write(_json.dumps(_record) + "\n")
    except Exception:
        pass

    return opportunities


def display(opportunities: list[WeatherOpportunity]):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n── Weather Scanner [{ts}] ────────────────────────────────")

    if not opportunities:
        print(f"  Geen kansen gevonden (minimaal {MIN_GAP*100:.0f}% gap vereist)")
        return

    print(f"  {len(opportunities)} kans(en) gevonden:\n")

    for opp in opportunities:
        sign = "+" if opp.gap > 0 else ""
        print(f"  [{opp.label()}] {opp.question[:65]}")
        print(f"  {'':4} Stad:        {opp.city} — {opp.date}")
        print(f"  {'':4} Voorspelling: {opp.forecast_temp}°{opp.unit} (weermodel)")
        print(f"  {'':4} Polymarket:  {opp.poly_price*100:.0f}% YES")
        print(f"  {'':4} Model kans:  {opp.model_prob*100:.0f}% YES")
        print(f"  {'':4} GAP:         {sign}{opp.gap*100:.1f}% → {opp.direction}")
        print(f"  {'':4} Volume:      ${opp.volume:,.0f}/dag")
        if opp.model_shift != 0.0:
            shift_sign = "+" if opp.model_shift > 0 else ""
            print(f"  {'':4} ⚡ Model shift: {shift_sign}{opp.model_shift:.1f}°C (nieuw model run)")
        if opp.consistency_bonus > 0:
            print(f"  {'':4} ✓ Consistentie bonus")
        print()


if __name__ == "__main__":
    print("── Polymarket Weather Temperature Scanner ───────────────")
    opps = scan()
    display(opps)
