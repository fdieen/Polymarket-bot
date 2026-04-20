"""
climatology.py — Historische temperatuurstatistieken per stad/datum.

Gebruikt Open-Meteo archive API (gratis, geen key nodig) om 10 jaar dagelijkse
max/min temperaturen op te halen. Resultaten worden gecached in data/climo_cache.json.

Geeft P(temp ∈ range) op basis van empirische verdeling — niet een puntschatting
maar een echte kansverdeling gebaseerd op historische data voor die stad/datum.

Gebruik:
    from climatology import climo_probability, get_climo_stats
    prob = climo_probability("miami", "2026-04-18", {"condition":"between","temp_low":29.0,"temp_high":30.0})
"""

import json
import math
import logging
import os
import requests
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("climatology")

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
CACHE_FILE   = os.path.join(os.path.dirname(__file__), "data", "climo_cache.json")

# 10 jaar historische data (archive heeft data t/m ~5 dagen geleden)
CLIMO_START_YEAR = 2014
CLIMO_END_YEAR   = 2024

# ±7 dagen venster rond de doeldatum → 10j × 15d = max 150 samples
WINDOW_DAYS = 7


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        log.warning(f"Cache opslaan mislukt: {e}")


# ── Open-Meteo archive ophalen ────────────────────────────────────────────────

def _fetch_year(lat: float, lon: float, year: int, month_day: str, temp_type: str) -> list[float]:
    """
    Haalt dagelijkse temperaturen op voor één jaar, ±WINDOW_DAYS rond de doeldatum.
    temp_type: "high" → temperature_2m_max, "low" → temperature_2m_min
    """
    month, day = int(month_day[:2]), int(month_day[3:])
    try:
        center = date(year, month, day)
    except ValueError:
        return []  # ongeldig datum (bijv. 29 feb in niet-schrikkeljaar)

    start = center - timedelta(days=WINDOW_DAYS)
    end   = center + timedelta(days=WINDOW_DAYS)

    var = "temperature_2m_max" if temp_type == "high" else "temperature_2m_min"

    try:
        r = requests.get(
            ARCHIVE_API,
            params={
                "latitude":   lat,
                "longitude":  lon,
                "start_date": start.isoformat(),
                "end_date":   end.isoformat(),
                "daily":      var,
                "timezone":   "auto",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        values = data.get("daily", {}).get(var, [])
        return [v for v in values if v is not None]
    except Exception as e:
        log.debug(f"Archive fetch fout {year}: {e}")
        return []


def get_climo_samples(
    city: str,
    month_day: str,   # "04-18"
    temp_type: str,   # "high" of "low"
    lat: float,
    lon: float,
) -> list[float]:
    """
    Geeft lijst van historische temperaturen (°C) voor stad/datum over CLIMO_YEARS jaren.
    Gebruikt cache — API wordt alleen aangeroepen als er geen gecachte data is.
    """
    cache_key = f"{city}:{month_day}:{temp_type}"
    cache = _load_cache()

    if cache_key in cache:
        return cache[cache_key]

    log.info(f"Klimatologie ophalen: {city} {month_day} ({temp_type}) — {CLIMO_END_YEAR - CLIMO_START_YEAR} jaar...")

    all_samples: list[float] = []
    for year in range(CLIMO_START_YEAR, CLIMO_END_YEAR + 1):
        samples = _fetch_year(lat, lon, year, month_day, temp_type)
        all_samples.extend(samples)

    if all_samples:
        cache[cache_key] = all_samples
        _save_cache(cache)
        log.info(f"  {len(all_samples)} samples opgeslagen voor {city} {month_day}")

    return all_samples


# ── Statistieken ──────────────────────────────────────────────────────────────

def get_climo_stats(samples: list[float]) -> Optional[dict]:
    """Berekent mean, std en percentilen uit samples."""
    if len(samples) < 10:
        return None

    n    = len(samples)
    mean = sum(samples) / n
    var  = sum((x - mean) ** 2 for x in samples) / (n - 1)
    std  = math.sqrt(var)

    sorted_s = sorted(samples)
    p10 = sorted_s[int(0.10 * n)]
    p25 = sorted_s[int(0.25 * n)]
    p50 = sorted_s[int(0.50 * n)]
    p75 = sorted_s[int(0.75 * n)]
    p90 = sorted_s[int(0.90 * n)]

    return {
        "mean": round(mean, 2),
        "std":  round(std, 2),
        "p10":  round(p10, 1),
        "p25":  round(p25, 1),
        "p50":  round(p50, 1),
        "p75":  round(p75, 1),
        "p90":  round(p90, 1),
        "n":    n,
    }


def _empirical_prob(samples: list[float], lo: float, hi: float) -> float:
    """P(lo ≤ temp ≤ hi) op basis van empirische verdeling."""
    n = len(samples)
    if n == 0:
        return 0.5
    return sum(1 for s in samples if lo <= s <= hi) / n


def _gaussian_prob(mean: float, std: float, lo: float, hi: float) -> float:
    """P(lo ≤ temp ≤ hi) via normale verdeling met gegeven mean/std."""
    def cdf(x):
        return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))
    return cdf(hi) - cdf(lo)


# ── Hoofd-interface ───────────────────────────────────────────────────────────

def climo_probability(
    city: str,
    date_str: str,   # "2026-04-18"
    parsed: dict,    # parsed market dict uit weather_scanner
    lat: float,
    lon: float,
) -> Optional[float]:
    """
    Berekent P(YES) op basis van historische klimatologie.

    Combineert empirische verdeling (directe teldata) met een geglaasde
    normale verdeling (robuust bij weinig samples).

    Returns None als er onvoldoende data is.
    """
    month_day = date_str[5:]   # "2026-04-18" → "04-18"
    temp_type = parsed.get("temp_type", "high")
    condition = parsed.get("condition", "between")

    lo_c = parsed.get("temp_low",  -999.0)
    hi_c = parsed.get("temp_high",  999.0)

    samples = get_climo_samples(city, month_day, temp_type, lat, lon)
    stats   = get_climo_stats(samples)

    if stats is None:
        return None

    mean = stats["mean"]
    std  = stats["std"]

    # Empirisch: directe teldata (ruwer maar onbevooroordeeld)
    if condition == "above":
        emp = sum(1 for s in samples if s >= lo_c) / len(samples)
        gau = 1 - 0.5 * (1 + math.erf((lo_c - mean) / (std * math.sqrt(2))))
    elif condition == "below":
        emp = sum(1 for s in samples if s <= hi_c) / len(samples)
        gau = 0.5 * (1 + math.erf((hi_c - mean) / (std * math.sqrt(2))))
    else:  # between / exact
        emp = _empirical_prob(samples, lo_c, hi_c)
        gau = _gaussian_prob(mean, std, lo_c, hi_c)

    # Blend empirisch + gaussisch (60/40): meer samples → empirisch betrouwbaarder
    w_emp = min(0.80, len(samples) / 200)  # bij 150+ samples → 75% empirisch
    w_gau = 1 - w_emp
    prob  = w_emp * emp + w_gau * gau

    log.debug(
        f"Climo {city} {month_day}: mean={mean:.1f}°C std={std:.1f}°C | "
        f"emp={emp:.3f} gau={gau:.3f} → {prob:.3f} ({len(samples)} samples)"
    )
    return round(prob, 3)


def blend_with_climo(
    model_prob: float,
    climo_prob: float,
    days_ahead: int,
) -> float:
    """
    Blended kans: gewogen gemiddelde van model-forecast en klimatologie.

    Gewichtslogica:
      - 2-3 dagen vooruit: model is nauwkeurig → 85% model, 15% climo
      - 4-6 dagen: model neemt af → 70% model, 30% climo
      - 7+ dagen: model onbetrouwbaar → 50% model, 50% climo
    """
    if days_ahead <= 3:
        w_climo = 0.15
    elif days_ahead <= 6:
        w_climo = 0.30
    else:
        w_climo = 0.50

    w_model = 1 - w_climo
    blended = w_model * model_prob + w_climo * climo_prob
    return round(blended, 3)


# ── CLI: vooraf warmdraaien van de cache ──────────────────────────────────────

if __name__ == "__main__":
    """
    Warmdraaien: haalt klimatologie op voor alle steden in weather_scanner.CITIES
    voor de komende 30 dagen. Run dit eenmalig om de cache te vullen.

        python climatology.py
    """
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    sys.path.insert(0, os.path.dirname(__file__))
    from weather_scanner import CITIES

    today = date.today()
    dates = [(today + timedelta(days=d)).strftime("%m-%d") for d in range(1, 31)]
    unique_dates = list(dict.fromkeys(dates))  # dedup maar volgorde bewaren

    total = len(CITIES) * len(unique_dates) * 2  # high + low
    done  = 0

    for city, (lat, lon) in CITIES.items():
        for md in unique_dates:
            for temp_type in ("high", "low"):
                get_climo_samples(city, md, temp_type, lat, lon)
                done += 1
                if done % 20 == 0:
                    print(f"  {done}/{total} — {city} {md} {temp_type}")

    print(f"Klaar — {done} combinaties gecached.")
