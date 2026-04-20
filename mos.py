"""
mos.py — NOAA GFS-MOS temperatuurprognose per vliegveld.

MOS (Model Output Statistics) is statistisch gecorrigeerde GFS-output,
gekalibreerd op historische METAR-metingen per vliegveld. Nauwkeuriger
dan ruwe modeloutput voor max/min temperatuur (~2-3°F foutmarge vs 4-5°F).

Bron: Iowa Environmental Mesonet MOS archive (gratis, geen API key)
Dekking: US vliegvelden + enkele internationale (CA, EU)
Update: 4x per dag (00Z, 06Z, 12Z, 18Z GFS runs)

Gebruik:
    from mos import get_mos_forecast
    fc = get_mos_forecast("miami", "2026-04-18")
    # {"max_f": 87, "min_f": 74, "max_c": 30.6, "min_c": 23.3, "source": "GFS-MOS"}
"""

import csv
import io
import logging
import math
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import requests

log = logging.getLogger("mos")

MESONET_URL = "https://mesonet.agron.iastate.edu/mos/csv.php"

# Stad → ICAO vliegveldcode
# MOS dekking: voornamelijk VS + Canada, beperkt Europa/Azië
CITY_TO_ICAO: dict[str, str] = {
    "miami":         "KMIA",
    "new york city": "KJFK",
    "new york":      "KJFK",
    "los angeles":   "KLAX",
    "chicago":       "KORD",
    "dallas":        "KDFW",
    "seattle":       "KSEA",
    "san francisco": "KSFO",
    "boston":        "KBOS",
    "denver":        "KDEN",
    "houston":       "KIAH",
    "atlanta":       "KATL",
    "toronto":       "CYYZ",
    "montreal":      "CYUL",
    "washington":    "KDCA",
    "phoenix":       "KPHX",
    "minneapolis":   "KMSP",
    "detroit":       "KDTW",
    "orlando":       "KMCO",
    "las vegas":     "KLAS",
    # Europa (beperkte MOS dekking via NAM)
    "london":        "EGLL",
    "amsterdam":     "EHAM",
    "paris":         "LFPG",
    "berlin":        "EDDB",
    "madrid":        "LEMD",
    "rome":          "LIRF",
    "zurich":        "LSZH",
    "vienna":        "LOWW",
    "warsaw":        "EPWA",
    "stockholm":     "ESSA",
    # Azië (minimale MOS dekking)
    "tokyo":         "RJTT",
    "seoul":         "RKSS",
    "beijing":       "ZBAA",
    "shanghai":      "ZSPD",
    "hong kong":     "VHHH",
    "singapore":     "WSSS",
}

# MOS typische foutmarge (°F) voor max/min temperatuur
# Bron: NWS MOS verification statistics
MOS_SIGMA_F = 2.8   # 1-sigma foutmarge ≈ 2.8°F voor dag-1/dag-2


def _latest_mos_runtime() -> str:
    """Geeft de meest recente GFS run terug (00Z/06Z/12Z/18Z)."""
    now = datetime.now(timezone.utc)
    # GFS runs: 00Z, 06Z, 12Z, 18Z — beschikbaar ~4h na run
    available_hours = [h for h in [0, 6, 12, 18] if now.hour >= h + 4]
    if not available_hours:
        # Gebruik gisteren 18Z
        yesterday = now - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d 18:00")
    run_hour = available_hours[-1]
    return now.strftime(f"%Y-%m-%d {run_hour:02d}:00")


def _fetch_mos_csv(icao: str, runtime: str) -> list[dict]:
    """
    Haalt MOS CSV op voor een vliegveld en GFS runtime.
    Retourneert lijst van rijen als dicts.
    """
    try:
        r = requests.get(
            MESONET_URL,
            params={"station": icao, "runtime": runtime, "model": "GFS"},
            timeout=10,
        )
        if r.status_code != 200:
            log.debug(f"MOS {icao}: HTTP {r.status_code}")
            return []
        reader = csv.DictReader(io.StringIO(r.text))
        return list(reader)
    except Exception as e:
        log.debug(f"MOS fetch fout {icao}: {e}")
        return []


def get_mos_forecast(city: str, date_str: str) -> Optional[dict]:
    """
    Haalt GFS-MOS max/min temperatuur op voor een stad op een specifieke datum.

    Args:
        city:     Stadsnaam (lowercase, bijv. "miami")
        date_str: Doeldatum "YYYY-MM-DD"

    Returns:
        dict met max_f, min_f, max_c, min_c, sigma_f, sigma_c, run_time
        of None als MOS niet beschikbaar voor deze stad/datum.
    """
    icao = CITY_TO_ICAO.get(city.lower())
    if not icao:
        return None  # stad niet in MOS dekking

    runtime = _latest_mos_runtime()
    rows = _fetch_mos_csv(icao, runtime)

    if not rows:
        # Probeer vorige run als fallback
        prev_time = datetime.strptime(runtime, "%Y-%m-%d %H:%M") - timedelta(hours=6)
        runtime = prev_time.strftime("%Y-%m-%d %H:%M")
        rows = _fetch_mos_csv(icao, runtime)

    if not rows:
        return None

    # Filter rijen voor doeldatum
    target = date_str  # "2026-04-18"
    day_rows = []
    for row in rows:
        ftime = row.get("ftime", "")
        if not ftime:
            continue
        # ftime formaat: "2026-04-18 15:00:00+00"
        if ftime[:10] == target:
            day_rows.append(row)

    if not day_rows:
        log.debug(f"MOS {icao}: geen data voor {target}")
        return None

    # Max temperatuur: hoogste tmp waarde overdag (06Z-21Z local ≈ 11Z-02Z UTC voor VS)
    # Gebruik ook n_x veld (daaglijkse max/min, staat bij 00Z en 12Z timestamps)
    temps_f = []
    max_nx = None
    min_nx = None

    for row in day_rows:
        tmp = row.get("tmp", "").strip()
        n_x = row.get("n_x", "").strip()

        if tmp and tmp.lstrip("-").isdigit():
            temps_f.append(int(tmp))

        if n_x and n_x.lstrip("-").isdigit():
            val = int(n_x)
            # n_x bij 12Z = nachtminimum, bij 00Z = dagmaximum
            ftime = row.get("ftime", "")
            hour = int(ftime[11:13]) if len(ftime) > 12 else 0
            if hour == 0:
                max_nx = val   # daaglijkse max
            elif hour == 12:
                min_nx = val   # nachtelijk minimum

    if not temps_f and max_nx is None:
        return None

    # Dagmaximum: n_x (meest betrouwbaar) of max van uurlijkse temps
    if max_nx is not None:
        max_f = max_nx
    elif temps_f:
        max_f = max(temps_f)
    else:
        return None

    # Dagminimum
    if min_nx is not None:
        min_f = min_nx
    elif temps_f:
        min_f = min(temps_f)
    else:
        min_f = max_f - 15  # ruwe schatting als niet beschikbaar

    def f_to_c(f: float) -> float:
        return round((f - 32) * 5 / 9, 1)

    result = {
        "max_f":    max_f,
        "min_f":    min_f,
        "max_c":    f_to_c(max_f),
        "min_c":    f_to_c(min_f),
        "sigma_f":  MOS_SIGMA_F,
        "sigma_c":  round(MOS_SIGMA_F * 5 / 9, 2),
        "run_time": runtime,
        "icao":     icao,
        "source":   "GFS-MOS",
    }
    log.info(f"MOS {icao} {target}: max={max_f}°F ({f_to_c(max_f)}°C) min={min_f}°F [run {runtime}]")
    return result


def mos_probability(mos: dict, parsed: dict) -> Optional[float]:
    """
    Berekent P(YES) op basis van GFS-MOS forecast.

    Gebruikt MOS max/min als center van normale verdeling,
    met gecalibreerde sigma (historisch ±2.8°F voor dag-1/dag-2).
    """
    temp_type = parsed.get("temp_type", "high")
    condition = parsed.get("condition", "between")
    unit      = parsed.get("unit", "F")

    # MOS center en sigma in de juiste eenheid
    if temp_type == "high":
        if unit == "F":
            center = float(mos["max_f"])
            sigma  = mos["sigma_f"]
        else:
            center = float(mos["max_c"])
            sigma  = mos["sigma_c"]
    else:  # low
        if unit == "F":
            center = float(mos["min_f"])
            sigma  = mos["sigma_f"]
        else:
            center = float(mos["min_c"])
            sigma  = mos["sigma_c"]

    lo = parsed["temp_low"]
    hi = parsed["temp_high"]

    def cdf(x: float) -> float:
        return 0.5 * (1 + math.erf((x - center) / (sigma * math.sqrt(2))))

    if condition == "above":
        prob = 1 - cdf(lo)
    elif condition == "below":
        prob = cdf(hi)
    else:  # between / exact
        prob = cdf(hi) - cdf(lo)

    return round(max(0.0, min(1.0, prob)), 3)


def blend_mos(model_prob: float, mos_prob: float, days_ahead: int) -> float:
    """
    Blend model-kans met MOS-kans.
    MOS krijgt meer gewicht bij kortere horizon (MOS is nauwkeuriger dichtbij).

    dag 1-2: 50% MOS (MOS is hier het nauwkeurigst)
    dag 3-4: 35% MOS
    dag 5+:  20% MOS (MOS heeft minder voorspelkracht ver vooruit)
    """
    if days_ahead <= 2:
        w_mos = 0.50
    elif days_ahead <= 4:
        w_mos = 0.35
    else:
        w_mos = 0.20

    return round((1 - w_mos) * model_prob + w_mos * mos_prob, 3)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    test_cases = [
        ("miami",         "2026-04-19", {"condition": "between", "temp_type": "high", "temp_low": 28.89, "temp_high": 29.44, "unit": "C"}),
        ("new york city", "2026-04-20", {"condition": "below",   "temp_type": "high", "temp_low": -999,  "temp_high": 25.0,  "unit": "C"}),
        ("chicago",       "2026-04-20", {"condition": "between", "temp_type": "high", "temp_low": 17.78, "temp_high": 18.33, "unit": "C"}),
        ("san francisco", "2026-04-21", {"condition": "between", "temp_type": "high", "temp_low": 16.67, "temp_high": 17.22, "unit": "C"}),
    ]

    print(f"\n{'Stad':<15} {'Datum':<10} {'MOS Max':>8} {'P(YES)':>8}  Conditie")
    print("-" * 60)
    for city, date_s, parsed in test_cases:
        fc = get_mos_forecast(city, date_s)
        if fc:
            prob = mos_probability(fc, parsed)
            unit = parsed["unit"]
            max_val = fc["max_c"] if unit == "C" else fc["max_f"]
            print(f"{city:<15} {date_s[5:]:<10} {max_val:>7.1f}° {prob*100:>7.1f}%  {parsed['condition']} [{fc['icao']}]")
        else:
            print(f"{city:<15} {date_s[5:]:<10} {'N/A':>8} {'N/A':>8}")
