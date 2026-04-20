"""
Multi-source weerdata module.

Bronnen per prioriteit:
  1. METAR (aviationweather.gov) — directe luchthavenmeting, geen model
  2. TAF  (aviationweather.gov) — meteoroloog-geschreven forecast 24-30h
  3. NWS  (api.weather.gov)     — officieel VS overheidsmodel (alleen VS)
  4. ECMWF via Open-Meteo      — Europees weercentrum (globaal, beste algemeen)
  5. GFS  via Open-Meteo        — Amerikaans model (NOAA)
  6. ICON via Open-Meteo        — Duits model (hoge resolutie Europa)
  7. KNMI via Open-Meteo        — Nederlands model (hoge resolutie NL/EU)
  8. UK Met Office via Open-Meteo — Brits model (sterk globaal)
  9. Météo-France via Open-Meteo  — Frans model (EU/Afrika/tropen)
 10. JMA  via Open-Meteo        — Japans model (Oost-Azië)
 11. BOM  via Open-Meteo        — Australisch model (Oceanië)
 12. CMA  via Open-Meteo        — Chinees model (Oost-Azië)
 13. Tomorrow.io               — ML-model (nauwkeurigst, gratis tier 500/dag)
 14. Synoptic Data             — 100.000+ actuele weerstations wereldwijd

Gratis bronnen: 1-12 vereisen geen API key.
Tomorrow.io: TOMORROW_API_KEY in .env (gratis via tomorrow.io/signup)
Synoptic Data: SYNOPTIC_TOKEN in .env (gratis via synopticdata.com)
"""
import os
import json as _json
import pathlib as _pathlib
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

OPENMETEO = "https://api.open-meteo.com/v1/forecast"
AWC_BASE  = "https://aviationweather.gov/api/data"

# Stad → (ICAO code, lat, lon)
CITY_META = {
    "amsterdam":     ("EHAM", 52.374,  4.890),
    "ankara":        ("LTAC", 39.920, 32.854),
    "athens":        ("LGAV", 37.936, 23.944),
    "austin":        ("KAUS", 30.198, -97.670),
    "bangkok":       ("VTBS", 13.681, 100.747),
    "barcelona":     ("LEBL", 41.297,  2.078),
    "beijing":       ("ZBAA", 40.080, 116.585),
    "berlin":        ("EDDB", 52.362, 13.500),
    "buenos aires":  ("SAEZ", -34.822, -58.535),
    "cairo":         ("HECA", 30.122,  31.406),
    "chengdu":       ("ZUUU", 30.578, 103.947),
    "chicago":       ("KORD", 41.978, -87.905),
    "dallas":        ("KDAL", 32.847, -96.852),   # Polymarket: Love Field, niet DFW
    "denver":        ("KBKF", 39.717,-104.752),   # Polymarket: Buckley AFB Aurora, niet DEN
    "dubai":         ("OMDB", 25.252,  55.364),
    "helsinki":      ("EFHK", 60.317,  24.963),
    "hong kong":     ("VHHH", 22.309, 113.915),
    "istanbul":      ("LTFM", 41.275,  28.752),
    "jakarta":       ("WIII", -6.126, 106.656),
    "johannesburg":  ("FAOR",-26.139,  28.246),
    "karachi":       ("OPKC", 24.906,  67.161),
    "kuala lumpur":  ("WMKK",  2.745, 101.710),
    "lagos":         ("DNMM",  6.577,   3.321),
    "lima":          ("SPJC",-11.975, -77.114),
    "houston":       ("KHOU", 29.645, -95.279),    # Polymarket: Hobby Airport, niet IAH
    "london":        ("EGLC", 51.505,   0.055),    # Polymarket: City Airport, niet Heathrow
    "los angeles":   ("KLAX", 33.943,-118.408),
    "lucknow":       ("VILK", 26.760,  80.889),
    "madrid":        ("LEMD", 40.472,  -3.561),
    "miami":         ("KMIA", 25.796, -80.287),
    "milan":         ("LIMC", 45.630,   8.723),
    "montreal":      ("CYUL", 45.470, -73.740),
    "moscow":        ("UUEE", 55.972,  37.415),
    "mumbai":        ("VABB", 19.089,  72.868),
    "munich":        ("EDDM", 48.354,  11.786),
    "nairobi":       ("HKJK", -1.319,  36.925),
    "new york":      ("KLGA", 40.777, -73.873),    # Polymarket: LaGuardia, niet JFK
    "new york city": ("KLGA", 40.777, -73.873),    # Polymarket: LaGuardia, niet JFK
    "oslo":          ("ENGM", 60.194,  11.100),
    "panama city":   ("MPTO",  9.071, -79.383),
    "paris":         ("LFPB", 48.969,   2.441),    # Polymarket: Le Bourget, niet CDG
    "rome":          ("LIRF", 41.800,  12.239),
    "san francisco": ("KSFO", 37.619,-122.375),
    "santiago":      ("SCEL",-33.393, -70.786),
    "sao paulo":     ("SBGR",-23.435, -46.473),
    "seoul":         ("RKSS", 37.558, 126.791),   # Polymarket: Incheon, niet Gimpo
    "shanghai":      ("ZSPD", 31.144, 121.805),
    "singapore":     ("WSSS",  1.350, 103.994),
    "stockholm":     ("ESSA", 59.652,  17.919),
    "sydney":        ("YSSY",-33.946, 151.177),
    "taipei":        ("RCTP", 25.077, 121.233),
    "tehran":        ("OIIE", 35.416,  51.152),
    "tokyo":         ("RJTT", 35.553, 139.781),
    "toronto":       ("CYYZ", 43.677, -79.631),
    "vienna":        ("LOWW", 48.110,  16.570),
    "warsaw":        ("EPWA", 52.166,  20.967),
    "zurich":        ("LSZH", 47.458,   8.548),
    "mexico city":   ("MMMX", 19.436, -99.072),
    "shenzhen":      ("ZGSZ", 22.640, 113.811),
    "tel aviv":      ("LLBG", 32.009,  34.887),
    "wellington":    ("NZWN",-41.327, 174.805),
    "busan":         ("RKPK", 35.179, 128.938),    # Polymarket: Gimhae International
}


def get_metar(city: str) -> Optional[dict]:
    """
    Actuele luchthavenmeting (geen model — echte sensor).
    Geeft: temp_c, dewpoint_c, wind_kt, wind_dir, obs_time
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    icao = meta[0]
    try:
        r = requests.get(
            f"{AWC_BASE}/metar",
            params={"ids": icao, "format": "json", "mostRecent": "true"},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        m = data[0]
        return {
            "source":    "METAR",
            "icao":      icao,
            "temp_c":    m.get("temp"),
            "dewp_c":    m.get("dewp"),
            "wind_kt":   m.get("wspd"),
            "wind_dir":  m.get("wdir"),
            "obs_time":  m.get("reportTime", ""),
            "raw":       m.get("rawOb", ""),
        }
    except Exception:
        return None


def get_metar_daymax(city: str) -> Optional[dict]:
    """
    Haalt de gemeten dagmax (en dagmin) op voor vandaag via uurlijkse METAR-history.

    Dit is dezelfde data die Wunderground toont — de officiële Polymarket resolutiebron.
    Na ~14h lokaal is de dagmax grotendeels zeker. Dit is de basis van de
    'METAR lock' strategie (~88% win rate als dagmax duidelijk buiten range ligt).

    Returns dict met:
      day_max_f / day_max_c : dagmax tot nu toe
      day_min_f / day_min_c : dagmin tot nu toe
      n_obs                 : aantal uurlijkse observaties
      last_obs_time         : tijdstip laatste meting (UTC)
      hours_since_midnight  : uren verstreken op lokale dag (schatting)
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    icao = meta[0]

    try:
        r = requests.get(
            f"{AWC_BASE}/metar",
            params={"ids": icao, "format": "json", "hours": 24},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None

        temps_f = []
        last_time = ""
        for obs in data:
            temp_c = obs.get("temp")
            if temp_c is not None:
                temps_f.append(temp_c * 9 / 5 + 32)
                t = obs.get("reportTime", "")
                if t > last_time:
                    last_time = t

        if not temps_f:
            return None

        day_max_f = max(temps_f)
        day_min_f = min(temps_f)

        return {
            "source":             "METAR-history",
            "icao":               icao,
            "day_max_f":          round(day_max_f, 1),
            "day_max_c":          round((day_max_f - 32) * 5 / 9, 1),
            "day_min_f":          round(day_min_f, 1),
            "day_min_c":          round((day_min_f - 32) * 5 / 9, 1),
            "n_obs":              len(temps_f),
            "last_obs_time":      last_time,
        }
    except Exception:
        return None


def get_taf_summary(city: str) -> Optional[dict]:
    """
    TAF-forecast van een human meteoroloog (24-30h vooruit).
    Geeft: wind_kt, visibility, clouds, valid_from, valid_to
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    icao = meta[0]
    try:
        r = requests.get(
            f"{AWC_BASE}/taf",
            params={"ids": icao, "format": "json"},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        taf  = data[0]
        fcst = taf.get("fcsts", [{}])[0]
        clouds = fcst.get("clouds", [])
        sky    = clouds[0].get("cover", "?") if clouds else "SKC"
        return {
            "source":     "TAF",
            "icao":       icao,
            "valid_from": taf.get("validTimeFrom"),
            "valid_to":   taf.get("validTimeTo"),
            "wind_kt":    fcst.get("wspd"),
            "wind_dir":   fcst.get("wdir"),
            "visibility": fcst.get("visib"),
            "sky":        sky,
            "wx":         fcst.get("wxString", ""),
        }
    except Exception:
        return None


def get_openmeteo(city: str, date: str, model: str = "best_match") -> Optional[dict]:
    """
    Open-Meteo forecast voor een datum.
    model: "best_match" (ECMWF), "gfs_seamless" (GFS), "icon_seamless" (ICON)
    Geeft: temp_max, temp_min, precip_mm, precip_pct, wind_max
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    _, lat, lon = meta
    try:
        r = requests.get(
            OPENMETEO,
            params={
                "latitude":      lat,
                "longitude":     lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max",
                "forecast_days": 16,
                "timezone":      "auto",
                "models":        model,
            },
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()["daily"]
        for i, d in enumerate(data["time"]):
            if d == date:
                return {
                    "source":     model,
                    "temp_max":   data["temperature_2m_max"][i],
                    "temp_min":   data["temperature_2m_min"][i],
                    "precip_mm":  data["precipitation_sum"][i] or 0.0,
                    "precip_pct": data["precipitation_probability_max"][i] or 0,
                    "wind_max":   data["windspeed_10m_max"][i] or 0.0,
                }
        return None
    except Exception:
        return None


def get_nws_forecast(city: str, date: str) -> Optional[dict]:
    """
    Officieel Amerikaans NWS-model (weather.gov) — alleen VS-steden.
    Nauwkeuriger dan ECMWF voor binnenlandse VS-locaties.
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    _, lat, lon = meta

    # Alleen VS
    if not (-125 < lon < -65 and 24 < lat < 50):
        return None

    try:
        # Stap 1: grid-locatie ophalen
        r1 = requests.get(
            f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
            headers={"User-Agent": "polymarket-bot/1.0"},
            timeout=8,
        )
        if r1.status_code != 200:
            return None
        props = r1.json()["properties"]
        forecast_url = props["forecast"]

        # Stap 2: dagelijkse forecast
        r2 = requests.get(forecast_url, headers={"User-Agent": "polymarket-bot/1.0"}, timeout=8)
        if r2.status_code != 200:
            return None

        periods = r2.json()["properties"]["periods"]
        target  = datetime.strptime(date, "%Y-%m-%d").date()

        day_temp_f  = None
        night_temp_f = None
        day_period  = None

        for p in periods:
            start = datetime.fromisoformat(p["startTime"]).date()
            if start != target:
                continue
            if p.get("isDaytime", True) and day_temp_f is None:
                day_temp_f = p["temperature"]
                day_period = p
            elif not p.get("isDaytime", True) and night_temp_f is None:
                night_temp_f = p["temperature"]

        if day_temp_f is None:
            return None

        temp_max_c = round((day_temp_f - 32) * 5 / 9, 1)
        temp_min_c = round((night_temp_f - 32) * 5 / 9, 1) if night_temp_f is not None else None

        return {
            "source":         "NWS",
            "temp_max":       temp_max_c,
            "temp_max_f":     day_temp_f,
            "temp_min":       temp_min_c,
            "temp_min_f":     night_temp_f,
            "short_forecast": day_period.get("shortForecast", "") if day_period else "",
            "wind_speed":     day_period.get("windSpeed", "") if day_period else "",
        }
    except Exception:
        return None


def get_synoptic(city: str) -> Optional[dict]:
    """
    Synoptic Data — actuele meting van dichtstbijzijnde weerstation (100k+ stations).
    Geeft de meest recente gemeten temperatuur, niet een modelvoorspelling.
    Vereist SYNOPTIC_TOKEN in .env.
    """
    token = os.getenv("SYNOPTIC_TOKEN", "")
    if not token:
        return None
    meta = CITY_META.get(city)
    if not meta:
        return None
    icao, lat, lon = meta
    try:
        r = requests.get(
            "https://api.synopticdata.com/v2/stations/latest",
            params={
                "token":   token,
                "radius":  f"{lat},{lon},50",  # 50km straal
                "vars":    "air_temp,wind_speed",
                "limit":   1,
                "units":   "metric",
                "output":  "json",
            },
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        stations = data.get("STATION", [])
        if not stations:
            return None
        obs = stations[0].get("OBSERVATIONS", {})
        temp = obs.get("air_temp_value_1", {})
        temp_c = temp.get("value") if isinstance(temp, dict) else None
        if temp_c is None:
            return None
        return {
            "source":  "Synoptic",
            "temp_c":  round(float(temp_c), 1),
            "station": stations[0].get("NAME", ""),
            "obs_time": obs.get("air_temp_value_1", {}).get("date_time", "") if isinstance(temp, dict) else "",
        }
    except Exception:
        return None


def get_tomorrow_io(city: str, date: str) -> Optional[dict]:
    """
    Tomorrow.io ML-weermodel — nauwkeurigste gratis bron.
    Vereist TOMORROW_API_KEY in .env (gratis: tomorrow.io/signup, 500 calls/dag).
    """
    api_key = os.getenv("TOMORROW_API_KEY", "")
    if not api_key:
        return None
    meta = CITY_META.get(city)
    if not meta:
        return None
    _, lat, lon = meta
    try:
        r = requests.get(
            "https://api.tomorrow.io/v4/weather/forecast",
            params={
                "location": f"{lat},{lon}",
                "apikey":   api_key,
                "timesteps": "1d",
                "fields":   "temperatureMax,temperatureMin,precipitationProbability",
                "units":    "metric",
            },
            timeout=8,
        )
        if r.status_code != 200:
            return None
        timelines = r.json().get("timelines", {}).get("daily", [])
        for day in timelines:
            day_date = day.get("time", "")[:10]
            if day_date == date:
                v = day.get("values", {})
                return {
                    "source":     "Tomorrow.io",
                    "temp_max":   v.get("temperatureMax"),
                    "temp_min":   v.get("temperatureMin"),
                    "precip_pct": v.get("precipitationProbability", 0),
                }
        return None
    except Exception:
        return None


# MOS cache helpers — gedefinieerd voor get_mos_bias
_MOS_CACHE_FILE = _pathlib.Path(__file__).parent / "data" / "mos_cache.json"


def _load_mos_cache() -> dict:
    _pathlib.Path(_MOS_CACHE_FILE.parent).mkdir(exist_ok=True)
    if _MOS_CACHE_FILE.exists():
        try:
            return _json.loads(_MOS_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_mos_cache(cache: dict):
    try:
        _MOS_CACHE_FILE.write_text(_json.dumps(cache, indent=2))
    except Exception:
        pass


_mos_cache: dict = _load_mos_cache()


def get_seasonal_prob(city: str, date: str, threshold_c: float, condition: str) -> Optional[float]:
    """
    Berekent P(boven/onder drempel) direct vanuit 50-member seasonal ensemble.

    Gebruik alleen voor markten die 8+ dagen vooruit liggen — dan verliezen
    deterministische modellen hun voorspelkracht en geeft het ensemble
    een betrouwbaardere klimaat-prior.

    condition: "above" of "below"
    Returns: kans 0.0-1.0 of None bij fout
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    _, lat, lon = meta

    try:
        r = requests.get(
            "https://seasonal-api.open-meteo.com/v1/seasonal",
            params={
                "latitude":      lat,
                "longitude":     lon,
                "daily":         "temperature_2m_max",
                "forecast_days": 35,
                "timezone":      "auto",
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None

        data  = r.json().get("daily", {})
        times = data.get("time", [])
        if date not in times:
            return None

        idx = times.index(date)
        member_keys = [k for k in data if "temperature_2m_max" in k and k != "temperature_2m_max"]
        vals = [data[k][idx] for k in member_keys if data[k][idx] is not None]

        if len(vals) < 10:
            return None

        if condition == "above":
            hits = sum(1 for v in vals if v > threshold_c)
        else:
            hits = sum(1 for v in vals if v < threshold_c)

        return round(hits / len(vals), 3)

    except Exception:
        return None


def get_mos_bias(city: str, month: int, lat: float, lon: float) -> float:
    """
    Model Output Statistics bias-correctie.

    Haalt ERA5 historische data op voor dezelfde maand in de laatste 5 jaar
    en berekent de gemiddelde afwijking van het actuele ECMWF ensemble t.o.v.
    ERA5 reanalysis (de 'waarheid').

    Returns: bias in °C (positief = model te warm, negatief = te koud)
    Cached per stad/maand zodat we niet elke trade opnieuw hoeven te fetchen.
    """
    cache_key = f"{city}_{month}"
    if cache_key in _mos_cache:
        return _mos_cache[cache_key]

    from datetime import date as _date
    import math

    biases = []
    current_year = _date.today().year

    for year in range(current_year - 5, current_year):
        # Haal ERA5 reanalysis op voor dezelfde maand (eerste 10 dagen als steekproef)
        start = f"{year}-{month:02d}-01"
        # Bepaal einde van maand
        if month == 12:
            end = f"{year}-12-10"
        else:
            end = f"{year}-{month:02d}-10"

        try:
            # ERA5 = beste beschikbare historische reanalysis (de 'waarheid')
            r_era5 = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude":   lat,
                    "longitude":  lon,
                    "start_date": start,
                    "end_date":   end,
                    "daily":      "temperature_2m_max",
                    "timezone":   "auto",
                    "models":     "era5",
                },
                timeout=8,
            )
            if r_era5.status_code != 200:
                continue
            era5_temps = r_era5.json().get("daily", {}).get("temperature_2m_max", [])

            # ECMWF reanalysis voor dezelfde periode (wat het model zou zeggen)
            r_ecmwf = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude":   lat,
                    "longitude":  lon,
                    "start_date": start,
                    "end_date":   end,
                    "daily":      "temperature_2m_max",
                    "timezone":   "auto",
                    "models":     "ecmwf_ifs",
                },
                timeout=8,
            )
            if r_ecmwf.status_code != 200:
                continue
            ecmwf_temps = r_ecmwf.json().get("daily", {}).get("temperature_2m_max", [])

            # Bereken gemiddelde bias over de beschikbare datums
            for era, ecm in zip(era5_temps, ecmwf_temps):
                if era is not None and ecm is not None:
                    biases.append(ecm - era)  # positief = model te warm

        except Exception:
            continue

    if not biases:
        _mos_cache[cache_key] = 0.0
        _save_mos_cache(_mos_cache)
        return 0.0

    bias = round(sum(biases) / len(biases), 2)
    _mos_cache[cache_key] = bias
    _save_mos_cache(_mos_cache)
    return bias


_ENSEMBLE_CACHE_FILE = _pathlib.Path(__file__).parent / "data" / "ensemble_cache.json"
_ENS_CACHE_TTL_HOURS = 6   # Ensemble modellen updaten elke 6h


def _load_ensemble_cache() -> dict:
    try:
        if _ENSEMBLE_CACHE_FILE.exists():
            return _json.loads(_ENSEMBLE_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_ensemble_cache(cache: dict):
    try:
        _pathlib.Path(_ENSEMBLE_CACHE_FILE.parent).mkdir(exist_ok=True)
        _ENSEMBLE_CACHE_FILE.write_text(_json.dumps(cache))
    except Exception:
        pass


def _fetch_ensemble_members(lat: float, lon: float, date: str, model: str) -> Optional[list[float]]:
    """
    Haalt alle ensemble member dagmaxima op voor één model/locatie/datum.
    Retourneert gesorteerde lijst van temperaturen (°C) of None bij fout.
    """
    try:
        r = requests.get(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            params={
                "latitude":      lat,
                "longitude":     lon,
                "daily":         "temperature_2m_max,temperature_2m_min",
                "forecast_days": 16,
                "timezone":      "auto",
                "models":        model,
            },
            timeout=12,
        )
        if r.status_code == 429:
            return None  # daily rate limit — gebruik cache
        if r.status_code != 200:
            return None

        data = r.json().get("daily", {})
        times = data.get("time", [])
        if date not in times:
            return None
        idx = times.index(date)

        max_members, min_members = [], []
        for key, vals in data.items():
            if not isinstance(vals, list) or idx >= len(vals) or vals[idx] is None:
                continue
            if "temperature_2m_max_member" in key:
                max_members.append(vals[idx])
            elif "temperature_2m_min_member" in key:
                min_members.append(vals[idx])

        if len(max_members) < 10:
            return None

        return {"max": sorted(max_members), "min": sorted(min_members) if min_members else []}
    except Exception:
        return None


def get_ensemble_data(city: str, date: str) -> Optional[dict]:
    """
    Haalt gecachede ensemble data op voor stad/datum.

    Cache TTL: 6 uur (ensemble modellen updaten elke 6h).
    Probeert ECMWF ENS (51 members) eerst, dan ICON (40), GFS (31), GEM (21).

    Returns dict met:
      max_members  : gesorteerde lijst dagmax per ensemble member (°C)
      min_members  : gesorteerde lijst dagmin per ensemble member (°C)
      spread       : P10-P90 spread dagmax (°C) — dynamische sigma
      p10/p50/p90  : percentielen dagmax
      n_members    : aantal ensemble members
      model        : welk model gebruikt
    """
    meta = CITY_META.get(city)
    if not meta:
        return None
    _, lat, lon = meta

    cache_key = f"{city}:{date}"
    cache = _load_ensemble_cache()

    # Cache hit: controleer of data nog vers is (< TTL uur oud)
    if cache_key in cache:
        entry = cache[cache_key]
        cached_at = entry.get("cached_at", "")
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)).total_seconds() / 3600
            if age_h < _ENS_CACHE_TTL_HOURS:
                return entry.get("data")
        except Exception:
            pass

    # ECMWF ENS eerst (51 members, meest betrouwbaar), dan fallbacks
    models_to_try = ["ecmwf_ifs025", "icon_seamless", "gfs025", "gem_global"]
    result = None

    for model in models_to_try:
        members = _fetch_ensemble_members(lat, lon, date, model)
        if members and len(members.get("max", [])) >= 10:
            mx = members["max"]
            mn = members["min"]
            n  = len(mx)
            p10 = mx[int(n * 0.10)]
            p50 = mx[int(n * 0.50)]
            p90 = mx[int(n * 0.90)]
            result = {
                "max_members": mx,
                "min_members": mn,
                "spread":      round(p90 - p10, 2),
                "p10":         round(p10, 1),
                "p50":         round(p50, 1),
                "p90":         round(p90, 1),
                "n_members":   n,
                "model":       model,
            }
            break

    # Sla op in cache (ook bij None zodat we rate-limited requests niet herhalen)
    cache[cache_key] = {
        "data":      result,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_ensemble_cache(cache)
    return result


def ensemble_probability(
    city: str,
    date: str,
    temp_low_c: float,
    temp_high_c: float,
    condition: str,
    temp_type: str = "high",
) -> Optional[float]:
    """
    Berekent P(YES) direct uit ensemble member verdeling.

    In plaats van: consensus → normale verdeling → P(YES)
    Doet dit:      tel members die de conditie voldoen / totaal members

    Dit is de meest directe en nauwkeurige methode — geen aanname van
    normale verdeling, geen handmatige sigma-kalibratie.

    Args:
        temp_low_c  : ondergrens in °C (gebruik -999 voor 'above' condities)
        temp_high_c : bovengrens in °C (gebruik 999 voor 'below' condities)
        condition   : "between", "above", "below"
        temp_type   : "high" (dagmax) of "low" (dagmin)

    Returns: P(YES) als float 0.0–1.0, of None als geen ensemble data
    """
    ens = get_ensemble_data(city, date)
    if ens is None:
        return None

    members = ens["max_members"] if temp_type == "high" else ens.get("min_members", [])
    if not members:
        return None

    n = len(members)
    if condition == "above":
        hits = sum(1 for m in members if m >= temp_low_c)
    elif condition == "below":
        hits = sum(1 for m in members if m <= temp_high_c)
    else:  # between
        hits = sum(1 for m in members if temp_low_c <= m <= temp_high_c)

    return round(hits / n, 3)


def get_ensemble_spread(city: str, date: str) -> Optional[float]:
    """
    Backwards-compatible wrapper: retourneert P10-P90 spread uit ensemble data.
    Gebruikt get_ensemble_data() met caching intern.
    """
    ens = get_ensemble_data(city, date)
    return ens["spread"] if ens else None


def multi_source_forecast(city: str, date: str) -> dict:
    """
    Haalt forecasts op van alle beschikbare bronnen en berekent consensus.

    Returns:
      sources:    dict per bron met temp_max
      consensus:  gewogen gemiddelde
      spread:     standaarddeviatie (onzekerheid)
      confidence: "HOOG" / "MATIG" / "LAAG"
      agreement:  True als alle modellen < 2°C afwijken van consensus
    """
    results   = {}
    temps     = []
    weights   = []

    _, lat, lon = CITY_META.get(city, (None, 0, 0))

    # ── Globale modellen (altijd) ────────────────────────────────────────────
    ecmwf = get_openmeteo(city, date, "best_match")
    if ecmwf and ecmwf["temp_max"] is not None:
        results["ECMWF"] = ecmwf; temps.append(ecmwf["temp_max"]); weights.append(3)

    gfs = get_openmeteo(city, date, "gfs_seamless")
    if gfs and gfs["temp_max"] is not None:
        results["GFS"] = gfs; temps.append(gfs["temp_max"]); weights.append(2)

    # UK Met Office — sterk globaal model
    ukmo = get_openmeteo(city, date, "ukmo_seamless")
    if ukmo and ukmo["temp_max"] is not None:
        results["UKMO"] = ukmo; temps.append(ukmo["temp_max"]); weights.append(3)

    # ── Regionale specialisten ────────────────────────────────────────────────
    # Europa / Afrika
    if -30 < lon < 50:
        icon = get_openmeteo(city, date, "icon_seamless")
        if icon and icon["temp_max"] is not None:
            results["ICON"] = icon; temps.append(icon["temp_max"]); weights.append(3)

        mf = get_openmeteo(city, date, "meteofrance_seamless")
        if mf and mf["temp_max"] is not None:
            results["MeteoFrance"] = mf; temps.append(mf["temp_max"]); weights.append(2)

        # KNMI alleen voor NL/EU hoge resolutie
        if 35 < lat < 72:
            knmi = get_openmeteo(city, date, "knmi_seamless")
            if knmi and knmi["temp_max"] is not None:
                results["KNMI"] = knmi; temps.append(knmi["temp_max"]); weights.append(3)

    # Noord-Amerika
    if -130 < lon < -60 and 15 < lat < 72:
        nws = get_nws_forecast(city, date)
        if nws and nws.get("temp_max") is not None:
            results["NWS"] = nws; temps.append(nws["temp_max"]); weights.append(4)

        gem = get_openmeteo(city, date, "gem_seamless")
        if gem and gem["temp_max"] is not None:
            results["GEM"] = gem; temps.append(gem["temp_max"]); weights.append(2)

    # Oost-Azië / Japan / China / Australië
    if lon > 60:
        jma = get_openmeteo(city, date, "jma_seamless")
        if jma and jma["temp_max"] is not None:
            results["JMA"] = jma; temps.append(jma["temp_max"]); weights.append(3)

        if lon > 100 and lat < 10:  # Oceanië
            bom = get_openmeteo(city, date, "bom_access_global")
            if bom and bom["temp_max"] is not None:
                results["BOM"] = bom; temps.append(bom["temp_max"]); weights.append(3)

        if 70 < lon < 135 and 15 < lat < 55:  # China
            cma = get_openmeteo(city, date, "cma_grapes_global")
            if cma and cma["temp_max"] is not None:
                results["CMA"] = cma; temps.append(cma["temp_max"]); weights.append(2)

    # ── Tomorrow.io (premium ML, indien API key aanwezig) ────────────────────
    tmrw = get_tomorrow_io(city, date)
    if tmrw and tmrw["temp_max"] is not None:
        results["Tomorrow.io"] = tmrw; temps.append(tmrw["temp_max"]); weights.append(5)

    # ── Synoptic Data (actuele meting — alleen VS stations) ───────────────────
    if -130 < lon < -60 and 15 < lat < 72:
        synoptic = get_synoptic(city)
        if synoptic and synoptic.get("temp_c") is not None:
            results["Synoptic"] = synoptic; temps.append(synoptic["temp_c"]); weights.append(4)

    if not temps:
        return {"error": "Geen weerdata beschikbaar"}

    # Gewogen gemiddelde
    import math
    total_w   = sum(weights)
    consensus = sum(t * w for t, w in zip(temps, weights)) / total_w

    # ── Ensemble spread (primair) vs inter-model spread (fallback) ───────────
    ensemble_spread = get_ensemble_spread(city, date)
    if ensemble_spread is not None:
        # P10-P90 van 51 ECMWF ensemble members — beste onzekerheidsmaat
        spread = ensemble_spread
        spread_source = "ensemble"
    else:
        # Fallback: gewogen standaarddeviatie tussen modellen
        spread = math.sqrt(sum(w * (t - consensus)**2 for t, w in zip(temps, weights)) / total_w)
        spread_source = "inter-model"

    # Overeenkomst: alle modellen binnen 2°C van consensus
    agreement = all(abs(t - consensus) < 2.0 for t in temps)

    # P10-P90 is groter dan sigma — drempelwaarden zijn hierop aangepast
    if spread_source == "ensemble":
        if spread < 3.0:   confidence = "HOOG"
        elif spread < 6.0: confidence = "MATIG"
        else:              confidence = "LAAG"
    else:
        if spread < 1.0:   confidence = "HOOG"
        elif spread < 2.5: confidence = "MATIG"
        else:              confidence = "LAAG"

    # ── MOS bias-correctie ───────────────────────────────────────────────────
    # Corrigeer de consensus voor bekende systematische modelfouten per stad/maand.
    # Alleen toepassen als er voldoende bronnen zijn (stabiele consensus).
    mos_bias = 0.0
    if len(temps) >= 3 and lat != 0:
        try:
            target_month = int(date[5:7])
            mos_bias = get_mos_bias(city, target_month, lat, lon)
            consensus_corrected = round(consensus - mos_bias, 1)
        except Exception:
            consensus_corrected = round(consensus, 1)
    else:
        consensus_corrected = round(consensus, 1)

    return {
        "sources":              results,
        "consensus":            consensus_corrected,   # MOS-gecorrigeerd
        "consensus_raw":        round(consensus, 1),   # origineel voor debug
        "spread":               round(spread, 2),
        "spread_source":        spread_source,         # "ensemble" of "inter-model"
        "confidence":           confidence,
        "agreement":            agreement,
        "n_sources":            len(temps),
        "mos_bias":             round(mos_bias, 2),
    }


_FORECAST_CACHE_FILE = _pathlib.Path(__file__).parent / "data" / "forecast_cache.json"


def _load_forecast_cache() -> dict:
    _pathlib.Path(_FORECAST_CACHE_FILE.parent).mkdir(exist_ok=True)
    if _FORECAST_CACHE_FILE.exists():
        try:
            return _json.loads(_FORECAST_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_forecast_cache(cache: dict):
    try:
        _FORECAST_CACHE_FILE.write_text(_json.dumps(cache, indent=2))
    except Exception:
        pass


def detect_model_shift(city: str, date: str) -> Optional[dict]:
    """
    Detecteert significante verschuivingen in het weermodel t.o.v. 6+ uur geleden.

    ECMWF draait om 00:00 en 12:00 UTC, GFS om 00:00, 06:00, 12:00, 18:00 UTC.
    Als een nieuwe run >= 1.5°C afwijkt van de gecachede run, is dit een handelskans
    vóórdat de markt heeft kunnen repricen.

    Returns: {"shift": 1.8, "direction": "warmer", "prev_consensus": 12.4, "curr_consensus": 14.2}
             of None als er geen significante shift is (of geen vorige data).
    """
    cache = _load_forecast_cache()
    cache_key = f"{city.lower()}_{date}"

    now_ts = datetime.now(timezone.utc)
    now_str = now_ts.strftime("%Y-%m-%dT%H:%MZ")

    # Haal huidig consensus op
    fc = multi_source_forecast(city.lower(), date)
    curr_consensus = None
    if fc and "error" not in fc:
        curr_consensus = fc.get("consensus")

    result = None

    if curr_consensus is not None:
        prev_entry = cache.get(cache_key)

        if prev_entry:
            prev_ts_str = prev_entry.get("ts", "")
            prev_consensus = prev_entry.get("consensus")

            # Controleer of de vorige entry 6+ uur geleden is
            try:
                prev_dt = datetime.fromisoformat(prev_ts_str.replace("Z", "+00:00"))
                age_hours = (now_ts - prev_dt).total_seconds() / 3600
            except Exception:
                age_hours = 0

            if age_hours >= 6 and prev_consensus is not None:
                shift = curr_consensus - prev_consensus
                if abs(shift) >= 1.5:
                    result = {
                        "shift":          round(abs(shift), 2),
                        "direction":      "warmer" if shift > 0 else "kouder",
                        "prev_consensus": round(prev_consensus, 1),
                        "curr_consensus": round(curr_consensus, 1),
                    }

        # Update cache met huidige waarde
        cache[cache_key] = {"ts": now_str, "consensus": curr_consensus}
        _save_forecast_cache(cache)

    return result


if __name__ == "__main__":
    print("── Multi-source weertest ─────────────────────────────────")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for city in ["amsterdam", "chicago", "miami", "tokyo"]:
        print(f"\n{city.upper()} — {today}")

        metar = get_metar(city)
        if metar:
            print(f"  METAR (actueel): {metar['temp_c']}°C  wind {metar['wind_kt']}kt")

        fc = multi_source_forecast(city, today)
        if "error" not in fc:
            print(f"  Consensus:  {fc['consensus']}°C  (raw: {fc['consensus_raw']}°C, MOS: {fc['mos_bias']:+.2f}°C)")
            print(f"  Spread:     ±{fc['spread']}°C  [{fc['spread_source']}]")
            print(f"  Betrouwbaarheid: {fc['confidence']}  — {fc['n_sources']} modellen")
            print(f"  Akkoord:    {'✓ ja' if fc['agreement'] else '✗ verdeeld'}")
            for src, data in fc["sources"].items():
                print(f"    {src:12} → {data.get('temp_max','?')}°C")
