"""
resolution_stations.py — Polymarket weerstation verificatie per stad.

Polymarket lost temperatuurmarkten op via Wunderground, met een specifiek
METAR-station per stad. Dit bestand documenteert die stations en biedt
een verificatie-functie om te checken of ons model hetzelfde station gebruikt.

Bron: market descriptions ophalen via Gamma API (automatisch gegenereerd
      door scripts/discover_stations.py).

KRITIEKE BEVINDINGEN vs onze eerdere mapping:
  Denver    : KBKF (Buckley AFB, Aurora) vs KDEN — 5 mijl verderop, andere microklimaat
  NYC       : KLGA (LaGuardia) vs KJFK — LaGuardia is warmer door urban setting
  Dallas    : KDAL (Love Field) vs KDFW — Love Field is dichter bij downtown
  London    : EGLC (City Airport) vs EGLL (Heathrow) — City Airport is warmer
  Houston   : KHOU (Hobby) vs KIAH (Intercontinental) — Hobby is dichter bij centrum
  Paris     : LFPB (Le Bourget) vs LFPG (CDG) — Le Bourget is net anders
  Busan     : RKPK (Gimhae) — was niet in onze ICAO mapping
"""

from typing import Optional

# Volledig overzicht van Polymarket resolution stations
# Formaat: stad (lowercase) → {"icao": ICAO, "wunderground_url": URL, "lat": lat, "lon": lon}
POLYMARKET_STATIONS: dict[str, dict] = {
    "miami":          {"icao": "KMIA",  "lat": 25.796, "lon": -80.287, "url": "https://www.wunderground.com/history/daily/us/fl/miami/KMIA"},
    "new york city":  {"icao": "KLGA",  "lat": 40.777, "lon": -73.873, "url": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA"},
    "chicago":        {"icao": "KORD",  "lat": 41.983, "lon": -87.907, "url": "https://www.wunderground.com/history/daily/us/il/chicago/KORD"},
    "los angeles":    {"icao": "KLAX",  "lat": 33.943, "lon": -118.408, "url": "https://www.wunderground.com/history/daily/us/ca/los-angeles/KLAX"},
    "dallas":         {"icao": "KDAL",  "lat": 32.847, "lon": -96.852, "url": "https://www.wunderground.com/history/daily/us/tx/dallas/KDAL"},
    "seattle":        {"icao": "KSEA",  "lat": 47.449, "lon": -122.309, "url": "https://www.wunderground.com/history/daily/us/wa/seatac/KSEA"},
    "san francisco":  {"icao": "KSFO",  "lat": 37.619, "lon": -122.375, "url": "https://www.wunderground.com/history/daily/us/ca/san-francisco/KSFO"},
    "houston":        {"icao": "KHOU",  "lat": 29.645, "lon": -95.279, "url": "https://www.wunderground.com/history/daily/us/tx/houston/KHOU"},
    "atlanta":        {"icao": "KATL",  "lat": 33.641, "lon": -84.427, "url": "https://www.wunderground.com/history/daily/us/ga/atlanta/KATL"},
    "denver":         {"icao": "KBKF",  "lat": 39.717, "lon": -104.752, "url": "https://www.wunderground.com/history/daily/us/co/aurora/KBKF"},
    "austin":         {"icao": "KAUS",  "lat": 30.194, "lon": -97.670, "url": "https://www.wunderground.com/history/daily/us/tx/austin/KAUS"},
    "toronto":        {"icao": "CYYZ",  "lat": 43.677, "lon": -79.631, "url": "https://www.wunderground.com/history/daily/ca/mississauga/CYYZ"},
    "amsterdam":      {"icao": "EHAM",  "lat": 52.308, "lon": 4.764,   "url": "https://www.wunderground.com/history/daily/nl/schiphol/EHAM"},
    "london":         {"icao": "EGLC",  "lat": 51.505, "lon": 0.055,   "url": "https://www.wunderground.com/history/daily/gb/london/EGLC"},
    "paris":          {"icao": "LFPB",  "lat": 48.969, "lon": 2.441,   "url": "https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB"},
    "madrid":         {"icao": "LEMD",  "lat": 40.494, "lon": -3.567,  "url": "https://www.wunderground.com/history/daily/es/madrid/LEMD"},
    "milan":          {"icao": "LIMC",  "lat": 45.630, "lon": 8.723,   "url": "https://www.wunderground.com/history/daily/it/milan/LIMC"},
    "munich":         {"icao": "EDDM",  "lat": 48.354, "lon": 11.787,  "url": "https://www.wunderground.com/history/daily/de/munich/EDDM"},
    "warsaw":         {"icao": "EPWA",  "lat": 52.166, "lon": 20.967,  "url": "https://www.wunderground.com/history/daily/pl/warsaw/EPWA"},
    "helsinki":       {"icao": "EFHK",  "lat": 60.317, "lon": 24.963,  "url": "https://www.wunderground.com/history/daily/fi/vantaa/EFHK"},
    "tokyo":          {"icao": "RJTT",  "lat": 35.553, "lon": 139.781, "url": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT"},
    "seoul":          {"icao": "RKSS",  "lat": 37.558, "lon": 126.791, "url": "https://www.wunderground.com/history/daily/kr/incheon/RKSS"},
    "busan":          {"icao": "RKPK",  "lat": 35.179, "lon": 128.938, "url": "https://www.wunderground.com/history/daily/kr/busan/RKPK"},
    "singapore":      {"icao": "WSSS",  "lat": 1.359,  "lon": 103.989, "url": "https://www.wunderground.com/history/daily/sg/singapore/WSSS"},
    "hong kong":      {"icao": "VHHH",  "lat": 22.309, "lon": 113.915, "url": "https://www.wunderground.com/history/daily/hk/hong-kong/VHHH"},
    "taipei":         {"icao": "RCSS",  "lat": 25.070, "lon": 121.552, "url": "https://www.wunderground.com/history/daily/tw/taipei/RCSS"},
    "kuala lumpur":   {"icao": "WMKK",  "lat": 2.746,  "lon": 101.710, "url": "https://www.wunderground.com/history/daily/my/sepang-district/WMKK"},
    "jakarta":        {"icao": "WIHH",  "lat": -6.125, "lon": 106.655, "url": "https://www.wunderground.com/history/daily/id/jakarta/WIHH"},
    "bangkok":        {"icao": "VTBS",  "lat": 13.681, "lon": 100.747, "url": "https://www.wunderground.com/history/daily/th/bangkok/VTBS"},
    "guangzhou":      {"icao": "ZGGG",  "lat": 23.392, "lon": 113.299, "url": "https://www.wunderground.com/history/daily/cn/guangzhou/ZGGG"},
    "shenzhen":       {"icao": "ZGSZ",  "lat": 22.640, "lon": 113.813, "url": "https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ"},
    "wuhan":          {"icao": "ZHHH",  "lat": 30.784, "lon": 114.208, "url": "https://www.wunderground.com/history/daily/cn/wuhan/ZHHH"},
    "mexico city":    {"icao": "MMMX",  "lat": 19.436, "lon": -99.072, "url": "https://www.wunderground.com/history/daily/mx/mexico-city/MMMX"},
    "sao paulo":      {"icao": "SBGR",  "lat": -23.432, "lon": -46.469, "url": "https://www.wunderground.com/history/daily/br/guarulhos/SBGR"},
    "lagos":          {"icao": "DNMM",  "lat": 6.577,  "lon": 3.321,   "url": "https://www.wunderground.com/history/daily/ng/lagos/DNMM"},
    "karachi":        {"icao": "OPKC",  "lat": 24.906, "lon": 67.161,  "url": "https://www.wunderground.com/history/daily/pk/karachi/OPKC"},
    "lucknow":        {"icao": "VILK",  "lat": 26.761, "lon": 80.889,  "url": "https://www.wunderground.com/history/daily/in/lucknow/VILK"},
    "panama city":    {"icao": "MPMG",  "lat": 9.071,  "lon": -79.383, "url": "https://www.wunderground.com/history/daily/pa/panama-city/MPMG"},
    "ankara":         {"icao": "LTAC",  "lat": 40.128, "lon": 32.995,  "url": "https://www.wunderground.com/history/daily/tr/%C3%A7ubuk/LTAC"},
    "wellington":     {"icao": "NZWN",  "lat": -41.327, "lon": 174.806, "url": "https://www.wunderground.com/history/daily/nz/wellington/NZWN"},
}

# Steden waar ons model een verkeerd station gebruikte — deze discrepanties
# kunnen systematisch tot verkeerde model-kansen leiden.
STATION_CORRECTIONS: dict[str, dict] = {
    "denver":        {"old_icao": "KDEN",  "new_icao": "KBKF",  "note": "Buckley AFB Aurora vs DEN — andere hoogte en microklimaat"},
    "new york city": {"old_icao": "KJFK",  "new_icao": "KLGA",  "note": "LaGuardia warmer dan JFK door urban setting"},
    "dallas":        {"old_icao": "KDFW",  "new_icao": "KDAL",  "note": "Love Field dichter bij downtown Dallas"},
    "london":        {"old_icao": "EGLL",  "new_icao": "EGLC",  "note": "City Airport warmer en windiger dan Heathrow"},
    "houston":       {"old_icao": "KIAH",  "new_icao": "KHOU",  "note": "Hobby Airport dichter bij centrum Houston"},
    "paris":         {"old_icao": "LFPG",  "new_icao": "LFPB",  "note": "Le Bourget vs CDG — vergelijkbaar maar net anders"},
}


def get_polymarket_station(city: str) -> Optional[dict]:
    """Retourneert station-info voor Polymarket resolutie."""
    return POLYMARKET_STATIONS.get(city.lower())


def get_wunderground_coords(city: str) -> Optional[tuple[float, float]]:
    """Retourneert (lat, lon) van het exacte Polymarket resolution station."""
    info = get_polymarket_station(city)
    if info:
        return (info["lat"], info["lon"])
    return None


def check_station_bias(city: str) -> Optional[str]:
    """
    Geeft een waarschuwing als ons model een ander station gebruikt
    dan Polymarket. Relevante systematische bias.
    """
    corr = STATION_CORRECTIONS.get(city.lower())
    if corr:
        return (
            f"STATION BIAS: {city.title()} — model gebruikt {corr['old_icao']} "
            f"maar Polymarket lost op via {corr['new_icao']} ({corr['note']})"
        )
    return None


def get_wunderground_url(city: str) -> Optional[str]:
    """Retourneert de Wunderground URL voor handmatige verificatie."""
    info = get_polymarket_station(city)
    return info.get("url") if info else None


# ── CLI: toon alle stations ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nPolymarket Resolution Stations")
    print("=" * 65)
    print(f"{'Stad':<20} {'ICAO':<8} {'Lat':>7} {'Lon':>8}  Wunderground URL")
    print("-" * 65)
    for city, info in sorted(POLYMARKET_STATIONS.items()):
        print(f"{city.title():<20} {info['icao']:<8} {info['lat']:>7.3f} {info['lon']:>8.3f}  {info['url']}")

    print("\n⚠  Station-discrepanties vs oud model:")
    for city, corr in STATION_CORRECTIONS.items():
        print(f"  {city.title()}: {corr['old_icao']} → {corr['new_icao']}  ({corr['note']})")
