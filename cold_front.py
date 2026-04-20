"""
cold_front.py — Koudefront detectie via Open-Meteo forecast.

Detecteert naderende koude/warme fronten op basis van:
  1. Drukdaling > 3 hPa/6h (naderende depressie of front)
  2. Drukstijging + temperatuurdaling (koud front passage)
  3. Windrotatie > 90° in 12h (backing/veering = frontpassage)
  4. Precipitatiespike gevolgd door afkoeling

Een actief front op de doeldatum verhoogt de onzekerheid in het
temperatuurmodel significant — de voorspelde max/min kan 5-10°F
afwijken afhankelijk van frontpassage-timing.

Gebruik:
    from cold_front import get_front_risk
    risk = get_front_risk("chicago", "2026-04-25", lat=41.88, lon=-87.63)
    # {"risk": 0.85, "type": "cold_front", "eta_hours": 6, "dp_max": -4.2}
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger("cold_front")

FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# Drempelwaarden voor frontdetectie
PRESSURE_DROP_THRESHOLD  = -3.0   # hPa/6h — naderende front/depressie
PRESSURE_RISE_THRESHOLD  =  3.0   # hPa/6h — frontpassage (achterkant)
TEMP_DROP_COLD_FRONT     = -3.0   # °C/6h — karakteristiek koude front
WIND_ROTATION_THRESHOLD  =  80.0  # graden/12h — frontale windrotatie
PRECIP_SPIKE_THRESHOLD   =  40    # % precipitatiekans = regen/front


def _fetch_forecast(lat: float, lon: float, timezone_str: str = "auto") -> Optional[dict]:
    """Haalt 7-daagse uurlijkse forecast op via Open-Meteo."""
    try:
        r = requests.get(
            FORECAST_API,
            params={
                "latitude":   lat,
                "longitude":  lon,
                "hourly":     "surface_pressure,wind_direction_10m,temperature_2m,precipitation_probability",
                "forecast_days": 7,
                "timezone":   timezone_str,
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.debug(f"ColdFront API fout: HTTP {r.status_code}")
            return None
        return r.json()
    except Exception as e:
        log.debug(f"ColdFront fetch fout: {e}")
        return None


def _wind_rotation(dir1: float, dir2: float) -> float:
    """Berekent absolute hoekrotatie tussen twee windrichtingen (0-180°)."""
    diff = abs(dir2 - dir1) % 360
    return diff if diff <= 180 else 360 - diff


def _analyze_fronts(data: dict, target_date: str) -> list[dict]:
    """
    Analyseert forecast data en retourneert lijst van gedetecteerde fronten.

    Elke front heeft: type, eta_hours (t.o.v. middernacht doeldatum),
    confidence, dp_6h, dt_6h, wind_rotation
    """
    h = data.get("hourly", {})
    times   = h.get("time", [])
    pressure= h.get("surface_pressure", [])
    temp    = h.get("temperature_2m", [])
    wind_d  = h.get("wind_direction_10m", [])
    precip  = h.get("precipitation_probability", [])

    if not times:
        return []

    # Bepaal tijdvenster: 24h voor t/m 24h na doeldatum
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    window_start = target_dt - timedelta(hours=24)
    window_end   = target_dt + timedelta(hours=48)

    fronts = []
    n = len(times)

    for i in range(6, n - 1):
        # Parse timestamp (formaat: "2026-04-19T06:00")
        try:
            ts = datetime.strptime(times[i], "%Y-%m-%dT%H:%M")
        except ValueError:
            continue

        if ts < window_start or ts > window_end:
            continue

        # Uren t.o.v. middernacht doeldatum
        eta_hours = (ts - target_dt).total_seconds() / 3600

        dp6  = pressure[i] - pressure[i - 6]   # drukverandering per 6h
        dt6  = temp[i]     - temp[i - 6]        # temperatuurverandering per 6h
        wr12 = _wind_rotation(wind_d[max(0, i-12)], wind_d[i]) if i >= 12 else 0

        # --- KOUD FRONT: drukstijging + sterke temperatuurdaling ---
        if dp6 >= PRESSURE_RISE_THRESHOLD and dt6 <= TEMP_DROP_COLD_FRONT:
            confidence = min(1.0, (dp6 / 5.0 + abs(dt6) / 6.0) / 2.0)
            fronts.append({
                "type":       "cold_front",
                "ts":         times[i],
                "eta_hours":  eta_hours,
                "confidence": round(confidence, 2),
                "dp_6h":      round(dp6, 1),
                "dt_6h":      round(dt6, 1),
                "wind_rotation": round(wr12, 0),
            })

        # --- NADEREND FRONT: snelle drukdaling ---
        elif dp6 <= PRESSURE_DROP_THRESHOLD:
            precip_val = precip[i] if precip else 0
            confidence = min(1.0, (abs(dp6) / 6.0) * (1 + precip_val / 100))
            fronts.append({
                "type":       "approaching_front",
                "ts":         times[i],
                "eta_hours":  eta_hours,
                "confidence": round(confidence, 2),
                "dp_6h":      round(dp6, 1),
                "dt_6h":      round(dt6, 1),
                "wind_rotation": round(wr12, 0),
            })

        # --- WINDROTATIE: frontpassage via wind veering ---
        elif wr12 >= WIND_ROTATION_THRESHOLD and abs(dp6) > 1.5:
            confidence = min(0.7, wr12 / 180.0)
            fronts.append({
                "type":       "wind_shift",
                "ts":         times[i],
                "eta_hours":  eta_hours,
                "confidence": round(confidence, 2),
                "dp_6h":      round(dp6, 1),
                "dt_6h":      round(dt6, 1),
                "wind_rotation": round(wr12, 0),
            })

    return fronts


def get_front_risk(
    city: str,
    date_str: str,
    lat: float,
    lon: float,
    timezone_str: str = "auto",
) -> Optional[dict]:
    """
    Berekent frontrisico voor een stad op een specifieke datum.

    Returns dict met:
      risk        : 0.0–1.0 (hogere waarde = meer onzekerheid door fronten)
      type        : "cold_front" | "approaching_front" | "wind_shift" | "clear"
      fronts      : lijst van gedetecteerde frontgebeurtenissen
      sigma_extra : extra onzekerheid °C die bij MOS/model sigma opgeteld kan worden

    Het risico beïnvloedt de positiegrootte — hoog frontrisico = kleinere positie.
    """
    data = _fetch_forecast(lat, lon, timezone_str)
    if data is None:
        return None

    fronts = _analyze_fronts(data, date_str)

    if not fronts:
        return {
            "risk":        0.0,
            "type":        "clear",
            "fronts":      [],
            "sigma_extra": 0.0,
        }

    # Maximale confidence van fronten die de doeldatum raken (eta -12h t/m +24h)
    day_fronts = [f for f in fronts if -12 <= f["eta_hours"] <= 24]
    if not day_fronts:
        # Fronten buiten het venster → lager risico
        day_fronts = fronts[:1]

    max_conf = max(f["confidence"] for f in day_fronts)
    best_front = max(day_fronts, key=lambda f: f["confidence"])

    # Sigma-verhoging: koude fronten kunnen max temp 3-8°C verschuiven
    # Wij vertalen dat naar extra sigma voor de kansberekening
    if best_front["type"] == "cold_front":
        sigma_extra = round(max_conf * 3.0, 1)  # max +3°C extra sigma
    elif best_front["type"] == "approaching_front":
        sigma_extra = round(max_conf * 2.0, 1)  # max +2°C
    else:
        sigma_extra = round(max_conf * 1.0, 1)  # windshift: max +1°C

    log.info(
        f"ColdFront {city} {date_str}: risk={max_conf:.2f} type={best_front['type']} "
        f"eta={best_front['eta_hours']:+.0f}h dp={best_front['dp_6h']}hPa dt={best_front['dt_6h']}°C"
    )

    return {
        "risk":        round(max_conf, 2),
        "type":        best_front["type"],
        "fronts":      day_fronts,
        "sigma_extra": sigma_extra,
    }


def apply_front_risk(
    model_prob: float,
    poly_price: float,
    front_risk: Optional[dict],
    parsed: dict,
) -> float:
    """
    Past frontrisico toe op model-kans door de kans richting 0.5 te trekken
    (hogere onzekerheid = meer naar de prior van 50%).

    Alleen actief als risk >= 0.4 (significant front).
    """
    if front_risk is None or front_risk["risk"] < 0.40:
        return model_prob

    risk = front_risk["risk"]

    # Trekfactor: bij risk=1.0 → 40% richting prior (0.5)
    pull = risk * 0.40
    adjusted = model_prob * (1 - pull) + 0.5 * pull

    log.debug(
        f"FrontRisk {front_risk['type']}: risk={risk:.2f} "
        f"model={model_prob:.3f} → adjusted={adjusted:.3f}"
    )
    return round(adjusted, 3)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    from datetime import date, timedelta

    test_cities = [
        ("chicago",       41.88,  -87.63),
        ("dallas",        32.90,  -97.04),
        ("new york city", 40.71,  -74.01),
        ("miami",         25.77,  -80.19),
        ("seattle",       47.61, -122.33),
    ]

    today = date.today()
    dates = [(today + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(1, 6)]

    print(f"\n{'Stad':<18} {'Datum':<12} {'Risk':>6} {'Type':<20} {'σ+':>5}")
    print("-" * 65)

    for city, lat, lon in test_cities:
        for d in dates[:3]:  # test 3 dagen
            result = get_front_risk(city, d, lat, lon)
            if result:
                print(
                    f"{city:<18} {d:<12} {result['risk']:>5.2f}  "
                    f"{result['type']:<20} +{result['sigma_extra']:.1f}°C"
                )
