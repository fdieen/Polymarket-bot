"""
position_monitor.py — Stop-loss en weer-aware early exit voor open posities.

Twee exit triggers:
  1. Prijs-stop: als positiewaarde >60% daalt t.o.v. entry → verkopen
     Beter $6 redden dan $15 verliezen (Miami-scenario)

  2. Weer-aware exit: op resolutiedag, als METAR-meting aantoont dat
     de uitkomst al vaststaat → direct verkopen voor volledige verlies

Run als standalone:
    python position_monitor.py           # eenmalig checken
    python position_monitor.py --watch   # elke 10 min herhalen

Of importeer in dashboard:
    from position_monitor import check_all_positions
"""

import re
import time
import logging
import requests
from datetime import datetime, timezone, date, timedelta
from typing import Optional

from portfolio import load_portfolio, sell_position
from weather_sources import get_metar, CITY_META

log = logging.getLogger("position_monitor")

CLOB = "https://clob.polymarket.com/markets"

# ── Stop-loss instellingen ─────────────────────────────────────────────────────

# Verlies >60% van entry → uitstappen
# Voorbeeld: NO @ $0.63 → exit als NO-prijs daalt naar $0.25
PRICE_STOP_LOSS_PCT = 0.60

# Weer-aware exit: binnen hoeveel graden van YES-range aanvaarden we het als zeker?
# Als METAR < TEMP_CERTAINTY_MARGIN van de rand van de range → aannnemen als goed
TEMP_CERTAINTY_MARGIN = 0.5  # °F of °C


# ── Marktprijs ophalen ────────────────────────────────────────────────────────

def fetch_current_price(condition_id: str, direction: str) -> Optional[float]:
    """Haal live prijs op voor onze positie (YES of NO)."""
    if not condition_id:
        return None
    try:
        r = requests.get(f"{CLOB}/{condition_id}", timeout=8)
        if r.status_code != 200:
            return None
        m = r.json()
        tokens = m.get("tokens", [])
        for tok in tokens:
            if tok.get("outcome", "").lower() == direction.lower():
                return float(tok["price"])
    except Exception:
        pass
    return None


# ── Vraag-parser: stad, temperatuur-range, eenheid ───────────────────────────

def parse_weather_question(question: str) -> Optional[dict]:
    """
    Parseer Polymarket weather-vraag naar bruikbare data.

    Voorbeelden:
      "Will the highest temperature in Miami be between 84-85°F on April 18?"
      "Will the highest temperature in Chicago be 62°F or higher on April 20?"
      "Will the lowest temperature in New York City be between 68-69°F on April 15?"
    """
    q = question.lower()

    # Stad
    city = None
    for c in CITY_META:
        if c in q:
            city = c
            break
    if not city:
        return None

    # Temperatuurtype
    temp_type = "high" if "highest" in q else ("low" if "lowest" in q else "high")

    # Eenheid
    unit = "F" if "°f" in q or "f on" in q or "°f on" in q else "C"

    # Resolutiedatum
    resolve_date = None
    date_match = re.search(
        r"on (january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d+)",
        q
    )
    if date_match:
        months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
                  "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
        m_num = months[date_match.group(1)]
        d_num = int(date_match.group(2))
        try:
            resolve_date = date(datetime.now().year, m_num, d_num)
        except ValueError:
            pass

    # Temperatuurbereik
    temp_range = None

    # "between X-Y"
    between = re.search(r"between\s+([\d.]+)[–\-]([\d.]+)", q)
    if between:
        temp_range = (float(between.group(1)), float(between.group(2)))

    # "X°F or higher" / "X or higher"
    higher = re.search(r"([\d.]+).*?or higher", q)
    if higher and not temp_range:
        lo = float(higher.group(1))
        temp_range = (lo, lo + 999)  # onbegrensd naar boven

    # "X°F or lower" / "X or lower"
    lower = re.search(r"([\d.]+).*?or (?:lower|below)", q)
    if lower and not temp_range:
        hi = float(lower.group(1))
        temp_range = (-999, hi)  # onbegrensd naar beneden

    # Exacte temp: "be 21°C on"
    exact = re.search(r"be ([\d.]+)(?:°[fc])? on", q)
    if exact and not temp_range:
        t = float(exact.group(1))
        temp_range = (t - 0.5, t + 0.5)

    if not temp_range:
        return None

    return {
        "city":        city,
        "temp_type":   temp_type,
        "unit":        unit,
        "temp_range":  temp_range,
        "resolve_date": resolve_date,
    }


OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

# Stad → (lat, lon, IANA timezone)
CITY_TIMEZONE: dict = {
    "miami":            (25.77, -80.19, "America/New_York"),
    "new york city":    (40.71, -74.01, "America/New_York"),
    "chicago":          (41.88, -87.63, "America/Chicago"),
    "los angeles":      (34.05, -118.24, "America/Los_Angeles"),
    "dallas":           (32.90, -97.04,  "America/Chicago"),
    "seattle":          (47.61, -122.33, "America/Los_Angeles"),
    "san francisco":    (37.77, -122.42, "America/Los_Angeles"),
    "boston":           (42.36, -71.06,  "America/New_York"),
    "denver":           (39.74, -104.98, "America/Denver"),
    "houston":          (29.76, -95.37,  "America/Chicago"),
    "atlanta":          (33.75, -84.39,  "America/New_York"),
    "phoenix":          (33.45, -112.07, "America/Phoenix"),
    "las vegas":        (36.17, -115.14, "America/Los_Angeles"),
    "minneapolis":      (44.98, -93.27,  "America/Chicago"),
    "detroit":          (42.33, -83.05,  "America/Detroit"),
    "orlando":          (28.54, -81.38,  "America/New_York"),
    "washington":       (38.91, -77.04,  "America/New_York"),
    "toronto":          (43.65, -79.38,  "America/Toronto"),
    "london":           (51.51, -0.13,   "Europe/London"),
    "amsterdam":        (52.37, 4.90,    "Europe/Amsterdam"),
    "paris":            (48.85, 2.35,    "Europe/Paris"),
    "berlin":           (52.52, 13.40,   "Europe/Berlin"),
    "tokyo":            (35.68, 139.69,  "Asia/Tokyo"),
    "seoul":            (37.57, 126.98,  "Asia/Seoul"),
    "singapore":        (1.35,  103.82,  "Asia/Singapore"),
    "hong kong":        (22.32, 114.17,  "Asia/Hong_Kong"),
    "bangkok":          (13.75, 100.52,  "Asia/Bangkok"),
    "kuala lumpur":     (3.14,  101.69,  "Asia/Kuala_Lumpur"),
    "busan":            (35.10, 129.04,  "Asia/Seoul"),
    "shanghai":         (31.23, 121.47,  "Asia/Shanghai"),
    "beijing":          (39.91, 116.39,  "Asia/Shanghai"),
}


def c_to_f(c: float) -> float:
    return c * 9/5 + 32


def get_current_temp(city: str, unit: str) -> Optional[float]:
    """Haal actuele temperatuur op via METAR."""
    metar = get_metar(city)
    if not metar or metar.get("temp_c") is None:
        return None
    temp_c = float(metar["temp_c"])
    return c_to_f(temp_c) if unit == "F" else temp_c


def get_intraday_tracking(city: str, resolve_date: date, unit: str) -> Optional[dict]:
    """
    Haalt uurlijkse temperatuurdata op voor de resolutiedag via Open-Meteo.

    Retourneert:
        day_max_so_far  : hoogste temp tot nu toe (in gevraagde eenheid)
        day_min_so_far  : laagste temp tot nu toe
        current_temp    : meest recente meting
        hours_elapsed   : uren verstreken op de dag
        hours_remaining : uren tot einde dag
        trend_per_hour  : trend van afgelopen 3h (°/h, positief = stijgend)
        projected_max   : geschatte dagmax (simpele extrapolatie)
        projected_min   : geschatte dagmin
    """
    city_lower = city.lower()
    tz_info = CITY_TIMEZONE.get(city_lower)
    if tz_info is None:
        # Fallback: gebruik CITY_META coords met auto timezone
        from weather_sources import CITY_META
        meta = CITY_META.get(city_lower)
        if meta is None:
            return None
        lat, lon = meta["lat"], meta["lon"]
        tz = "auto"
    else:
        lat, lon, tz = tz_info

    today_str    = resolve_date.isoformat()
    tomorrow_str = (resolve_date + timedelta(days=1)).isoformat()

    try:
        r = requests.get(
            OPEN_METEO_FORECAST,
            params={
                "latitude":     lat,
                "longitude":    lon,
                "hourly":       "temperature_2m",
                "past_days":    1,
                "forecast_days": 1,
                "timezone":     tz,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception as e:
        log.debug(f"IntraDay fetch fout {city}: {e}")
        return None

    h = data.get("hourly", {})
    times = h.get("time", [])
    temps_c = h.get("temperature_2m", [])

    # Filter voor resolutiedag
    day_temps_c = []
    for ts, tc in zip(times, temps_c):
        if ts and ts[:10] == today_str and tc is not None:
            day_temps_c.append((ts, tc))

    if not day_temps_c:
        return None

    # Bepaal hoe laat het nu is (lokale dag uren: 0-23)
    now_utc = datetime.now(timezone.utc)
    # Schat uren verstreken: gebruik het laatste timestamp in de data
    last_ts = day_temps_c[-1][0]  # "2026-04-19T15:00"
    last_hour = int(last_ts[11:13])
    hours_elapsed   = last_hour + 1  # uren verstreken (incl. huidig uur)
    hours_remaining = max(0, 24 - hours_elapsed)

    temps_only = [tc for _, tc in day_temps_c]
    day_max_c  = max(temps_only)
    day_min_c  = min(temps_only)
    current_c  = temps_only[-1]

    # Trend: verandering over afgelopen 3 meetpunten
    trend_c_per_h = 0.0
    if len(temps_only) >= 3:
        trend_c_per_h = (temps_only[-1] - temps_only[-3]) / 2.0

    # Projectie dagmax: als we nog stijgen en het is < 14h lokaal
    projected_max_c = day_max_c
    if last_hour < 14 and trend_c_per_h > 0:
        hours_to_peak = max(0, 14 - last_hour)
        projected_max_c = current_c + trend_c_per_h * hours_to_peak * 0.6  # conservatief

    # Projectie dagmin: als we 's nachts nog dalen
    projected_min_c = day_min_c
    if last_hour >= 20 or last_hour < 6:
        hours_to_dawn = (6 - last_hour) % 24
        if trend_c_per_h < 0 and hours_to_dawn > 0:
            projected_min_c = current_c + trend_c_per_h * hours_to_dawn * 0.5

    def conv(c: float) -> float:
        return c_to_f(c) if unit == "F" else c

    return {
        "day_max_so_far":  round(conv(day_max_c),      1),
        "day_min_so_far":  round(conv(day_min_c),      1),
        "current_temp":    round(conv(current_c),      1),
        "projected_max":   round(conv(projected_max_c), 1),
        "projected_min":   round(conv(projected_min_c), 1),
        "trend_per_hour":  round(trend_c_per_h * (9/5 if unit == "F" else 1.0), 2),
        "hours_elapsed":   hours_elapsed,
        "hours_remaining": hours_remaining,
        "last_hour_local": last_hour,
        "n_readings":      len(day_temps_c),
    }


# ── Prijs-stop check ──────────────────────────────────────────────────────────

def check_price_stop(pos: dict) -> Optional[str]:
    """
    Retourneert exit-reden als prijs-stop triggered, anders None.
    """
    entry = pos.get("entry_price", 0)
    current = pos.get("current_price", 0)
    if not entry or not current:
        return None

    direction = pos.get("direction", "YES")

    if direction == "NO":
        # NO positie verliest als de YES-prijs stijgt boven onze entry
        loss_pct = (current - entry) / entry
    else:
        # YES positie verliest als de prijs daalt onder onze entry
        loss_pct = (entry - current) / entry

    if loss_pct >= PRICE_STOP_LOSS_PCT:
        return (
            f"Prijs-stop ({direction}): entry ${entry:.2f} → nu ${current:.2f} "
            f"(verlies {loss_pct:.0%}, drempel {PRICE_STOP_LOSS_PCT:.0%})"
        )
    return None


# ── Weer-aware exit check ─────────────────────────────────────────────────────

def check_weather_exit(pos: dict) -> Optional[str]:
    """
    Op resolutiedag: gebruik intraday temperatuurtracking om vroeg een exit te signaleren.

    Logica:
      - Vroeg op de dag (< 12h lokaal): gebruik projected max/min op basis van trend
      - Laat op de dag (>= 16h lokaal): dagmax is grotendeels zeker, gebruik gemeten max
      - Hoog zekerheid-drempel voor early exit (conservatief), laag voor late exit
    """
    parsed = parse_weather_question(pos.get("question", ""))
    if not parsed:
        return None

    resolve_date = parsed.get("resolve_date")
    if not resolve_date:
        return None

    today = date.today()
    if today != resolve_date:
        return None  # nog niet op resolutiedag

    direction = pos.get("direction", "YES")
    temp_type = parsed["temp_type"]
    unit      = parsed["unit"]
    lo, hi    = parsed["temp_range"]

    # ── Intraday tracking via Open-Meteo ──────────────────────────────────────
    intraday = get_intraday_tracking(parsed["city"], resolve_date, unit)

    # Fallback: gebruik METAR als Open-Meteo faalt
    if intraday is None:
        actual_temp = get_current_temp(parsed["city"], unit)
        if actual_temp is None:
            return None
        hour_utc = datetime.now(timezone.utc).hour
        if hour_utc < 12:
            return None  # te vroeg zonder intraday data

        in_range    = lo <= actual_temp <= hi
        above_range = actual_temp > hi
        below_range = actual_temp < lo

        if direction == "NO" and temp_type == "high":
            if above_range:
                return f"Weer-exit: temp {actual_temp:.1f}°{unit} BOVEN range [{lo}-{hi}°{unit}] — verlies vastgelegd"
            if in_range:
                return f"Weer-exit: temp {actual_temp:.1f}°{unit} IN range [{lo}-{hi}°{unit}] — risico"
        elif direction == "YES" and temp_type == "high" and actual_temp < lo - 5:
            return f"Weer-exit: temp {actual_temp:.1f}°{unit} ver ONDER range [{lo}-{hi}°{unit}]"
        return None

    # ── Analyse met intraday data ─────────────────────────────────────────────
    last_hour      = intraday["last_hour_local"]
    hours_remaining= intraday["hours_remaining"]
    current        = intraday["current_temp"]
    day_max        = intraday["day_max_so_far"]
    day_min        = intraday["day_min_so_far"]
    projected_max  = intraday["projected_max"]
    projected_min  = intraday["projected_min"]
    trend          = intraday["trend_per_hour"]

    log.debug(
        f"IntraDay {parsed['city']} {today}: cur={current:.1f} max={day_max:.1f} "
        f"proj_max={projected_max:.1f} trend={trend:+.2f}/h "
        f"h_elapsed={intraday['hours_elapsed']} h_left={hours_remaining}"
    )

    if temp_type == "high":
        # Gebruik dagmax als het na 16h is (piek bereikt)
        # Gebruik projected_max eerder op de dag
        effective_max = day_max if last_hour >= 16 else max(day_max, projected_max)

        if direction == "NO":
            # Wij wedden dat de dagmax NIET in de range valt
            if effective_max > hi:
                return (
                    f"Weer-exit: dagmax {effective_max:.1f}°{unit} BOVEN range [{lo}-{hi}°{unit}]"
                    + (f" (meting om {last_hour:02d}h)" if last_hour >= 16 else f" (projectie, trend {trend:+.1f}/h)")
                )
            if lo <= effective_max <= hi:
                # In range: mogelijk verlies, maar geef 1h marge voor late ochtend
                if last_hour >= 14:
                    return (
                        f"Weer-exit: dagmax {effective_max:.1f}°{unit} IN range [{lo}-{hi}°{unit}] "
                        f"(h={last_hour}, risico op YES)"
                    )

        elif direction == "YES":
            # Wij wedden dat de dagmax WEL in de range valt
            # Vroeg exit als projected_max ver buiten range
            margin_out = 3.0 if unit == "F" else 1.5
            if effective_max < lo - margin_out and hours_remaining < 6:
                return (
                    f"Weer-exit: dagmax {effective_max:.1f}°{unit} te LAAG voor range [{lo}-{hi}°{unit}] "
                    f"({hours_remaining}h resterend)"
                )
            if effective_max > hi + margin_out:
                return (
                    f"Weer-exit: dagmax {effective_max:.1f}°{unit} te HOOG voor range [{lo}-{hi}°{unit}]"
                )

    elif temp_type == "low":
        # Dagmin is het vroegst 's nachts, round 04-06h lokaal
        effective_min = day_min if last_hour >= 7 else min(day_min, projected_min)

        if direction == "NO":
            if effective_min < lo:
                return (
                    f"Weer-exit: dagmin {effective_min:.1f}°{unit} ONDER range [{lo}-{hi}°{unit}] "
                    f"— YES zeker"
                )
            if lo <= effective_min <= hi and last_hour >= 6:
                return (
                    f"Weer-exit: dagmin {effective_min:.1f}°{unit} IN range [{lo}-{hi}°{unit}] "
                    f"(h={last_hour}, risico op YES)"
                )

        elif direction == "YES":
            margin_out = 3.0 if unit == "F" else 1.5
            if effective_min > hi + margin_out and hours_remaining < 4:
                return (
                    f"Weer-exit: dagmin {effective_min:.1f}°{unit} te HOOG voor range [{lo}-{hi}°{unit}] "
                    f"({hours_remaining}h resterend)"
                )

    return None


# ── Hoofd check ───────────────────────────────────────────────────────────────

def check_all_positions(dry_run: bool = True) -> list[dict]:
    """
    Check alle open posities op prijs-stop en weer-aware exit.
    Retourneert lijst van acties die genomen zijn (of zouden worden bij dry_run).
    """
    portfolio = load_portfolio()
    open_positions = [p for p in portfolio.positions if p.get("status") == "open"]

    if not open_positions:
        log.info("Geen open posities om te checken.")
        return []

    actions = []

    for pos in open_positions:
        pos_id  = pos.get("id", "?")
        question = pos.get("question", "")[:60]
        direction = pos.get("direction", "?")
        entry = pos.get("entry_price", 0)

        # Update live prijs
        cond_id = pos.get("condition_id", "")
        live_price = fetch_current_price(cond_id, direction)
        if live_price is not None:
            pos["current_price"] = live_price

        # Check 1: prijs-stop
        reason = check_price_stop(pos)

        # Check 2: weer-aware exit (als prijs-stop niet al triggered)
        if not reason:
            reason = check_weather_exit(pos)

        if reason:
            current = pos.get("current_price", entry)
            loss = (entry - current) * pos.get("shares", 0)
            log.warning(f"EXIT SIGNAAL [{pos_id}] {direction} '{question}': {reason}")
            log.warning(f"  Verlies bij exit: ${loss:.2f} (vs volledig verlies ${pos['amount']:.2f})")

            action = {
                "position_id": pos_id,
                "question":    question,
                "direction":   direction,
                "reason":      reason,
                "exit_price":  current,
                "loss_at_exit": round(loss, 2),
                "full_loss":   round(pos["amount"], 2),
                "saved":       round(pos["amount"] - abs(loss), 2),
            }

            if not dry_run:
                result = sell_position(pos_id, exit_price=current)
                action["executed"] = True
                action["result"] = result
                log.warning(f"  UITGEVOERD: positie {pos_id} verkocht @ ${current:.3f}")
            else:
                action["executed"] = False
                log.warning(f"  [DRY RUN] zou verkopen @ ${current:.3f}")

            actions.append(action)
        else:
            current = pos.get("current_price", entry)
            pct = (current - entry) / entry * 100 if entry else 0
            log.info(f"OK [{pos_id}] {direction} '{question}' — prijs {current:.3f} ({pct:+.0f}%)")

    return actions


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    watch_mode = "--watch" in sys.argv
    dry_run    = "--live" not in sys.argv  # standaard dry run, --live voor echte verkopen

    if dry_run:
        log.info("DRY RUN — gebruik --live voor echte verkopen")
    else:
        log.info("LIVE MODE — posities worden echt verkocht bij signaal")

    if watch_mode:
        log.info("Watch mode — check elke 10 minuten")
        while True:
            check_all_positions(dry_run=dry_run)
            time.sleep(600)
    else:
        actions = check_all_positions(dry_run=dry_run)
        if actions:
            print(f"\n{len(actions)} exit signalen gevonden:")
            for a in actions:
                print(f"  [{a['position_id']}] {a['direction']} {a['question']}")
                print(f"    Reden:  {a['reason']}")
                print(f"    Bespaard: ${a['saved']:.2f} van ${a['full_loss']:.2f}")
        else:
            print("Geen exit signalen — alle posities binnen limieten.")
