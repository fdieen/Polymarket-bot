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
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

GAMMA_API  = "https://gamma-api.polymarket.com/markets"
OPENMETEO  = "https://api.open-meteo.com/v1/forecast"

MIN_GAP    = 0.35   # minimaal 35%-punt — HOOG confidence met lage spread heeft hogere trefzekerheid
MIN_VOLUME = 50.0   # minimaal $50/dag volume — illiquide markten hebben slechte spreads

# YES-bets op lage prijzen (<20%) zijn historisch verliesgevend: 0W/1L (-$20)
# Daarom extra filter: alleen YES als poly_price >= 0.20
MIN_YES_PRICE = 0.20

# Tropische steden: stabiel warm weer maakt model minder betrouwbaar (6 van 14 verliesgevende trades)
# Hogere drempel vereist om false positives te vermijden
TROPICAL_CITIES = {
    "kuala lumpur", "singapore", "panama city", "mumbai", "bangkok",
    "jakarta", "manila", "ho chi minh", "colombo", "dhaka",
    "miami",  # 3W/2L historisch — tropisch klimaat met hoge dagelijkse variatie
}
TROPICAL_MIN_GAP = 0.55  # 55% gap vereist voor tropische steden (vs 35% normaal)

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
    "seoul":         (37.558, 126.791),  # Polymarket: Incheon (RKSS)
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
    source_agreement:  float = 1.0   # 0.0–1.0: mate van overeenstemming tussen bronnen
    source_probs:      str   = ""    # debug: "model=0.08 mos=0.12 climo=0.09"

    def label(self):
        if abs(self.gap) >= 0.30: return "STERK"
        if abs(self.gap) >= 0.15: return "GOED"
        return "ZWAK"


def parse_temperature_question(question: str) -> Optional[dict]:
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


def fetch_forecast_full(city: str, date: str) -> Optional[dict]:
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


def fetch_forecast_temp(city: str, date: str) -> Optional[float]:
    """Backwards-compat wrapper."""
    result = fetch_forecast_full(city, date)
    return result["temp_max"] if result else None


def fetch_all_city_temps(date: Optional[str] = None) -> dict:
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


def _resolve_condition_id(market: dict) -> str:
    """Geeft conditionId terug. Als Gamma het niet meestuurt, haal het op via CLOB."""
    cid = market.get("conditionId", "")
    if cid:
        return cid
    # Fallback: CLOB API via slug
    slug = market.get("slug", "")
    if slug:
        try:
            r = requests.get(f"https://clob.polymarket.com/markets/{slug}", timeout=6)
            if r.status_code == 200:
                return r.json().get("condition_id", "")
        except Exception:
            pass
    # Fallback: CLOB API via market_id (event markets endpoint)
    mid = str(market.get("id", ""))
    if mid:
        try:
            r = requests.get(
                "https://gamma-api.polymarket.com/markets",
                params={"id": mid},
                timeout=6,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    return data[0].get("conditionId", "")
        except Exception:
            pass
    return ""


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

    # Dedupliceer op conditionId én vraagtekst (beide checks — conditionId kan leeg zijn)
    seen_cid = set()
    seen_q = set()
    unique = []
    for m in results:
        cid = m.get("conditionId", "")
        q = m.get("question", "")
        if (cid and cid in seen_cid) or (q and q in seen_q):
            continue
        if cid:
            seen_cid.add(cid)
        if q:
            seen_q.add(q)
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

        raw_model_prob = model_probability(forecast_c, parsed, spread=spread, days_ahead=days_ahead)
        model_prob     = raw_model_prob
        _climo_prob: Optional[float] = None
        _mos_prob:   Optional[float] = None
        _ens_prob:   Optional[float] = None

        # ── Klimatologie blending (historische verdeling als prior) ──────────
        try:
            from climatology import climo_probability, blend_with_climo
            city_lower = parsed["city"].lower()
            coords = CITIES.get(city_lower)
            if coords:
                _climo_prob = climo_probability(
                    city=city_lower,
                    date_str=parsed["date"],
                    parsed=parsed,
                    lat=coords[0],
                    lon=coords[1],
                )
                if _climo_prob is not None:
                    model_prob = blend_with_climo(model_prob, _climo_prob, days_ahead)
        except Exception:
            pass

        # ── GFS-MOS blending (alleen dag 1-5, US/EU vliegvelden) ────────────
        try:
            from mos import get_mos_forecast, mos_probability, blend_mos
            mos_fc = get_mos_forecast(parsed["city"].lower(), parsed["date"])
            if mos_fc is not None:
                _mos_prob = mos_probability(mos_fc, parsed)
                if _mos_prob is not None:
                    model_prob = blend_mos(model_prob, _mos_prob, days_ahead)
        except Exception:
            pass

        # ── Ensemble P(YES): directe kansbepaling uit member verdeling ────────
        # Nauwkeuriger dan consensus+normale verdeling: empirisch tellen
        # welk percentage van de 40-51 ensemble members de conditie haalt.
        _ens_prob: Optional[float] = None
        try:
            from weather_sources import ensemble_probability
            lo_c  = to_celsius(parsed["temp_low"],  parsed["unit"])
            hi_c  = to_celsius(parsed["temp_high"], parsed["unit"])
            _ens_prob = ensemble_probability(
                city      = parsed["city"].lower(),
                date      = parsed["date"],
                temp_low_c = lo_c,
                temp_high_c= hi_c,
                condition  = parsed["condition"],
                temp_type  = parsed.get("temp_type", "high"),
            )
            if _ens_prob is not None:
                # Gewicht: dag 1-3: 40% ensemble (meest betrouwbaar dichtbij)
                #          dag 4-6: 35%, dag 7+: 25%
                if days_ahead <= 3:
                    w_ens = 0.40
                elif days_ahead <= 6:
                    w_ens = 0.35
                else:
                    w_ens = 0.25
                model_prob = round((1 - w_ens) * model_prob + w_ens * _ens_prob, 3)
        except Exception:
            pass

        # ── Koudefront detectie ──────────────────────────────────────────────
        # Actieve fronten verhogen temperatuuronzekerheid → kans richting 0.5 trekken
        _front_risk: Optional[dict] = None
        try:
            from cold_front import get_front_risk, apply_front_risk
            _front_risk = get_front_risk(
                city=parsed["city"].lower(),
                date_str=parsed["date"],
                lat=lat,
                lon=lon,
            )
            if _front_risk and _front_risk["risk"] >= 0.40:
                model_prob = apply_front_risk(model_prob, poly_price, _front_risk, parsed)
        except Exception:
            pass

        # ── Source agreement score ────────────────────────────────────────────
        # Meet hoeveel bronnen het eens zijn over de richting (YES/NO) en
        # hoe dicht ze bij elkaar zitten. Hogere agreement → grotere positie.
        _source_probs = [raw_model_prob]
        if _climo_prob is not None: _source_probs.append(_climo_prob)
        if _mos_prob   is not None: _source_probs.append(_mos_prob)
        if _ens_prob   is not None: _source_probs.append(_ens_prob)

        if len(_source_probs) >= 2:
            # Richting-overeenstemming: alle bronnen aan dezelfde kant van poly_price?
            directions = [p > poly_price for p in _source_probs]
            all_agree  = all(directions) or not any(directions)
            # Spreiding: kleinere spread = hogere zekerheid
            spread_sources = max(_source_probs) - min(_source_probs)
            spread_score   = max(0.0, 1.0 - spread_sources / 0.30)  # 0% spread=1.0, 30%=0.0
            agreement = (1.0 if all_agree else 0.4) * spread_score
        else:
            agreement = 0.5  # maar één bron beschikbaar

        # Frontrisico verlaagt agreement (hogere onzekerheid = kleiner positie)
        if _front_risk and _front_risk["risk"] >= 0.40:
            front_penalty = _front_risk["risk"] * 0.30  # max -30% agreement bij risk=1.0
            agreement = max(0.0, agreement - front_penalty)

        _src_debug = (
            f"model={raw_model_prob:.2f}"
            + (f" climo={_climo_prob:.2f}" if _climo_prob is not None else "")
            + (f" mos={_mos_prob:.2f}"     if _mos_prob   is not None else "")
            + (f" ens={_ens_prob:.2f}"     if _ens_prob   is not None else "")
            + (f" front={_front_risk['risk']:.2f}({_front_risk['type'][:4]})" if _front_risk and _front_risk["risk"] >= 0.40 else "")
        )

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

        # ── Dynamische gap-drempel op basis van modelconsensus ──────────────
        # Hoge consensus (veel modellen eens, lage spread) → lagere drempel
        # Lage consensus (modellen verdeeld, hoge spread) → hogere drempel
        confidence = fc.get("confidence", "MATIG")
        if confidence == "HOOG" and n_sources >= 5:
            consensus_factor = 0.80   # 80% van base → 32% bij MIN_GAP=0.40
        elif confidence == "MATIG" or n_sources < 4:
            consensus_factor = 1.20   # 120% van base → 48%
        elif confidence == "LAAG":
            consensus_factor = 1.50   # wordt al gefilterd boven, maar als fallback
        else:
            consensus_factor = 1.00

        # ── Grensafstand correctie (op consensus_factor) ─────────────────────
        # (< 1°C afstand = heel onzeker, < 2°C = matig)
        # Bij HOOG confidence + 5 modellen is de grensafstand minder kritiek:
        # de modellen zijn het eens, dus de onzekerheid is al verwerkt in de spread
        high_conf = (confidence == "HOOG" and n_sources >= 5)
        if threshold_dist < 1.0:
            boundary_factor = 1.4 if high_conf else 2.0
        elif threshold_dist < 2.0:
            boundary_factor = 1.1 if high_conf else 1.5
        else:
            boundary_factor = 1.0

        # Gap drempel per markttype — gebaseerd op historische win rates
        # "or higher/lower": 2W/3L = 40% → drempel fors hoger
        # "between": 12W/1L = 92% → lage drempel volstaat
        # Tropisch penalty wordt NA markttype toegepast (neem de hoogste)
        condition_type = parsed.get("condition", "")
        if condition_type in ("above", "below"):
            base_gap = 0.65        # or higher/below: 40% win rate → hoge drempel
        elif condition_type == "between":
            base_gap = 0.20        # between: 92% win rate → lagere drempel volstaat
        else:
            base_gap = MIN_GAP
        # Tropische steden: drempel verhogen bovenop markttype (neem hoogste waarde)
        if parsed["city"].lower() in TROPICAL_CITIES:
            base_gap = max(base_gap, TROPICAL_MIN_GAP)
        effective_min_gap = base_gap * consensus_factor * boundary_factor

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

        # Filter: YES-bets op te lage prijzen zijn historisch verliesgevend
        if gap > 0 and poly_price < MIN_YES_PRICE:
            continue

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
                condition_id=_resolve_condition_id(market),
                slug=market.get("slug", ""),
                market_id=str(market.get("id", "")),
                model_shift=model_shift_val,
                source_agreement=round(agreement, 2),
                source_probs=_src_debug,
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

    # ── ColdMath-stijl scan: near-impossible YES prijzen ─────────────────────
    # Zoek markten waar YES nog 5-22% staat maar model <8% berekent
    # Dit zijn structurele mispricingen — liquidity providers hebben prijs niet bijgewerkt
    from datetime import timedelta as _td
    coldmath_min_date = (datetime.now(timezone.utc) + _td(days=1)).strftime("%Y-%m-%d")
    for market in markets:
        q      = market.get("question", "")
        parsed = parse_temperature_question(q)
        if not parsed:
            continue
        if parsed["date"] < coldmath_min_date:
            continue
        prices = market.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: continue
        poly_price = float(prices[0]) if prices else 0
        if not (0.05 < poly_price < 0.22):
            continue
        liq = float(market.get("liquidity") or 0)
        if liq < 300:
            continue

        key = (parsed["city"], parsed["date"])
        fc  = forecasts.get(key)
        if not fc or "error" in fc:
            continue
        if fc.get("n_sources", 0) < 4:
            continue
        if fc.get("confidence") != "HOOG":
            continue

        forecast_c = fc["consensus"]
        spread = fc.get("spread", 2.0)
        if fc.get("spread_source") == "ensemble":
            spread = spread / 2.56
        try:
            from datetime import date as _date2
            _days = (_date2.fromisoformat(parsed["date"]) - _date2.today()).days
        except Exception:
            _days = 2
        model_prob_yes = model_probability(forecast_c, parsed, spread=spread, days_ahead=_days)

        # Alleen als model zegt <8% en prijs is >5%: structurele mispricing
        no_gap = poly_price - model_prob_yes
        if model_prob_yes >= 0.08 or no_gap < 0.08:
            continue

        # Check al in opportunities (voorkom duplicaat)
        cid = market.get("conditionId", "")
        if any(o.condition_id == cid for o in opportunities):
            continue

        volume = float(market.get("volume24hr") or 0)
        if volume < MIN_VOLUME:
            continue

        display_temp = forecast_c if parsed["unit"] == "C" else forecast_c * 9/5 + 32
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
            model_prob=round(model_prob_yes, 3),
            gap=round(-no_gap, 3),   # negatief = BUY NO
            direction="BUY NO",
            volume=volume,
            condition_id=cid,
            slug=market.get("slug", ""),
            market_id=str(market.get("id", "")),
        ))

    opportunities = sorted(opportunities, key=lambda o: abs(o.gap) + o.consistency_bonus, reverse=True)

    return opportunities


def scan_metar_lock() -> list[WeatherOpportunity]:
    """
    METAR Lock strategie: na ~14h lokaal staat de dagmax grotendeels vast.

    Vergelijkt de gemeten dagmax (via METAR history, dezelfde bron als Wunderground/Polymarket)
    met open markten voor vandaag. Als de dagmax al duidelijk buiten een bucket valt,
    is de uitkomst vrijwel zeker → koop de winnende kant.

    Win rate: ~88% (bron: Kalshi Weather Edge, bevestigd door meerdere traders).
    Minimale edge: dagmax moet > METAR_LOCK_MARGIN buiten de range liggen.
    """
    from weather_sources import get_metar_daymax, CITY_META
    from datetime import datetime, timezone

    METAR_LOCK_MARGIN_F = 2.0   # dagmax moet min. 2°F buiten range liggen
    METAR_LOCK_MARGIN_C = 1.0   # of 1°C buiten range
    MIN_HOUR_UTC        = 14    # pas na 14h UTC (~09-10h lokaal VS oost = vroeg middag)
    MAX_POLY_PRICE      = 0.92  # markt mag niet al volledig ingeprijsd zijn

    now_utc_hour = datetime.now(timezone.utc).hour
    if now_utc_hour < MIN_HOUR_UTC:
        return []   # te vroeg — dagmax niet stabiel genoeg

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    markets = fetch_temperature_markets()
    today_markets = [
        m for m in markets
        if today_str in m.get("question", "")
        or (m.get("endDate", "") or "")[:10] == today_str
    ]

    if not today_markets:
        return []

    opportunities = []

    for market in today_markets:
        parsed = parse_market(market)
        if not parsed:
            continue

        city_lower = parsed["city"].lower()
        meta = CITY_META.get(city_lower)
        if not meta:
            continue

        poly_price = float(
            market.get("lastTradePrice")
            or (market.get("outcomePrices") or ["0.5"])[0]
        )
        if poly_price <= 0.01 or poly_price >= MAX_POLY_PRICE:
            continue   # al bijna zeker of geen liquide prijs

        # Haal dagmax op via METAR history (= Wunderground data)
        day_obs = get_metar_daymax(city_lower)
        if not day_obs or day_obs["n_obs"] < 6:
            continue   # te weinig observaties

        unit    = parsed["unit"]
        lo      = parsed["temp_low"]
        hi      = parsed["temp_high"]
        cond    = parsed["condition"]

        if unit == "F":
            obs_max = day_obs["day_max_f"]
            obs_min = day_obs["day_min_f"]
            margin  = METAR_LOCK_MARGIN_F
        else:
            obs_max = day_obs["day_max_c"]
            obs_min = day_obs["day_min_c"]
            margin  = METAR_LOCK_MARGIN_C

        temp_type = parsed.get("temp_type", "high")
        obs_val   = obs_max if temp_type == "high" else obs_min

        # Bepaal of uitkomst al vaststaat
        lock_direction = None
        lock_confidence = 0.0

        if cond == "between":
            if obs_val > hi + margin:
                lock_direction  = "NO"      # temp al boven range — YES onmogelijk
                lock_confidence = min(0.97, 0.80 + (obs_val - hi - margin) * 0.05)
            elif obs_val < lo - margin and temp_type == "high":
                # Dagmax al < ondergrens — zou kunnen stijgen, alleen lock als ruim eronder
                if obs_val < lo - margin * 3:
                    lock_direction  = "NO"
                    lock_confidence = min(0.92, 0.75 + (lo - obs_val - margin * 3) * 0.04)

        elif cond == "above":
            if obs_val >= lo:
                lock_direction  = "YES"     # al boven drempel — YES zeker
                lock_confidence = min(0.97, 0.85 + (obs_val - lo) * 0.02)
            elif obs_val < lo - margin * 3:
                lock_direction  = "NO"
                lock_confidence = min(0.90, 0.75 + (lo - obs_val - margin * 3) * 0.04)

        elif cond == "below":
            if obs_val <= hi:
                lock_direction  = "YES"
                lock_confidence = min(0.97, 0.85 + (hi - obs_val) * 0.02)

        if lock_direction is None or lock_confidence < 0.75:
            continue

        # Bereken gap t.o.v. marktprijs
        if lock_direction == "YES":
            model_prob = lock_confidence
            gap        = model_prob - poly_price
        else:
            no_price   = 1 - poly_price
            model_prob = lock_confidence
            gap        = model_prob - no_price

        if abs(gap) < 0.08:     # minimaal 8% edge
            continue

        q = market.get("question", "")
        opportunities.append(WeatherOpportunity(
            question        = q,
            city            = parsed["city"].title(),
            date            = parsed["date"],
            condition       = cond,
            temp_low        = lo,
            temp_high       = hi,
            unit            = unit,
            poly_price      = poly_price if lock_direction == "YES" else 1 - poly_price,
            forecast_temp   = obs_val,
            model_prob      = model_prob,
            gap             = gap,
            direction       = lock_direction,
            volume          = float(market.get("volume") or 0),
            condition_id    = _resolve_condition_id(market),
            slug            = market.get("slug", ""),
            market_id       = str(market.get("id", "")),
            source_agreement= lock_confidence,
            source_probs    = f"metar_lock obs={obs_val:.1f}{unit} conf={lock_confidence:.2f} n_obs={day_obs['n_obs']}",
        ))

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


import threading
import logging
import time

log = logging.getLogger("weather_scanner")

ALERT_MIN_GAP   = 0.25   # lager dan trade-drempel — vroeg signaal sturen
POLL_INTERVAL_S = 1800   # elke 30 minuten scannen


class WeatherScanner:
    """Achtergrond-scanner die continu op kansen scant en Telegram alerts stuurt."""

    def __init__(self):
        self._stop   = threading.Event()
        self._seen   = set()          # al gemelde kansen (reset elke dag)
        self._date   = None
        self._thread = None
        self.latest: list = []        # voor dashboard gebruik

    def _reset_daily(self):
        today = datetime.now(timezone.utc).date()
        if today != self._date:
            self._seen.clear()
            self._date = today
            log.info("Weather scanner: dagelijkse reset")

    def _run(self):
        log.info("Weather scanner achtergrond-loop gestart")
        while not self._stop.is_set():
            try:
                self._reset_daily()
                opps = scan()
                self.latest = opps

                for o in opps:
                    if abs(o.gap) < ALERT_MIN_GAP:
                        continue
                    key = f"{o.question}:{round(o.gap, 2)}"
                    if key in self._seen:
                        continue
                    self._seen.add(key)

                    sign = "+" if o.gap > 0 else ""
                    msg = (
                        f"🌡 <b>WEATHER KANS</b>\n\n"
                        f"📋 {o.question}\n"
                        f"• Richting:    <b>{o.direction}</b>\n"
                        f"• Polymarket:  {o.poly_price*100:.0f}%\n"
                        f"• Model:       {o.model_prob*100:.0f}%\n"
                        f"• GAP:         <b>{sign}{o.gap*100:.1f}%</b>\n"
                        f"• Forecast:    {o.forecast_temp}°{o.unit}\n"
                        f"• Vol/dag:     ${o.volume:,.0f}\n"
                        + (f"• <a href=\"https://polymarket.com/event/{o.slug}\">Bekijk op Polymarket</a>\n" if o.slug else "")
                    )
                    try:
                        from alerts import send_telegram
                        send_telegram(msg)
                        log.info(f"WEATHER ALERT: {o.city} {o.date} | {o.direction} | gap={sign}{o.gap*100:.1f}%")
                    except Exception as e:
                        log.warning(f"Telegram alert mislukt: {e}")

            except Exception as e:
                log.warning(f"Weather scanner fout: {e}")

            self._stop.wait(POLL_INTERVAL_S)

        log.info("Weather scanner gestopt")

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="weather-scanner")
        self._thread.start()

    def stop(self):
        self._stop.set()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="Polymarket Weather Scanner")
    parser.add_argument("--once",   action="store_true", help="Eenmalige scan")
    parser.add_argument("--daemon", action="store_true", help="Blijf draaien (poll elke 30 min)")
    args = parser.parse_args()

    print("── Polymarket Weather Temperature Scanner ───────────────")
    opps = scan()
    display(opps)

    if args.daemon and not args.once:
        print(f"\nContinue polling elke {POLL_INTERVAL_S // 60} minuten...")
        ws = WeatherScanner()
        ws.start()
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            ws.stop()
            print("\nGestopt.")
