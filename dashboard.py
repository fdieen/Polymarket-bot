"""
Polymarket Dashboard — Flask web interface.
Start: venv/bin/python dashboard.py
Open:  http://localhost:5000
"""
import json
import os
import requests
from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
GAMMA_API = "https://gamma-api.polymarket.com/markets"

CATEGORIES = {
    "all":     [],
    "sport":   ["nba","nfl","nhl","soccer","football","basketball","tennis","mls","ufc","boxing","cricket","ipl","bundesliga","premier","laliga","serie","ligue"],
    "crypto":  ["bitcoin","btc","eth","crypto","token","opensea","coinbase","sec","etf","solana","doge"],
    "politics":["trump","iran","russia","ukraine","nato","election","congress","senate","president","fed","tariff"],
    "weather": ["temperature","°c","°f","fahrenheit","celsius","rainfall","precipitation","snowfall","inches of snow","mm of rain","will it rain"],
}


def fetch_markets(category="all", limit=50):
    tags = CATEGORIES.get(category, [])
    if category == "weather":
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _fetch_page(offset):
            try:
                r = requests.get(
                    GAMMA_API,
                    params={"limit": 500, "offset": offset, "order": "liquidity",
                            "ascending": "false", "active": "true"},
                    timeout=12,
                )
                return r.json()
            except Exception:
                return []
        data = []
        with ThreadPoolExecutor(max_workers=5) as exe:
            futures = [exe.submit(_fetch_page, off) for off in range(0, 5000, 500)]
            for f in as_completed(futures):
                data.extend(f.result())
        data = [m for m in data if "highest temperature in" in m.get("question","").lower()
                and float(m.get("liquidity") or 0) > 0]
        # dedup
        seen = set()
        deduped = []
        for m in data:
            cid = m.get("conditionId","")
            if cid not in seen:
                seen.add(cid)
                deduped.append(m)
        data = sorted(deduped, key=lambda m: float(m.get("liquidity") or 0), reverse=True)
    else:
        r = requests.get(
            GAMMA_API,
            params={"limit": 100, "order": "volume24hr", "ascending": "false", "active": "true"},
            timeout=10,
        )
        data = r.json()
        if tags:
            data = [m for m in data if any(t in m.get("question","").lower() for t in tags)]
    result = []
    for m in data[:limit]:
        prices   = m.get("outcomePrices","[]")
        outcomes = m.get("outcomes","[]")
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: prices = []
        if isinstance(outcomes, str):
            try: outcomes = json.loads(outcomes)
            except: outcomes = []

        yes_price = float(prices[0]) if prices else 0
        no_price  = float(prices[1]) if len(prices) > 1 else 1 - yes_price
        vol       = float(m.get("volume24hr") or 0)
        liq       = float(m.get("liquidity") or 0)

        result.append({
            "question":    m.get("question","?"),
            "conditionId": m.get("conditionId",""),
            "slug":        m.get("slug",""),
            "yes":         round(yes_price, 3),
            "no":          round(no_price, 3),
            "volume":      vol,
            "liquidity":   liq,
            "endDate":     m.get("endDate","")[:10] if m.get("endDate") else "",
            "outcomes":    outcomes,
        })
    return result


HTML = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>POLYMARKET TERMINAL</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    /* Pressure System — meteorological precision × quant finance */
    --void:    #020B12;
    --deep:    #040F18;
    --panel:   #071420;
    --panel2:  #0A1A28;
    --border:  #0D2030;
    --border2: #163548;

    /* Primary accent: amber — weeralarmen, temperatuurhitte */
    --ice:     #E8920A;
    --ice2:    #7A4A00;
    --ice3:    #2D1A00;

    /* Secondary: electric blue — koude data, NO signalen */
    --blue:    #1AB8FF;
    --blue2:   #005880;
    --blue3:   #001D2E;

    --danger:  #FF3F5A;
    --warn:    #FFB800;
    --go:      #00DF7A;
    --go2:     #004D2A;
    --text:    #A8C4D8;
    --text2:   #2E5268;
    --muted:   #0F2030;
    --green:   #00DF7A;
    --green2:  #004D2A;
    --red:     #FF3F5A;
    --amber:   #E8920A;
    --accent:  #1AB8FF;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--void);
    color: var(--text);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Topografische contourlijnen — weerkaartstijl */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      repeating-linear-gradient(
        0deg,
        transparent,
        transparent 39px,
        rgba(232,146,10,0.04) 39px,
        rgba(232,146,10,0.04) 40px
      ),
      repeating-linear-gradient(
        90deg,
        transparent,
        transparent 39px,
        rgba(232,146,10,0.025) 39px,
        rgba(232,146,10,0.025) 40px
      );
    pointer-events: none;
    z-index: 0;
  }

  /* Atmosferische gloed onderin */
  body::after {
    content: '';
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: 40vh;
    background: radial-gradient(ellipse at 50% 100%, rgba(232,146,10,0.04) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  .header, .tabs, .main { position: relative; z-index: 1; }

  /* Header */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 28px;
    height: 52px;
    border-bottom: 1px solid var(--border);
    background: rgba(3,8,16,0.95);
    position: sticky;
    top: 0;
    z-index: 50;
    backdrop-filter: blur(12px);
  }

  /* Top edge — amber pressure line */
  .header::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, transparent 0%, var(--ice) 30%, var(--warn) 50%, var(--ice) 70%, transparent 100%);
    opacity: 0.7;
  }

  /* Subtiele atmosferische gloed achter header */
  .header::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, rgba(232,146,10,0.03) 0%, transparent 100%);
    pointer-events: none;
  }

  .logo {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  /* Barometer logo mark */
  .logo-mark {
    width: 14px;
    height: 14px;
    border: 2px solid var(--ice);
    border-radius: 50%;
    position: relative;
    box-shadow: 0 0 10px rgba(232,146,10,0.4);
    animation: pressurePulse 4s ease-in-out infinite;
    flex-shrink: 0;
  }

  .logo-mark::after {
    content: '';
    position: absolute;
    top: 50%; left: 50%;
    width: 5px; height: 2px;
    background: var(--ice);
    transform-origin: left center;
    transform: translate(-1px, -50%) rotate(-40deg);
    border-radius: 1px;
  }

  @keyframes pressurePulse {
    0%, 100% { box-shadow: 0 0 10px rgba(232,146,10,0.4); }
    50%       { box-shadow: 0 0 18px rgba(232,146,10,0.7), 0 0 6px rgba(232,146,10,0.3); }
  }

  .logo-text {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 0.22em;
    color: var(--text);
    text-transform: uppercase;
  }

  .logo-text span { color: var(--ice); }

  .header-right {
    display: flex;
    align-items: center;
    gap: 24px;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    letter-spacing: 0.12em;
    color: var(--text2);
    text-transform: uppercase;
  }

  .header-right .live-dot {
    display: inline-block;
    width: 7px; height: 7px;
    background: var(--go);
    border-radius: 50%;
    margin-right: 6px;
    box-shadow: 0 0 8px var(--go), 0 0 3px var(--go);
    animation: pressurePulse 2s ease-in-out infinite;
  }

  #clock {
    color: var(--ice);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px;
    letter-spacing: 0.1em;
    font-weight: 600;
  }

  /* Tabs — departure board stijl */
  .tabs {
    display: flex;
    align-items: stretch;
    padding: 0 24px;
    background: rgba(2,11,18,0.98);
    border-bottom: 1px solid var(--border);
    position: relative;
    gap: 2px;
    overflow-x: auto;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  .tabs::-webkit-scrollbar { display: none; }

  .tab {
    padding: 0 18px;
    height: 42px;
    display: flex;
    align-items: center;
    cursor: pointer;
    color: var(--text2);
    letter-spacing: 0.18em;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 700;
    transition: color 0.15s;
    text-transform: uppercase;
    white-space: nowrap;
    flex-shrink: 0;
    position: relative;
    border-bottom: 2px solid transparent;
  }

  .tab:hover { color: var(--text); }

  .tab.active {
    color: var(--ice);
    border-bottom-color: var(--ice);
  }

  .tab.active::before {
    content: '';
    position: absolute;
    bottom: -1px; left: 0; right: 0;
    height: 2px;
    background: var(--ice);
    box-shadow: 0 0 12px rgba(232,146,10,0.6);
  }

  /* Main layout */
  .main { padding: 20px 28px; }

  /* Stats bar */
  .stats-bar {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 18px;
  }

  .stat-card {
    background: var(--deep);
    border: 1px solid var(--border);
    border-top: 2px solid var(--ice2);
    padding: 14px 18px;
    border-radius: 2px;
    position: relative;
    overflow: hidden;
    transition: border-top-color 0.2s, box-shadow 0.2s;
  }

  .stat-card:hover {
    border-top-color: var(--ice);
    box-shadow: 0 -2px 16px rgba(232,146,10,0.15);
  }

  .stat-card::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, rgba(232,146,10,0.04) 0%, transparent 50%);
    pointer-events: none;
  }

  .stat-label {
    font-family: 'Barlow Condensed', sans-serif;
    color: var(--text2);
    font-size: 11px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    margin-bottom: 8px;
    font-weight: 500;
  }

  .stat-val, .stat-value {
    font-size: 22px;
    font-weight: 700;
    color: var(--text);
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: -0.02em;
  }

  .stat-value.green { color: var(--go); }
  .stat-value.amber { color: var(--warn); }

  /* Table */
  .table-wrap {
    background: var(--deep);
    border: 1px solid var(--border);
    border-radius: 3px;
    overflow: hidden;
  }

  .table-header {
    display: grid;
    grid-template-columns: 1fr 80px 80px 100px 90px 80px;
    padding: 10px 18px;
    background: var(--panel2);
    border-bottom: 1px solid var(--border);
    color: var(--text2);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    font-weight: 600;
  }

  .market-row {
    display: grid;
    grid-template-columns: 1fr 80px 80px 100px 90px 80px;
    padding: 11px 18px;
    border-bottom: 1px solid var(--border);
    border-left: 2px solid transparent;
    align-items: center;
    cursor: pointer;
    transition: background 0.12s, border-color 0.12s;
    animation: fadeIn 0.3s ease forwards;
    opacity: 0;
  }

  .market-row:last-child { border-bottom: none; }

  .market-row:hover {
    background: var(--panel);
    border-left-color: var(--ice);
  }

  @keyframes fadeIn { to { opacity: 1; } }

  .market-question {
    font-size: 11px;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding-right: 16px;
  }

  .price-yes { font-weight: 700; }
  .price-yes.high  { color: var(--go); }
  .price-yes.mid   { color: var(--warn); }
  .price-yes.low   { color: var(--danger); }
  .price-yes.dead  { color: var(--muted); }

  .price-no { color: var(--text2); }

  .prob-bar {
    height: 4px;
    background: rgba(255,51,85,0.25);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 5px;
  }

  .prob-fill {
    height: 100%;
    border-radius: 2px 0 0 2px;
    transition: width 0.6s cubic-bezier(0.23, 1, 0.32, 1);
  }

  .prob-fill.high  { background: linear-gradient(90deg, var(--go2), var(--go)); }
  .prob-fill.mid   { background: linear-gradient(90deg, var(--ice2), var(--ice)); }
  .prob-fill.low   { background: linear-gradient(90deg, rgba(255,63,90,0.4), var(--danger)); }

  .vol-cell { color: var(--text2); }
  .vol-cell .vol-num { color: var(--text); }

  .end-date {
    color: var(--text2);
    font-size: 10px;
  }

  .trade-btn {
    background: transparent;
    border: 1px solid var(--ice2);
    color: var(--ice);
    padding: 4px 12px;
    border-radius: 2px;
    cursor: pointer;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2em;
    transition: all 0.15s;
    text-transform: uppercase;
    position: relative;
  }

  .trade-btn:hover {
    background: var(--ice3);
    border-color: var(--ice);
    box-shadow: 0 0 16px rgba(232,146,10,0.35);
    color: #fff;
  }

  .trade-btn.hot {
    border-color: var(--go);
    color: var(--go);
    animation: tradePulse 2.5s ease-in-out infinite;
  }

  @keyframes tradePulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(0,223,122,0); }
    50%       { box-shadow: 0 0 0 5px rgba(0,223,122,0.18); }
  }

  /* Loading */
  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 48px;
    color: var(--text2);
    gap: 12px;
    font-family: 'Barlow Condensed', sans-serif;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    font-size: 12px;
  }

  .spinner {
    width: 14px; height: 14px;
    border: 2px solid var(--border);
    border-top-color: var(--ice);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  /* Modal */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(3,8,16,0.88);
    z-index: 200;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(6px);
  }

  .modal-overlay.open { display: flex; }

  .modal {
    background: var(--deep);
    border: 1px solid var(--border2);
    border-top: 1px solid var(--ice2);
    border-radius: 4px;
    padding: 28px;
    width: 480px;
    animation: slideUp 0.2s ease;
    box-shadow: 0 24px 80px rgba(0,0,0,0.7), 0 0 40px rgba(0,200,255,0.06);
  }

  @keyframes slideUp {
    from { transform: translateY(16px); opacity: 0; }
    to   { transform: translateY(0);    opacity: 1; }
  }

  .modal-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--ice);
    margin-bottom: 6px;
  }

  .modal-question {
    font-size: 12px;
    color: var(--text);
    margin-bottom: 22px;
    line-height: 1.6;
  }

  .modal-prices {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-bottom: 20px;
  }

  .price-opt {
    border: 1px solid var(--border2);
    border-radius: 3px;
    padding: 14px;
    cursor: pointer;
    transition: all 0.15s;
    text-align: center;
    background: var(--panel);
  }

  .price-opt:hover { border-color: var(--ice2); }
  .price-opt.selected { border-color: var(--ice); background: var(--ice3); box-shadow: 0 0 16px rgba(0,200,255,0.1); }
  .price-opt .opt-label { font-family: 'Barlow Condensed', sans-serif; font-size: 10px; font-weight: 700; color: var(--text2); letter-spacing: 0.2em; margin-bottom: 8px; text-transform: uppercase; }
  .price-opt .opt-price { font-size: 26px; font-weight: 700; letter-spacing: -0.02em; }
  .price-opt.yes .opt-price { color: var(--go); }
  .price-opt.no  .opt-price { color: var(--danger); }

  .amount-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
  }

  .amount-label { font-family: 'Barlow Condensed', sans-serif; color: var(--text2); font-size: 12px; font-weight: 600; letter-spacing: 0.15em; white-space: nowrap; text-transform: uppercase; }

  .amount-input {
    flex: 1;
    background: var(--panel);
    border: 1px solid var(--border2);
    color: var(--text);
    padding: 8px 14px;
    border-radius: 3px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px;
    outline: none;
    transition: border-color 0.15s;
  }

  .amount-input:focus { border-color: var(--ice); box-shadow: 0 0 0 2px rgba(0,200,255,0.08); }

  .quick-amounts { display: flex; gap: 6px; }

  .qa-btn {
    background: var(--panel);
    border: 1px solid var(--border2);
    color: var(--text2);
    padding: 6px 10px;
    border-radius: 2px;
    cursor: pointer;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 600;
    transition: all 0.1s;
    letter-spacing: 0.05em;
  }

  .qa-btn:hover { border-color: var(--warn); color: var(--warn); }

  .modal-footer {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
    margin-top: 4px;
  }

  .btn-cancel {
    background: transparent;
    border: 1px solid var(--border2);
    color: var(--text2);
    padding: 9px 18px;
    border-radius: 3px;
    cursor: pointer;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    transition: all 0.15s;
  }

  .btn-cancel:hover { border-color: var(--text2); color: var(--text); }

  .btn-execute {
    background: var(--ice3);
    border: 1px solid var(--ice);
    color: var(--ice);
    padding: 9px 22px;
    border-radius: 3px;
    cursor: pointer;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    transition: all 0.15s;
  }

  .btn-execute:hover { background: var(--ice2); box-shadow: 0 0 16px rgba(0,200,255,0.3); }
  .btn-execute:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Kelly */
  .kelly-block {
    background: var(--panel);
    border: 1px solid var(--border2);
    border-left: 2px solid var(--warn);
    border-radius: 3px;
    padding: 14px 16px;
    margin-bottom: 18px;
  }

  .kelly-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .kelly-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--warn);
  }

  .kelly-sub { font-size: 10px; color: var(--text2); letter-spacing: 0.05em; }

  .kelly-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }

  .kelly-label { font-family: 'Barlow Condensed', sans-serif; font-size: 11px; font-weight: 600; color: var(--text2); white-space: nowrap; width: 84px; letter-spacing: 0.1em; text-transform: uppercase; }
  .kelly-pct { font-size: 14px; font-weight: 700; color: var(--text); width: 38px; }

  .kelly-slider {
    flex: 1;
    accent-color: var(--warn);
    cursor: pointer;
  }

  .kelly-bankroll-input {
    width: 84px;
    background: var(--deep);
    border: 1px solid var(--border2);
    color: var(--text);
    padding: 5px 9px;
    border-radius: 2px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    outline: none;
  }

  .kelly-result {
    margin-top: 10px;
    padding: 10px 14px;
    background: var(--deep);
    border-radius: 2px;
    font-size: 11px;
    line-height: 1.9;
    min-height: 44px;
    border-left: 2px solid var(--border2);
  }

  .kelly-good  { color: var(--go); }
  .kelly-ok    { color: var(--warn); }
  .kelly-bad   { color: var(--danger); }
  .kelly-value { color: var(--text); font-weight: 700; }

  .note {
    font-size: 10px;
    color: var(--text2);
    margin-top: 14px;
    line-height: 1.7;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }

  /* Category filter pills */
  .cat-pill {
    padding: 5px 14px;
    border-radius: 20px;
    border: 1px solid var(--border2);
    background: transparent;
    color: var(--text2);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.15s;
  }

  .cat-pill:hover { color: var(--text); border-color: var(--ice2); }

  .cat-pill.active {
    background: rgba(0,200,255,0.12);
    border-color: var(--ice);
    color: var(--ice);
  }

  /* Sports tab */
  .sports-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }

  .scan-btn {
    background: rgba(232,146,10,0.1);
    border: 1px solid var(--ice2);
    color: var(--ice);
    padding: 7px 18px;
    border-radius: 2px;
    cursor: pointer;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    transition: all 0.15s;
  }

  .scan-btn:hover {
    background: rgba(232,146,10,0.18);
    border-color: var(--ice);
    box-shadow: 0 0 16px rgba(232,146,10,0.25);
    color: #fff;
  }
  .scan-btn:disabled { opacity: 0.35; cursor: not-allowed; }

  /* Toggle switch */
  .toggle-wrap { position:relative; display:inline-block; width:44px; height:24px; cursor:pointer; }
  .toggle-wrap input { opacity:0; width:0; height:0; }
  .toggle-slider { position:absolute; inset:0; background:var(--ice3); border-radius:24px; transition:0.3s; }
  .toggle-slider::before { content:''; position:absolute; height:18px; width:18px; left:3px; bottom:3px; background:var(--text2); border-radius:50%; transition:0.3s; }
  .toggle-wrap input:checked + .toggle-slider { background:var(--ice2); }
  .toggle-wrap input:checked + .toggle-slider::before { background:var(--ice); transform:translateX(20px); }

  /* Config cards */
  .config-card { background:var(--panel); border:1px solid var(--ice3); border-radius:6px; padding:12px; text-align:center; }
  .config-label { font-size:10px; color:var(--text2); text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px; }
  .config-val { font-size:16px; color:var(--ice); font-weight:700; margin-top:6px; font-family:'JetBrains Mono',monospace; }
  .cfg-slider { width:100%; accent-color:var(--ice); }

  /* Whale tracker */
  .whale-row { display:flex; gap:8px; align-items:center; padding:4px 0; border-bottom:1px solid rgba(0,200,255,0.06); font-size:12px; }
  .whale-outcome { padding:2px 6px; border-radius:3px; font-size:10px; font-weight:700; flex-shrink:0; }
  .whale-outcome.yes { background:rgba(0,232,122,0.15); color:var(--go); }
  .whale-outcome.no  { background:rgba(255,51,85,0.15);  color:var(--danger); }
  .whale-val   { color:var(--ice);  width:48px; flex-shrink:0; font-variant-numeric:tabular-nums; }
  .whale-price { color:var(--text2); width:32px; flex-shrink:0; }
  .whale-title { color:var(--text); flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  .whale-copy-btn { background:var(--ice3); border:1px solid var(--ice2); color:var(--ice); padding:2px 8px; border-radius:3px; cursor:pointer; font-size:10px; font-family:inherit; flex-shrink:0; }
  .whale-copy-btn:hover { background:var(--ice2); }
  .whale-positions-list { max-height:300px; overflow-y:auto; }

  .sports-table-wrap {
    background: var(--deep);
    border: 1px solid var(--border);
    border-radius: 3px;
    overflow: hidden;
  }

  .sports-table-header {
    display: grid;
    grid-template-columns: 1fr 120px 80px 80px 70px 110px 70px;
    padding: 10px 18px;
    background: var(--panel2);
    border-bottom: 1px solid var(--border);
    color: var(--text2);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
  }

  .sports-row {
    display: grid;
    grid-template-columns: 1fr 120px 80px 80px 70px 110px 70px;
    padding: 11px 18px;
    border-bottom: 1px solid var(--border);
    border-left: 2px solid transparent;
    align-items: center;
    animation: fadeIn 0.3s ease forwards;
    opacity: 0;
    transition: background 0.12s, border-color 0.12s;
  }

  .sports-row:last-child { border-bottom: none; }
  .sports-row:hover { background: var(--panel); border-left-color: var(--ice2); }

  .gap-badge {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 2px;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.1em;
  }

  .gap-strong { background: rgba(0,223,122,0.1); color: var(--go); border: 1px solid rgba(0,223,122,0.3); letter-spacing: 0.08em; }
  .gap-good   { background: rgba(232,146,10,0.1); color: var(--ice); border: 1px solid rgba(232,146,10,0.3); letter-spacing: 0.08em; }
  .gap-empty {
    color: var(--text2);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 13px;
    letter-spacing: 0.1em;
    padding: 48px;
    text-align: center;
  }

  /* ── Weather Panel ─────────────────────────── */
  .weer-panel {
    display: none;
  }

  .weer-top {
    display: grid;
    grid-template-columns: 1fr 340px;
    gap: 16px;
    margin-bottom: 16px;
  }

  .weer-map-wrap {
    position: relative;
    background: #060d18;
    border: 1px solid #1a2740;
    border-radius: 6px;
    overflow: hidden;
    height: 420px;
  }

  /* Radar ring overlay op de kaart */
  .radar-rings {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 500;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .radar-rings::before,
  .radar-rings::after {
    content: '';
    position: absolute;
    border-radius: 50%;
    border: 1px solid rgba(0,200,120,0.08);
    animation: radarPulse 4s linear infinite;
  }

  .radar-rings::before { width: 200px; height: 200px; animation-delay: 0s; }
  .radar-rings::after  { width: 380px; height: 380px; animation-delay: 2s; }

  @keyframes radarPulse {
    0%   { opacity: 0.6; transform: scale(0.6); }
    100% { opacity: 0;   transform: scale(1.8); }
  }

  #weather-map {
    width: 100%;
    height: 100%;
    filter: brightness(0.9) saturate(0.85);
  }

  .weer-map-label {
    position: absolute;
    top: 10px;
    left: 12px;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: rgba(0,200,120,0.7);
    text-transform: uppercase;
    z-index: 500;
    background: rgba(6,13,24,0.8);
    padding: 3px 8px;
    border-radius: 2px;
    border: 1px solid rgba(0,200,120,0.2);
  }

  /* Opportunities — full width grid, geen aparte container styling nodig */
  .weer-opps { /* fallback, grid is inline */ }

  .weer-opp-card {
    background: var(--deep);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 14px 16px;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s, transform 0.1s;
    animation: fadeIn 0.4s ease forwards;
    opacity: 0;
    position: relative;
    overflow: hidden;
  }

  .weer-opp-card::before {
    content: '';
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 3px;
  }

  .weer-opp-card::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, rgba(232,146,10,0.03) 0%, transparent 50%);
    pointer-events: none;
  }

  .weer-opp-card.buy-no::before  { background: var(--blue); box-shadow: 0 0 10px var(--blue); }
  .weer-opp-card.buy-yes::before { background: var(--ice); box-shadow: 0 0 10px var(--ice); }

  .weer-opp-card:hover {
    border-color: var(--ice2);
    background: var(--panel);
    transform: translateX(2px);
  }

  .weer-opp-city {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 20px;
    font-weight: 800;
    color: var(--text);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 1px;
    line-height: 1;
  }

  .weer-opp-date {
    font-size: 9px;
    color: var(--text2);
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-bottom: 12px;
    font-family: 'Barlow Condensed', sans-serif;
  }

  .weer-opp-temps {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 12px;
  }

  .temp-box {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 8px 10px;
    text-align: center;
  }

  .temp-box-label { font-family: 'Barlow Condensed', sans-serif; font-size: 9px; font-weight: 600; color: var(--text2); letter-spacing: 0.18em; text-transform: uppercase; margin-bottom: 4px; }
  .temp-box-val   { font-size: 18px; font-weight: 700; font-family: 'IBM Plex Mono', monospace; }
  .temp-hot  { color: #FF5722; }
  .temp-warm { color: var(--warn); }
  .temp-mild { color: var(--go); }
  .temp-cool { color: var(--ice); }
  .temp-cold { color: #7986cb; }

  .weer-gap-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .weer-action {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.18em;
    padding: 4px 12px;
    border-radius: 2px;
    text-transform: uppercase;
  }

  .action-buy-no  { background: var(--ice3); color: var(--ice); border: 1px solid var(--ice2); }
  .action-buy-yes { background: rgba(255,170,0,0.1); color: var(--warn); border: 1px solid rgba(255,170,0,0.3); }

  .weer-gap-val {
    font-size: 13px;
    font-weight: 700;
    font-family: 'IBM Plex Mono', monospace;
  }

  /* Windy embed */
  .windy-wrap {
    border: 1px solid var(--border);
    border-radius: 3px;
    overflow: hidden;
    position: relative;
    background: var(--void);
  }

  .windy-label {
    position: absolute;
    top: 10px;
    right: 12px;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.2em;
    color: rgba(0,200,255,0.8);
    text-transform: uppercase;
    z-index: 10;
    background: rgba(3,8,16,0.92);
    padding: 4px 10px;
    border-radius: 2px;
    border: 1px solid rgba(0,200,255,0.2);
  }

  .windy-wrap iframe {
    width: 100%;
    height: 340px;
    border: none;
    display: block;
  }

  .weer-scan-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }

  .weer-scan-info {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.06em;
    color: var(--text2);
  }

  .weer-scan-info span { color: var(--ice); }

  /* Whale wallet cards */
  .whale-card {
    background: var(--panel);
    border: 1px solid var(--border2);
    border-radius: 6px;
    padding: 14px 16px;
    transition: border-color 0.2s, box-shadow 0.2s;
    cursor: pointer;
  }
  .whale-card:hover { border-color: var(--blue); box-shadow: 0 0 16px rgba(26,184,255,0.08); }
  .whale-card-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px; }
  .whale-card-name { font-size:14px; font-weight:700; color:var(--text); letter-spacing:0.05em; }
  .whale-card-addr { font-size:9px; color:var(--text2); font-family:'IBM Plex Mono',monospace; margin-top:2px; }
  .whale-card-note { font-size:10px; color:var(--blue); margin-top:2px; }
  .whale-stats { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-top:10px; }
  .whale-stat { text-align:center; }
  .whale-stat-label { font-size:8px; color:var(--text2); letter-spacing:0.12em; text-transform:uppercase; }
  .whale-stat-val { font-size:13px; font-weight:700; color:var(--text); margin-top:2px; }
  .whale-remove-btn {
    background:transparent; border:1px solid var(--border2); color:var(--text2);
    padding:3px 8px; border-radius:3px; cursor:pointer; font-size:9px;
    font-family:'IBM Plex Mono',monospace; letter-spacing:0.1em;
    transition:all 0.15s;
  }
  .whale-remove-btn:hover { border-color:#f85149; color:#f85149; }

  /* Whale feed rows */
  .wf-row {
    display:grid;
    grid-template-columns: 80px 36px 36px 70px 60px 1fr auto;
    align-items:center;
    gap:8px;
    padding:8px 12px;
    border-radius:4px;
    border: 1px solid var(--border);
    background: var(--panel);
    font-size:11px;
    font-family:'IBM Plex Mono',monospace;
    transition: border-color 0.15s;
  }
  .wf-row:hover { border-color: var(--border2); }
  .wf-row.buy  { border-left: 3px solid var(--go); }
  .wf-row.sell { border-left: 3px solid var(--danger); }
  .wf-whale  { font-weight:700; color:var(--blue); font-size:10px; }
  .wf-side-buy  { color:var(--go); font-weight:700; }
  .wf-side-sell { color:var(--danger); font-weight:700; }
  .wf-outcome-yes { color:var(--ice); font-weight:700; }
  .wf-outcome-no  { color:var(--blue); font-weight:700; }
  .wf-size   { color:var(--text); font-weight:600; }
  .wf-price  { color:var(--text2); }
  .wf-title  { color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .wf-filter {
    background:transparent; border:1px solid var(--border2); color:var(--text2);
    padding:4px 10px; border-radius:3px; cursor:pointer; font-size:9px;
    font-family:'IBM Plex Mono',monospace; letter-spacing:0.12em; text-transform:uppercase;
    transition:all 0.15s;
  }
  .wf-filter.active, .wf-filter:hover { border-color:var(--blue); color:var(--blue); }

  /* Refresh indicator — amber temperatuurbalk */
  .refresh-bar {
    height: 2px;
    background: linear-gradient(90deg, var(--ice), var(--warn), var(--go), transparent);
    transform-origin: left;
    animation: shrink 30s linear infinite;
    margin-bottom: 18px;
    opacity: 0.5;
    border-radius: 1px;
  }

  @keyframes shrink {
    from { transform: scaleX(1); }
    to   { transform: scaleX(0); }
  }
</style>
</head>
<body>

<header class="header">
  <div class="logo">
    <div class="logo-mark"></div>
    <div class="logo-text">POLY<span>MARKET</span> TERMINAL</div>
  </div>
  <div class="header-right">
    <span><span class="live-dot"></span>LIVE DATA</span>
    <span id="clock">--:--:--</span>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="setTab('weer', this)">WEER ARB</div>
  <div class="tab" onclick="setTab('portfolio', this)">WEER PORTFOLIO</div>
  <div class="tab" onclick="setTab('crypto', this)">CRYPTO PORTFOLIO</div>
  <div class="tab" onclick="setTab('momentum', this)">⚡ BTC MOMENTUM</div>
  <div class="tab" onclick="setTab('wallets', this)">WALLETS</div>
  <div class="tab" onclick="setTab('whalefeed', this)">WHALE FEED</div>
  <div class="tab" onclick="setTab('flow', this)">FLOW</div>
  <div class="tab" onclick="setTab('markten', this)">MARKTEN</div>
  <div class="tab" onclick="setTab('auto', this)">AUTO</div>
  <div class="tab" onclick="setTab('settings', this)">INSTELLINGEN</div>
</div>

<div class="main">

  <!-- Sports Arb Panel (verborgen, bewaard voor toekomstig gebruik) -->
  <div id="sports-panel" style="display:none">
    <div class="sports-header">
      <div style="font-size:11px;color:var(--text2)">
        Polymarket vs. bookmakers — kansen waar Polymarket <span style="color:var(--amber)">achterloopt</span> op bookmaker odds
      </div>
      <button class="scan-btn" id="scan-btn" onclick="runSportsScan()">⟳ SCAN NU</button>
    </div>
    <div class="sports-table-wrap">
      <div class="sports-table-header">
        <div>Markt</div>
        <div>Team</div>
        <div>Polymarket</div>
        <div>Bookmaker</div>
        <div>GAP</div>
        <div>Beste book</div>
        <div>Trade</div>
      </div>
      <div id="sports-list">
        <div class="gap-empty">Klik op SCAN NU om te scannen</div>
      </div>
    </div>
    <div style="margin-top:10px;font-size:10px;color:var(--muted)" id="sports-scan-time"></div>
  </div>

  <!-- F1 Weather Panel -->
  <div id="f1-panel" style="display:none">
    <div class="sports-header">
      <div style="font-size:11px;color:var(--text2)">
        F1 regenradar — Polymarket odds gecorrigeerd voor nat-prestatie per coureur
      </div>
      <button class="scan-btn" id="f1-scan-btn" onclick="runF1Scan()">⟳ SCAN NU</button>
    </div>

    <!-- Aankomende race info -->
    <div id="f1-race-info" style="display:none;margin-bottom:16px">
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:14px 16px">
        <div style="font-size:10px;color:var(--text2);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:8px">Aankomende Race</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px">
          <div><div style="font-size:10px;color:var(--muted)">Race</div><div style="font-size:13px;color:var(--text)" id="f1-race-name">—</div></div>
          <div><div style="font-size:10px;color:var(--muted)">Datum</div><div style="font-size:13px;color:var(--text)" id="f1-race-date">—</div></div>
          <div><div style="font-size:10px;color:var(--muted)">Regenprognose</div><div style="font-size:13px;font-weight:600" id="f1-rain-pct">—</div></div>
          <div><div style="font-size:10px;color:var(--muted)">Neerslag</div><div style="font-size:13px;color:var(--text)" id="f1-rain-mm">—</div></div>
        </div>
      </div>
    </div>

    <div class="sports-table-wrap">
      <div class="sports-table-header" style="grid-template-columns:1fr 90px 80px 80px 70px 90px 80px 70px">
        <div>Markt</div>
        <div>Coureur</div>
        <div>Poly %</div>
        <div>Regen-adj</div>
        <div>GAP</div>
        <div>Nat delta</div>
        <div>Actie</div>
        <div>Trade</div>
      </div>
      <div id="f1-list">
        <div class="gap-empty">Klik op SCAN NU om te scannen</div>
      </div>
    </div>
    <div style="margin-top:10px;font-size:10px;color:var(--muted)" id="f1-scan-time"></div>
  </div>

  <!-- WEER ARB Panel -->
  <div class="weer-panel" id="weer-panel">
    <div class="weer-scan-row">
      <div class="weer-scan-info">
        Weermodel (ECMWF via Open-Meteo) vs Polymarket temperatuurmarkten — <span id="weer-opp-count">—</span> kansen
      </div>
      <button class="scan-btn" id="weer-scan-btn" onclick="runWeerScan()">⟳ SCAN NU</button>
    </div>

    <!-- Kaart + grid layout -->
    <div style="display:grid;grid-template-columns:1fr 320px;gap:14px;margin-bottom:14px" id="weer-main-layout">

      <!-- Kansen grid -->
      <div id="weer-opps" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;align-content:start">
        <div class="gap-empty" style="color:#4a6080;grid-column:1/-1">Klik SCAN NU om te starten</div>
      </div>

      <!-- Heatmap kaart — lichte achtergrond, gekleurde kans-bubbles -->
      <div style="position:relative">
        <div style="font-size:9px;color:var(--text2);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:6px">KANSEN KAART</div>
        <div id="weather-map" style="width:100%;height:340px;border-radius:4px;border:1px solid var(--border2);overflow:hidden"></div>
        <div id="map-empty" style="display:none;position:absolute;inset:22px 0 0 0;display:flex;align-items:center;justify-content:center;font-size:11px;color:#4a6080;pointer-events:none">geen kansen</div>
      </div>
    </div>

    <div style="margin-bottom:6px;font-size:10px;color:var(--text2);letter-spacing:0.15em;text-transform:uppercase" id="weer-scan-time"></div>

    <!-- Windy temperatuurmodel embed — compact onderaan -->
    <div class="windy-wrap" id="windy-wrap">
      <div class="windy-label">ECMWF TEMPERATUURMODEL</div>
      <iframe id="windy-frame"
        src="https://embed.windy.com/embed2.html?lat=48&lon=10&zoom=4&level=surface&overlay=temp&product=ecmwf&menu=&message=&marker=&calendar=now&pressure=&type=map&location=coordinates&detail=&metricWind=default&metricTemp=default&radarRange=-1"
        allowfullscreen>
      </iframe>
    </div>
  </div>

  <!-- MARKTEN panel -->
  <!-- Flow Scanner Panel -->
  <div id="flow-panel" style="display:none">
    <div class="scanner-header">
      <div>
        <div class="scanner-title">SMART MONEY FLOW</div>
        <div class="scanner-sub" id="flow-sub">Gecoördineerde wallets op dezelfde markt — mogelijk insider of sterke conviction</div>
      </div>
      <button class="scan-btn" onclick="loadFlowSignals()">⟳ REFRESH</button>
    </div>

    <!-- Stats row -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px">
      <div class="stat-card" style="padding:12px"><div class="stat-label">SIGNALEN VANDAAG</div><div class="stat-val" id="flow-count">—</div></div>
      <div class="stat-card" style="padding:12px"><div class="stat-label">LAATSTE SIGNAAL</div><div class="stat-val" id="flow-last">—</div></div>
      <div class="stat-card" style="padding:12px"><div class="stat-label">GROOTSTE FLOW</div><div class="stat-val" id="flow-biggest">—</div></div>
      <div class="stat-card" style="padding:12px"><div class="stat-label">TOP CATEGORIE</div><div class="stat-val" id="flow-topcat">—</div></div>
    </div>

    <!-- Signals table -->
    <div style="background:var(--panel2);border:1px solid var(--border);border-radius:6px;overflow:hidden">
      <div style="display:grid;grid-template-columns:2fr 90px 60px 60px 70px 90px 80px 70px 50px;gap:0;padding:8px 12px;border-bottom:1px solid var(--border);font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.08em">
        <div>Markt</div><div>Type</div><div>Richting</div><div>Prijs</div><div>Wallets</div><div>Flow</div><div>Categorie</div><div>Tijd</div><div></div>
      </div>
      <div id="flow-list" style="font-size:12px">
        <div style="padding:24px;text-align:center;color:var(--text2)">Laden...</div>
      </div>
    </div>
  </div>

  <div id="markten-panel" style="display:none">

    <!-- Stats bar -->
    <div style="font-size:10px;color:var(--text2);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:10px">
      Weer &amp; Temperatuurmarkten op Polymarket
    </div>
    <div class="stats-bar" style="margin-bottom:14px">
      <div class="stat-card">
        <div class="stat-label">Weer markten</div>
        <div class="stat-value green" id="stat-count">—</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Top vol 24h</div>
        <div class="stat-value amber" id="stat-topvol">—</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Gem. YES prijs</div>
        <div class="stat-value" id="stat-avgyes">—</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Laatste update</div>
        <div class="stat-value" id="stat-time" style="font-size:13px">—</div>
      </div>
    </div>

    <div class="refresh-bar" id="refresh-bar"></div>
    <div class="table-wrap" id="markets-panel">
      <div class="table-header">
        <div>Markt</div>
        <div>YES</div>
        <div>NO</div>
        <div>Vol 24h</div>
        <div>Sluit</div>
        <div>Trade</div>
      </div>
      <div id="market-list">
        <div class="loading"><div class="spinner"></div> Markten laden...</div>
      </div>
    </div>
  </div>
</div>
</div>

<!-- Trade Modal -->
<!-- Whales Panel -->
<!-- CRYPTO PORTFOLIO panel -->
<div class="weer-panel" id="crypto-panel" style="display:none">

  <div class="scanner-header">
    <div>
      <div class="scanner-sub" id="wp-sub">Whale copy trades · $500 startkapitaal</div>
      <div class="scanner-title">CRYPTO PORTFOLIO</div>
    </div>
    <div style="display:flex;gap:8px">
      <button class="scan-btn" onclick="refreshWhalePf()">⟳ REFRESH</button>
    </div>
  </div>

  <!-- Stats rij 1: waarden -->
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:8px">
    <div class="stat-card" style="padding:12px"><div class="stat-label">EQUITY</div><div class="stat-val" id="wp-equity">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">CASH</div><div class="stat-val" id="wp-cash">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">REALIZED P&L</div><div class="stat-val" id="wp-rpnl">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">UNREALIZED P&L</div><div class="stat-val" id="wp-upnl">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">WIN RATE</div><div class="stat-val" id="wp-wr">—</div></div>
  </div>
  <!-- Stats rij 2: risk metrics -->
  <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:8px;margin-bottom:16px">
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)"><div class="stat-label">SHARPE</div><div class="stat-val" id="wp-sharpe">—</div></div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)"><div class="stat-label">SORTINO</div><div class="stat-val" id="wp-sortino">—</div></div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)"><div class="stat-label">CALMAR</div><div class="stat-val" id="wp-calmar">—</div></div>
    <div class="stat-card" style="padding:12px;border-color:rgba(255,80,80,0.2)"><div class="stat-label">MAX DRAWDOWN</div><div class="stat-val" id="wp-maxdd">—</div></div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)"><div class="stat-label">PROFIT FACTOR</div><div class="stat-val" id="wp-pf">—</div></div>
    <div class="stat-card" style="padding:12px;border-color:rgba(255,180,0,0.2)"><div class="stat-label">VAR 95%</div><div class="stat-val" id="wp-var95">—</div></div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)"><div class="stat-label">KELLY %</div><div class="stat-val" id="wp-kelly">—</div></div>
  </div>

  <!-- Equity curve -->
  <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase">Equity Curve</div>
  <div id="wp-curve-wrap" style="background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:16px;height:120px;position:relative">
    <svg id="wp-curve-svg" width="100%" height="100%" style="overflow:visible"></svg>
    <div id="wp-curve-empty" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--text2);font-size:12px">Nog geen historische data</div>
  </div>

  <!-- Posities tabel -->
  <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase">Posities</div>
  <div id="wp-positions"></div>

  <!-- Auto-follow status -->
  <div style="background:rgba(0,223,122,0.06);border:1px solid rgba(0,223,122,0.25);border-radius:6px;padding:10px 16px;margin:20px 0 16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div style="display:flex;align-items:center;gap:10px">
      <div style="width:8px;height:8px;border-radius:50%;background:var(--go);box-shadow:0 0 6px var(--go)"></div>
      <span style="font-size:10px;color:var(--go);font-weight:700;letter-spacing:0.15em">AUTO-FOLLOW ACTIEF</span>
      <span style="font-size:10px;color:var(--text2)" id="af-config">—</span>
    </div>
    <div id="af-last" style="font-size:10px;color:var(--text2)">Laatste check: —</div>
  </div>

  <!-- Recent auto-copies log -->
  <div id="af-log-wrap" style="display:none;margin-bottom:16px">
    <div style="font-size:9px;color:var(--text2);letter-spacing:0.15em;margin-bottom:6px">RECENTE AUTO-COPIES</div>
    <div id="af-log"></div>
  </div>

</div>

<!-- BTC MOMENTUM panel -->
<div class="weer-panel" id="momentum-panel" style="display:none">

  <div class="scanner-header">
    <div>
      <div class="scanner-sub">4-minuten regel · BTC/USDT · Binance realtime</div>
      <div class="scanner-title">⚡ BTC MOMENTUM TRADER</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <div id="mom-btc-price" style="font-size:13px;color:var(--ice);font-weight:700">BTC $—</div>
      <button class="scan-btn" id="mom-toggle-btn" onclick="toggleMomentum()">▶ START</button>
      <button class="scan-btn" onclick="loadMomentum()">⟳</button>
    </div>
  </div>

  <!-- Status balk -->
  <div id="mom-status-bar" style="display:flex;align-items:center;gap:12px;background:rgba(255,200,0,0.06);border:1px solid rgba(255,200,0,0.2);border-radius:6px;padding:10px 16px;margin-bottom:16px">
    <div id="mom-dot" style="width:8px;height:8px;border-radius:50%;background:#555"></div>
    <span id="mom-status-text" style="font-size:10px;color:var(--text2);font-weight:700;letter-spacing:0.1em">GESTOPT</span>
    <span id="mom-dry-tag" style="font-size:10px;background:rgba(255,180,0,0.15);color:#ffb400;padding:2px 7px;border-radius:3px;display:none">DRY RUN</span>
    <span style="flex:1"></span>
    <span style="font-size:10px;color:var(--text2)">Strategy: als 4 opeenvolgende 1m-candles zelfde richting → continuation bet</span>
  </div>

  <!-- Stats -->
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:16px">
    <div class="stat-card" style="padding:12px"><div class="stat-label">TRADES VANDAAG</div><div class="stat-val" id="mom-trades-today">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">TOTAAL TRADES</div><div class="stat-val" id="mom-trades-total">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">WIN RATE</div><div class="stat-val" id="mom-wr">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">TOTAAL P&L</div><div class="stat-val" id="mom-pnl">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">LAATSTE SIGNAL</div><div class="stat-val" id="mom-signal">—</div></div>
  </div>

  <!-- Strategie uitleg -->
  <div style="background:rgba(0,200,255,0.04);border:1px solid rgba(0,200,255,0.12);border-radius:6px;padding:14px 18px;margin-bottom:16px">
    <div style="font-size:10px;color:var(--text2);letter-spacing:0.12em;margin-bottom:10px">STRATEGIE — 4-MINUTEN REGEL</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;font-size:11px;color:var(--text)">
      <div>
        <div style="color:var(--go);font-weight:700;margin-bottom:4px">96.8% win rate (perfecte setup)</div>
        <div style="color:var(--text2)">Als alle 4 minuten dezelfde kant opgaan</div>
      </div>
      <div>
        <div style="color:var(--ice);font-weight:700;margin-bottom:4px">78.5% overall win rate</div>
        <div style="color:var(--text2)">Over alle 12.000 geanalyseerde trades</div>
      </div>
      <div>
        <div style="color:#ffb400;font-weight:700;margin-bottom:4px">Max 6 verliezende trades op rij</div>
        <div style="color:var(--text2)">In 12.000 trades dataset (0.5% max drawdown)</div>
      </div>
    </div>
  </div>

  <!-- Log -->
  <div style="font-size:9px;color:var(--text2);letter-spacing:0.15em;margin-bottom:6px">LIVE LOG</div>
  <div id="mom-log" style="background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:10px;max-height:200px;overflow-y:auto;font-size:11px;font-family:monospace;color:var(--text2)">
    Wachten op start...
  </div>

</div>

<!-- WALLETS panel -->
<div class="weer-panel" id="wallets-panel" style="display:none">

  <!-- ── GEVOLGDE TRADERS ──────────────────────────────────────── -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin:24px 0 12px">
    <div>
      <div style="font-size:11px;color:var(--text2);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:2px">Gevolgde Traders</div>
      <div style="font-size:16px;font-weight:800;color:var(--text)">WHALE WALLETS</div>
    </div>
    <button class="scan-btn" onclick="loadWallets()">⟳ REFRESH</button>
  </div>

  <!-- Add whale form -->
  <div style="background:var(--panel);border:1px solid var(--blue);border-radius:6px;padding:14px 16px;margin-bottom:20px">
    <div style="font-size:9px;color:var(--blue);letter-spacing:0.2em;margin-bottom:10px">WHALE TOEVOEGEN</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <input id="wp-address" type="text" placeholder="0x wallet adres (42 chars)"
        style="flex:2;min-width:220px;background:var(--deep);border:1px solid var(--border2);color:var(--text);padding:8px 12px;border-radius:4px;font-family:'IBM Plex Mono',monospace;font-size:12px;outline:none">
      <input id="wp-name" type="text" placeholder="Naam (bijv. BTC_Floor)"
        style="flex:1;min-width:120px;background:var(--deep);border:1px solid var(--border2);color:var(--text);padding:8px 12px;border-radius:4px;font-family:'IBM Plex Mono',monospace;font-size:12px;outline:none">
      <input id="wp-note" type="text" placeholder="Notitie (optioneel)"
        style="flex:2;min-width:160px;background:var(--deep);border:1px solid var(--border2);color:var(--text);padding:8px 12px;border-radius:4px;font-family:'IBM Plex Mono',monospace;font-size:12px;outline:none">
      <select id="wp-portfolio" style="background:var(--deep);border:1px solid var(--border2);color:var(--text);padding:8px 12px;border-radius:4px;font-family:'IBM Plex Mono',monospace;font-size:12px;outline:none;cursor:pointer">
        <option value="crypto">₿ CRYPTO portfolio</option>
        <option value="weather">🌡 WEATHER portfolio</option>
      </select>
      <button onclick="addWalletWhale()" style="background:rgba(26,184,255,0.15);border:1px solid var(--blue);color:var(--blue);padding:8px 16px;border-radius:4px;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;letter-spacing:0.1em;white-space:nowrap">+ VOEG TOE</button>
    </div>
    <div id="wp-add-msg" style="font-size:11px;margin-top:8px;color:var(--text2)"></div>
  </div>

  <!-- Whale cards grid -->
  <div id="wallets-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px">
    <div style="color:var(--text2);padding:40px;text-align:center;grid-column:1/-1">Laden...</div>
  </div>
</div>

<!-- WHALE FEED panel -->
<div class="weer-panel" id="whalefeed-panel" style="display:none">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <div>
      <div style="font-size:11px;color:var(--text2);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:4px">Live activiteit</div>
      <div style="font-size:20px;font-weight:800;color:var(--text);letter-spacing:0.05em">WHALE FEED</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <span id="wf-count" style="font-size:10px;color:var(--text2)">—</span>
      <button class="scan-btn" onclick="loadWhaleFeed()">⟳ REFRESH</button>
    </div>
  </div>

  <!-- Filter bar -->
  <div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap" id="wf-filters">
    <button class="wf-filter active" data-filter="all" onclick="setWfFilter('all',this)">ALLE</button>
  </div>

  <!-- Feed list -->
  <div id="wf-list" style="display:flex;flex-direction:column;gap:4px">
    <div style="color:var(--text2);padding:40px;text-align:center">Klik REFRESH om live trades te laden</div>
  </div>
</div>

<!-- Old whales panel (kept for internal whale scanner) -->
<div id="whales-panel" style="display:none"></div>

<!-- Hurricane Panel -->
<div class="weer-panel" id="hurricane-panel" style="display:none">
  <div class="scanner-header">
    <div>
      <div class="scanner-title">HURRICANE SCANNER</div>
      <div class="scanner-sub" id="hurricane-sub">NHC officiële stormdata + historische klimatologie vs Polymarket odds</div>
    </div>
    <button class="scan-btn" id="hurricane-scan-btn" onclick="runHurricaneScan()">⟳ SCAN NU</button>
  </div>

  <!-- Season info bar -->
  <div id="hurricane-season-bar" style="display:none; background:var(--panel); border:1px solid var(--ice3); border-radius:6px; padding:12px 16px; margin-bottom:16px; font-size:13px; color:var(--text);">
  </div>

  <div id="hurricane-list"><div style="color:var(--text2); padding:32px; text-align:center;">Klik SCAN NU om hurricane-markten te analyseren</div></div>
</div>

<!-- Portfolio Panel -->
<div class="weer-panel" id="portfolio-panel" style="display:none">
  <div class="scanner-header">
    <div>
      <div class="scanner-title">WEATHER PORTFOLIO</div>
      <div class="scanner-sub" id="portfolio-sub">Automatische trades bijhouden</div>
    </div>
    <div style="display:flex;gap:8px">
      <button class="scan-btn" onclick="refreshPortfolio()">⟳ REFRESH</button>
      <button id="pf-update-prices-btn" onclick="updatePortfolioPrices()" style="background:rgba(0,200,255,0.1);border:1px solid var(--accent);color:var(--accent);padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;font-family:inherit">↻ UPDATE PRICES</button>
      <button onclick="backupPortfolio()" style="background:rgba(0,255,150,0.08);border:1px solid var(--go);color:var(--go);padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;font-family:inherit">💾 BACKUP</button>
      <button onclick="confirmReset()" style="background:rgba(255,51,85,0.1);border:1px solid var(--danger);color:var(--danger);padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;font-family:inherit">RESET</button>
    </div>
  </div>

  <!-- Stats row 1: portfolio waarden -->
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:8px">
    <div class="stat-card" style="padding:12px"><div class="stat-label">EQUITY</div><div class="stat-val" id="pf-equity">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">CASH</div><div class="stat-val" id="pf-cash">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">REALIZED P&L</div><div class="stat-val" id="pf-realized">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">UNREALIZED P&L</div><div class="stat-val" id="pf-unrealized">—</div></div>
    <div class="stat-card" style="padding:12px"><div class="stat-label">WIN RATE</div><div class="stat-val" id="pf-winrate">—</div></div>
  </div>
  <!-- Stats row 2: risk metrics -->
  <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:8px;margin-bottom:16px">
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)">
      <div class="stat-label" title="Rendement / totale volatiliteit — >1 goed, >2 uitstekend, >3 hedge fund kwaliteit">SHARPE</div>
      <div class="stat-val" id="pf-sharpe">—</div>
    </div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)">
      <div class="stat-label" title="Rendement / downside volatiliteit — beter dan Sharpe voor skewed returns">SORTINO</div>
      <div class="stat-val" id="pf-sortino">—</div>
    </div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)">
      <div class="stat-label" title="Jaarrendement / max drawdown — >3 professioneel">CALMAR</div>
      <div class="stat-val" id="pf-calmar">—</div>
    </div>
    <div class="stat-card" style="padding:12px;border-color:rgba(255,80,80,0.2)">
      <div class="stat-label" title="Diepste equity daling piek→dal">MAX DRAWDOWN</div>
      <div class="stat-val" id="pf-maxdd">—</div>
    </div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)">
      <div class="stat-label" title="Bruto winst / bruto verlies — >1.5 goed, >2 uitstekend">PROFIT FACTOR</div>
      <div class="stat-val" id="pf-pf">—</div>
    </div>
    <div class="stat-card" style="padding:12px;border-color:rgba(255,180,0,0.2)">
      <div class="stat-label" title="Slechtste 5% van trades (Value at Risk 95%)">VAR 95%</div>
      <div class="stat-val" id="pf-var95">—</div>
    </div>
    <div class="stat-card" style="padding:12px;border-color:rgba(0,200,255,0.2)">
      <div class="stat-label" title="Kelly Criterion — optimale inzetgrootte als % van bankroll">KELLY %</div>
      <div class="stat-val" id="pf-kelly">—</div>
    </div>
  </div>

  <!-- Trade source split: whale vs model -->
  <div style="background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:12px 16px;margin-bottom:12px;display:flex;align-items:center;gap:20px;flex-wrap:wrap">
    <div style="font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.08em;margin-right:4px">Trade bron</div>
    <div style="display:flex;align-items:center;gap:6px">
      <span style="font-size:12px">🐋</span>
      <div>
        <div style="font-size:10px;color:var(--text2)">Whale copy</div>
        <div style="font-size:14px;font-weight:700;color:#ffc800" id="src-whale-trades">—</div>
      </div>
    </div>
    <div style="color:var(--border);font-size:18px">|</div>
    <div style="display:flex;align-items:center;gap:6px">
      <span style="font-size:12px;background:rgba(0,200,255,0.1);color:var(--accent);border:1px solid var(--accent);border-radius:3px;padding:1px 5px;font-weight:700;font-size:10px">M</span>
      <div>
        <div style="font-size:10px;color:var(--text2)">Model</div>
        <div style="font-size:14px;font-weight:700;color:var(--accent)" id="src-model-trades">—</div>
      </div>
    </div>
    <div style="color:var(--border);font-size:18px">|</div>
    <div>
      <div style="font-size:10px;color:var(--text2)">Whale P&L</div>
      <div style="font-size:13px;font-weight:700" id="src-whale-pnl">—</div>
    </div>
    <div>
      <div style="font-size:10px;color:var(--text2)">Model P&L</div>
      <div style="font-size:13px;font-weight:700" id="src-model-pnl">—</div>
    </div>
  </div>

  <!-- Equity curve -->
  <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase;margin-top:8px">Equity Curve</div>
  <div id="equity-curve-wrap" style="background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:16px;height:120px;position:relative">
    <svg id="equity-curve-svg" width="100%" height="100%" style="overflow:visible"></svg>
    <div id="equity-curve-empty" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--text2);font-size:12px">Geen historische data — log wordt gevuld naarmate de trader draait</div>
  </div>

  <!-- Positions table -->
  <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase">Posities</div>
  <div id="portfolio-positions" style="font-size:12px">
    <div style="color:var(--text2);padding:32px;text-align:center">Klik REFRESH — of laat auto trader draaien om posities te vullen</div>
  </div>
</div>

<!-- Auto Trade Panel -->
<div class="weer-panel" id="auto-panel" style="display:none">
  <div class="scanner-header">
    <div>
      <div class="scanner-title">AUTO TRADER</div>
      <div class="scanner-sub" id="auto-sub">Automatisch handelen op weather scanner kansen</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <label class="toggle-wrap" title="Schakel auto-trading in/uit">
        <input type="checkbox" id="auto-enabled-toggle" onchange="toggleAutoTrader(this.checked)">
        <span class="toggle-slider"></span>
      </label>
      <span id="auto-status-badge" style="font-size:11px;padding:3px 8px;border-radius:3px;background:var(--ice3);color:var(--text2)">IDLE</span>
    </div>
  </div>

  <!-- Config sliders -->
  <div id="auto-config-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px;">
    <div class="config-card">
      <div class="config-label">MIN GAP</div>
      <input type="range" min="10" max="40" value="20" class="cfg-slider" id="cfg-min-gap" oninput="updateCfgLabel('cfg-min-gap','cfg-min-gap-val','%')">
      <div class="config-val" id="cfg-min-gap-val">20%</div>
    </div>
    <div class="config-card">
      <div class="config-label">MAX TRADE</div>
      <input type="range" min="5" max="100" value="25" class="cfg-slider" id="cfg-max-trade" oninput="updateCfgLabel('cfg-max-trade','cfg-max-trade-val','$')">
      <div class="config-val" id="cfg-max-trade-val">$25</div>
    </div>
    <div class="config-card">
      <div class="config-label">DAGBUDGET</div>
      <input type="range" min="50" max="1000" step="50" value="200" class="cfg-slider" id="cfg-budget" oninput="updateCfgLabel('cfg-budget','cfg-budget-val','$')">
      <div class="config-val" id="cfg-budget-val">$200</div>
    </div>
    <div class="config-card">
      <div class="config-label">INTERVAL</div>
      <input type="range" min="5" max="60" step="5" value="10" class="cfg-slider" id="cfg-interval" oninput="updateCfgLabel('cfg-interval','cfg-interval-val','m')">
      <div class="config-val" id="cfg-interval-val">10m</div>
    </div>
  </div>

  <div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text);cursor:pointer">
      <input type="checkbox" id="dry-run-toggle" checked onchange="saveAutoConfig()"> DRY RUN (geen echte orders)
    </label>
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text);cursor:pointer">
      <input type="checkbox" id="whale-copy-toggle" checked onchange="saveAutoConfig()"> WHALE COPY (volg ColdMath)
    </label>
    <button onclick="saveAutoConfig()" style="background:var(--ice3);border:1px solid var(--ice2);color:var(--ice);padding:4px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-family:inherit">OPSLAAN</button>
    <button onclick="runManualScan()" style="background:var(--deep);border:1px solid var(--ice3);color:var(--text);padding:4px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-family:inherit">SCAN NU</button>
  </div>

  <!-- Stats bar -->
  <div id="auto-stats" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px;">
    <div class="stat-card" style="padding:10px"><div class="stat-label">BESTEED VANDAAG</div><div class="stat-val" id="at-spent">$0</div></div>
    <div class="stat-card" style="padding:10px"><div class="stat-label">BUDGET OVER</div><div class="stat-val" id="at-budget">$200</div></div>
    <div class="stat-card" style="padding:10px"><div class="stat-label">DEPLOYED CAP</div><div class="stat-val" id="at-deployed" style="color:var(--text2)">max 90%</div></div>
    <div class="stat-card" style="padding:10px"><div class="stat-label">TRADES VANDAAG</div><div class="stat-val" id="at-trades">0</div></div>
    <div class="stat-card" style="padding:10px"><div class="stat-label">VOLGENDE SCAN</div><div class="stat-val" id="at-next">—</div></div>
  </div>

  <!-- Trade log -->
  <div style="display:grid;grid-template-columns:2fr 1fr;gap:12px">
    <div>
      <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase">Live Log</div>
      <div id="auto-log" style="background:var(--deep);border:1px solid var(--ice3);border-radius:4px;padding:10px;font-size:11px;font-family:'JetBrains Mono',monospace;height:300px;overflow-y:auto;color:var(--text2)">
        Nog geen activiteit...
      </div>
    </div>
    <div>
      <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase">Recente Trades</div>
      <div id="auto-trades" style="font-size:12px">—</div>
    </div>
  </div>
</div>

<!-- Settings Panel -->
<div class="weer-panel" id="settings-panel" style="display:none">
  <div class="scanner-title" style="margin-bottom:20px">INSTELLINGEN</div>

  <!-- Telegram -->
  <div style="background:var(--panel);border:1px solid var(--ice3);border-radius:8px;padding:20px;margin-bottom:16px">
    <div style="color:var(--ice);font-weight:700;margin-bottom:12px">TELEGRAM ALERTS</div>
    <div style="display:grid;gap:8px;font-size:13px">
      <div style="display:flex;gap:8px;align-items:center">
        <span style="color:var(--text2);width:140px">Bot Token:</span>
        <input id="tg-token" type="password" placeholder="1234567890:AABBcc..."
          style="flex:1;background:var(--deep);border:1px solid var(--ice3);color:var(--text);padding:6px 10px;border-radius:4px;font-family:inherit;font-size:12px">
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span style="color:var(--text2);width:140px">Chat ID(s):</span>
        <input id="tg-chat" type="text" placeholder="123456789, 987654321"
          style="flex:1;background:var(--deep);border:1px solid var(--ice3);color:var(--text);padding:6px 10px;border-radius:4px;font-family:inherit;font-size:12px">
      </div>
      <div style="color:var(--text2);font-size:11px;margin-left:148px">Meerdere ontvangers: komma-gescheiden. Stuur je bot een bericht en open getUpdates om je chat.id te vinden.</div>
      <div style="display:flex;gap:8px;align-items:center">
        <span style="color:var(--text2);width:140px">Min gap alert:</span>
        <input id="tg-min-gap" type="number" value="20" min="10" max="50" step="5"
          style="width:70px;background:var(--deep);border:1px solid var(--ice3);color:var(--text);padding:6px 10px;border-radius:4px;font-family:inherit;font-size:12px">
        <span style="color:var(--text2)">%</span>
      </div>
      <div style="display:flex;gap:8px;margin-top:4px">
        <button onclick="saveTelegramSettings()" style="background:var(--ice3);border:1px solid var(--ice2);color:var(--ice);padding:6px 16px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:12px">OPSLAAN</button>
        <button onclick="testTelegram()" style="background:var(--deep);border:1px solid var(--ice3);color:var(--text);padding:6px 16px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:12px">TEST VERBINDING</button>
        <span id="tg-status" style="font-size:12px;color:var(--text2);align-self:center"></span>
      </div>
    </div>
    <div style="color:var(--text2);font-size:11px;margin-top:12px;line-height:1.6">
      Setup: open Telegram → zoek <b>@BotFather</b> → /newbot → kopieer token hier.<br>
      Stuur je bot een bericht, open dan: api.telegram.org/bot&lt;TOKEN&gt;/getUpdates → kopieer chat.id
    </div>
  </div>

  <!-- Alert drempels -->
  <div style="background:var(--panel);border:1px solid var(--ice3);border-radius:8px;padding:20px">
    <div style="color:var(--ice);font-weight:700;margin-bottom:12px">ALERT DREMPELS</div>
    <div style="display:grid;gap:10px;font-size:13px">
      <div style="display:flex;gap:8px;align-items:center">
        <span style="color:var(--text2);width:200px">Weer ARB alert bij gap ≥</span>
        <input id="alert-weer-gap" type="number" value="20" min="10" max="50"
          style="width:60px;background:var(--deep);border:1px solid var(--ice3);color:var(--text);padding:6px 10px;border-radius:4px;font-family:inherit;font-size:12px">
        <span style="color:var(--text2)">%</span>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text)">
          <input type="checkbox" id="alert-on-scan"> Alert na elke scan (ook zonder kansen)
        </label>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text)">
          <input type="checkbox" id="alert-auto-trade" checked> Alert bij elke auto-trade
        </label>
      </div>
      <button onclick="saveAlertSettings()" style="width:fit-content;background:var(--ice3);border:1px solid var(--ice2);color:var(--ice);padding:6px 16px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:12px">OPSLAAN</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-title">POSITIE OPENEN</div>
    <div class="modal-question" id="modal-question">—</div>

    <div class="modal-prices">
      <div class="price-opt yes selected" id="opt-yes" onclick="selectSide('yes')">
        <div class="opt-label">YES</div>
        <div class="opt-price" id="modal-yes-price">—</div>
      </div>
      <div class="price-opt no" id="opt-no" onclick="selectSide('no')">
        <div class="opt-label">NO</div>
        <div class="opt-price" id="modal-no-price">—</div>
      </div>
    </div>

    <!-- Kelly Calculator -->
    <div class="kelly-block">
      <div class="kelly-header">
        <span class="kelly-title">KELLY CALCULATOR</span>
        <span class="kelly-sub">Wat denk jij dat de echte kans is?</span>
      </div>
      <div class="kelly-row">
        <span class="kelly-label">Mijn kans:</span>
        <input type="range" id="kelly-slider" min="1" max="99" value="50" oninput="updateKelly()" class="kelly-slider">
        <span class="kelly-pct" id="kelly-pct">50%</span>
      </div>
      <div class="kelly-row">
        <span class="kelly-label">Bankroll:</span>
        <input type="number" id="kelly-bankroll" value="500" min="10" step="10" oninput="updateKelly()" class="kelly-bankroll-input">
        <span class="kelly-label">USDC</span>
      </div>
      <div class="kelly-result" id="kelly-result"></div>
    </div>

    <div class="amount-row">
      <div class="amount-label">USDC</div>
      <input type="number" class="amount-input" id="amount-input" value="10" min="1" step="1">
      <div class="quick-amounts">
        <button class="qa-btn" onclick="setAmount(10)">$10</button>
        <button class="qa-btn" onclick="setAmount(25)">$25</button>
        <button class="qa-btn" onclick="setAmount(50)">$50</button>
        <button class="qa-btn" onclick="setAmount(100)">$100</button>
      </div>
    </div>

    <div class="modal-footer">
      <button class="btn-cancel" onclick="closeModal()">ANNULEER</button>
      <button class="btn-execute" onclick="executeTrade()">EXECUTE ORDER</button>
    </div>

    <div class="note">
      ⚠ Orders worden geplaatst via je wallet. Zorg dat .env correct is ingesteld.<br>
      Market orders worden direct uitgevoerd tegen de huidige prijs.
    </div>
  </div>
</div>

<script>
let currentTab     = 'weer';
let currentMarket  = null;
let selectedSide   = 'yes';
let markets        = [];

// Clock
setInterval(() => {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('nl-NL');
}, 1000);

function setTab(tab, el) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');

  const isWeer       = tab === 'weer';
  const isPortfolio  = tab === 'portfolio';
  const isCrypto     = tab === 'crypto';
  const isMomentum   = tab === 'momentum';
  const isWallets    = tab === 'wallets';
  const isWhaleFeed  = tab === 'whalefeed';
  const isFlow       = tab === 'flow';
  const isMarkten    = tab === 'markten';
  const isAuto       = tab === 'auto';
  const isSettings   = tab === 'settings';

  document.getElementById('weer-panel').style.display       = isWeer      ? 'block' : 'none';
  document.getElementById('portfolio-panel').style.display  = isPortfolio ? 'block' : 'none';
  document.getElementById('crypto-panel').style.display     = isCrypto    ? 'block' : 'none';
  document.getElementById('momentum-panel').style.display   = isMomentum  ? 'block' : 'none';
  document.getElementById('wallets-panel').style.display    = isWallets   ? 'block' : 'none';
  document.getElementById('whalefeed-panel').style.display  = isWhaleFeed ? 'block' : 'none';
  document.getElementById('flow-panel').style.display       = isFlow      ? 'block' : 'none';
  document.getElementById('markten-panel').style.display    = isMarkten   ? 'block' : 'none';
  document.getElementById('auto-panel').style.display       = isAuto      ? 'block' : 'none';
  document.getElementById('settings-panel').style.display   = isSettings  ? 'block' : 'none';

  if (isWeer) {
    setTimeout(runWeerScan, 400);
  }

  if (isCrypto) {
    setTimeout(async () => {
      await fetch('/api/whale-portfolio/update_prices', {method:'POST'}).catch(()=>{});
      refreshWhalePf();
      loadFollowStatus();
    }, 400);
  }
  if (isMomentum)  setTimeout(loadMomentum, 400);
  if (isWallets)   setTimeout(loadWallets, 400);
  if (isWhaleFeed) setTimeout(loadWhaleFeed, 400);
  if (isPortfolio) {
    setTimeout(async () => {
      await fetch('/api/portfolio/update_prices', {method:'POST'});
      refreshPortfolio();
      refreshEquityCurve();
    }, 400);
  }
  if (isFlow)      setTimeout(loadFlowSignals, 400);
  if (isAuto)      setTimeout(refreshAutoStatus, 400);
  if (isSettings)  setTimeout(loadSettings, 400);
  if (isMarkten) {
    loadMarkets();
    const bar = document.getElementById('refresh-bar');
    if (bar) { bar.style.animation = 'none'; bar.offsetHeight; bar.style.animation = 'shrink 30s linear infinite'; }
  }
}


async function loadFlowSignals() {
  document.getElementById('flow-sub').textContent = 'Ophalen...';
  try {
    const r = await fetch('/api/flow-signals');
    const d = await r.json();
    const signals = d.signals || [];

    document.getElementById('flow-count').textContent = signals.length;
    document.getElementById('flow-last').textContent  = signals.length ? signals[0].last : '—';
    document.getElementById('flow-biggest').textContent = signals.length
      ? '$' + Math.max(...signals.map(s => s.total_size)).toLocaleString('nl', {maximumFractionDigits:0})
      : '—';
    const cats = {};
    signals.forEach(s => cats[s.category] = (cats[s.category]||0)+1);
    const topCat = Object.entries(cats).sort((a,b)=>b[1]-a[1])[0];
    document.getElementById('flow-topcat').textContent = topCat ? topCat[0].toUpperCase() : '—';

    const list = document.getElementById('flow-list');
    if (!signals.length) {
      list.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text2)">Nog geen signalen — scanner draait elke 5 minuten.</div>';
      document.getElementById('flow-sub').textContent = 'Geen signalen gevonden';
      return;
    }

    const catColor  = {sports:'var(--go)', crypto:'var(--ice)', politics:'var(--warn)', weather:'#a78bfa', other:'var(--text2)'};
    const typeColor = {PROACTIEF:'var(--go)', REACTIEF:'var(--warn)', ONBEKEND:'var(--text2)'};
    const typeIcon  = {PROACTIEF:'🧠', REACTIEF:'⚡', ONBEKEND:'❓'};
    list.innerHTML = signals.map(s => {
      const col     = catColor[s.category]    || 'var(--text2)';
      const tcol    = typeColor[s.signal_type] || 'var(--text2)';
      const ticon   = typeIcon[s.signal_type]  || '';
      const dir     = s.outcome === 'Yes' ? '<span style="color:var(--go)">YES</span>' : '<span style="color:var(--danger)">NO</span>';
      const link    = s.url ? `<a href="${s.url}" target="_blank" style="color:var(--ice);font-size:10px">→</a>` : '';
      const pricePre = s.price_before ? `<span style="color:var(--text2);font-size:10px"> (was ${(s.price_before*100).toFixed(0)}%)</span>` : '';
      return `<div style="display:grid;grid-template-columns:2fr 90px 60px 60px 70px 90px 80px 70px 50px;gap:0;padding:10px 12px;border-bottom:1px solid var(--border);align-items:center">
        <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)">${s.title}</div>
        <div style="color:${tcol};font-weight:700;font-size:11px">${ticon} ${s.signal_type}</div>
        <div>${dir}</div>
        <div style="color:var(--text2)">${(s.price*100).toFixed(0)}%${pricePre}</div>
        <div style="color:var(--text);font-weight:700">${s.wallets}</div>
        <div style="color:var(--go);font-weight:700">$${s.total_size.toLocaleString('nl',{maximumFractionDigits:0})}</div>
        <div style="color:${col}">${s.category.toUpperCase()}</div>
        <div style="color:var(--text2);font-size:11px">${s.first}→${s.last}</div>
        <div>${link}</div>
      </div>`;
    }).join('');

    document.getElementById('flow-sub').textContent = `${signals.length} signalen — laatste scan ${d.last_scan || ''}`;
  } catch(e) {
    document.getElementById('flow-list').innerHTML = `<div style="padding:16px;color:var(--danger)">Fout: ${e}</div>`;
  }
}

async function runSportsScan() {
  const btn  = document.getElementById('scan-btn');
  const list = document.getElementById('sports-list');
  btn.disabled = true;
  btn.textContent = '⟳ SCANNEN...';
  list.innerHTML  = '<div class="loading"><div class="spinner"></div> Bookmakers vergelijken — even geduld (~15s)...</div>';

  try {
    const r    = await fetch('/api/sports');
    const data = await r.json();

    document.getElementById('sports-scan-time').textContent =
      'Laatste scan: ' + new Date().toLocaleTimeString('nl-NL') + ' — ' + data.length + ' kans(en) gevonden';

    if (data.length === 0) {
      list.innerHTML = `<div class="gap-empty">
        Geen kansen gevonden (drempel: ${data.min_gap !== undefined ? (data.min_gap*100).toFixed(0) : 8}%)<br>
        <span style="color:var(--muted);font-size:10px">Probeer later opnieuw — kansen zijn situationeel</span>
      </div>`;
    } else {
      list.innerHTML = data.map((o, i) => {
        const gapPct   = (o.gap * 100).toFixed(1);
        const gapClass = o.gap >= 0.15 ? 'gap-strong' : 'gap-good';
        const delay    = i * 40;
        return `
          <div class="sports-row" style="animation-delay:${delay}ms">
            <div class="market-question" title="${o.question}">${o.question}</div>
            <div style="font-size:11px;color:var(--text)">${o.outcome}</div>
            <div style="color:var(--red);font-weight:600">${(o.poly_price*100).toFixed(0)}%</div>
            <div style="color:var(--green);font-weight:600">${(o.book_price*100).toFixed(0)}%</div>
            <div><span class="gap-badge ${gapClass}">+${gapPct}%</span></div>
            <div style="font-size:10px;color:var(--text2)">${o.best_bookmaker}</div>
            <div>
              <button class="trade-btn" onclick="openSportsTrade(${JSON.stringify(o).replace(/"/g,'&quot;')})">TRADE</button>
            </div>
          </div>`;
      }).join('');
    }
  } catch(e) {
    list.innerHTML = '<div class="loading" style="color:var(--red)">Fout: ' + e.message + '</div>';
  }

  btn.disabled    = false;
  btn.textContent = '⟳ SCAN NU';
}

async function runF1Scan() {
  const btn  = document.getElementById('f1-scan-btn');
  const list = document.getElementById('f1-list');
  btn.disabled = true;
  btn.textContent = '⟳ SCANNEN...';
  list.innerHTML = '<div class="loading"><div class="spinner"></div> F1 kalender + weer ophalen...</div>';

  try {
    const r    = await fetch('/api/f1');
    const data = await r.json();

    if (data.error) {
      list.innerHTML = `<div class="loading" style="color:var(--red)">Fout: ${data.error}</div>`;
      btn.disabled = false; btn.textContent = '⟳ SCAN NU';
      return;
    }

    document.getElementById('f1-scan-time').textContent =
      'Laatste scan: ' + new Date().toLocaleTimeString('nl-NL');

    // Race info tonen
    if (data.race) {
      document.getElementById('f1-race-info').style.display = 'block';
      document.getElementById('f1-race-name').textContent = data.race.name;
      document.getElementById('f1-race-date').textContent = data.race.date;
      const rp = data.race.rain_pct;
      const rpEl = document.getElementById('f1-rain-pct');
      rpEl.textContent = rp + '%';
      rpEl.style.color = rp >= 60 ? 'var(--red)' : rp >= 35 ? 'var(--amber)' : 'var(--green)';
      document.getElementById('f1-rain-mm').textContent = data.race.rain_mm + 'mm';
    }

    const opps = data.opportunities || [];
    if (opps.length === 0) {
      list.innerHTML = `<div class="gap-empty">
        ${data.race ? 'Geen significante kansen (regen < ' + (data.race.rain_pct) + '% of gap < 5%)' : 'Geen race binnen 16 dagen — weerdata nog niet beschikbaar'}<br>
        <span style="color:var(--muted);font-size:10px">Check terug dichter bij de race</span>
      </div>`;
    } else {
      list.innerHTML = opps.map((o, i) => {
        const gapAbs   = Math.abs(o.gap);
        const gapClass = gapAbs >= 10 ? 'gap-strong' : 'gap-good';
        const sign     = o.gap > 0 ? '+' : '';
        const deltaSign = o.wet_delta > 0 ? '+' : '';
        const delay    = i * 40;
        return `
          <div class="sports-row" style="animation-delay:${delay}ms;grid-template-columns:1fr 90px 80px 80px 70px 90px 80px 70px">
            <div class="market-question" title="${o.question}">${o.question}</div>
            <div style="font-size:11px;color:var(--text);text-transform:capitalize">${o.driver}</div>
            <div style="color:var(--text2);font-weight:600">${(o.poly_price*100).toFixed(0)}%</div>
            <div style="color:var(--blue);font-weight:600">${(o.adj_price*100).toFixed(0)}%</div>
            <div><span class="gap-badge ${gapClass}">${sign}${o.gap.toFixed(1)}%</span></div>
            <div style="font-size:10px;color:${o.wet_delta>0?'var(--green)':'var(--red)'}">${deltaSign}${o.wet_delta}% nat</div>
            <div style="font-size:10px;color:var(--amber);font-weight:600">${o.direction}</div>
            <div>
              <button class="trade-btn" onclick="openF1Trade(${JSON.stringify(o).replace(/"/g,'&quot;')})">TRADE</button>
            </div>
          </div>`;
      }).join('');
    }
  } catch(e) {
    list.innerHTML = '<div class="loading" style="color:var(--red)">Fout: ' + e.message + '</div>';
  }

  btn.disabled = false;
  btn.textContent = '⟳ SCAN NU';
}

function openF1Trade(opp) {
  currentMarket = {
    question:    opp.question,
    conditionId: '',
    yes:         opp.poly_price,
    no:          1 - opp.poly_price,
  };
  const action  = opp.direction === 'BUY YES' ? 'YES' : 'NO';
  const adjPct  = (opp.adj_price * 100).toFixed(0);
  document.getElementById('modal-question').textContent  =
    opp.question + ` — Regen-adjusted: ${adjPct}% (${opp.direction})`;
  document.getElementById('modal-yes-price').textContent = (opp.poly_price*100).toFixed(0) + '¢';
  document.getElementById('modal-no-price').textContent  = ((1-opp.poly_price)*100).toFixed(0) + '¢';
  document.getElementById('kelly-slider').value = Math.round(opp.adj_price * 100);
  selectSide(action.toLowerCase());
  updateKelly();
  document.getElementById('modal').classList.add('open');
}

function openSportsTrade(opp) {
  // Hergebruik de bestaande trade modal, vul sportdata in
  currentMarket = {
    question:    opp.question,
    conditionId: '',
    yes:         opp.poly_price,
    no:          1 - opp.poly_price,
  };
  document.getElementById('modal-question').textContent  = opp.question + ' (koop YES — ' + opp.outcome + ')';
  document.getElementById('modal-yes-price').textContent = (opp.poly_price*100).toFixed(0) + '¢';
  document.getElementById('modal-no-price').textContent  = ((1-opp.poly_price)*100).toFixed(0) + '¢';
  document.getElementById('kelly-slider').value = Math.round(opp.book_price * 100);
  selectSide('yes');
  updateKelly();
  document.getElementById('modal').classList.add('open');
}

function priceClass(p) {
  if (p >= 0.75) return 'high';
  if (p >= 0.45) return 'mid';
  if (p >= 0.05) return 'low';
  return 'dead';
}

function formatVol(v) {
  if (v >= 1_000_000) return '$' + (v/1_000_000).toFixed(1) + 'M';
  if (v >= 1_000)     return '$' + (v/1_000).toFixed(0) + 'k';
  return '$' + v.toFixed(0);
}

async function loadMarkets() {
  const list = document.getElementById('market-list');
  list.innerHTML = '<div class="loading"><div class="spinner"></div> Laden...</div>';

  try {
    const r   = await fetch('/api/markets?category=weather');
    markets   = await r.json();

    // Stats
    document.getElementById('stat-count').textContent = markets.length;
    if (markets.length > 0) {
      const topVol = Math.max(...markets.map(m => m.volume));
      const avgYes = markets.filter(m => m.yes > 0 && m.yes < 1)
                            .reduce((a, m, _, arr) => a + m.yes / arr.length, 0);
      document.getElementById('stat-topvol').textContent  = formatVol(topVol);
      document.getElementById('stat-avgyes').textContent  = (avgYes * 100).toFixed(0) + '%';
      document.getElementById('stat-time').textContent    = new Date().toLocaleTimeString('nl-NL');
    }

    const maxVol = Math.max(...markets.map(m => m.volume), 1);
    list.innerHTML = markets.map((m, i) => {
      const cls  = priceClass(m.yes);
      const dead = m.yes === 0 || m.yes === 1;
      const pct  = (m.yes * 100).toFixed(0);
      const noPct = (m.no * 100).toFixed(0);
      const delay = i * 30;
      const isHot = m.volume > maxVol * 0.25;

      return `
        <div class="market-row" style="animation-delay:${delay}ms" onclick="openModal(${i})">
          <div class="market-question" title="${m.question}">${m.question}</div>
          <div>
            <div class="price-yes ${cls}">${dead ? (m.yes===1?'✓':'✗') : pct+'%'}</div>
            ${!dead ? `<div class="prob-bar"><div class="prob-fill ${cls}" style="width:${pct}%"></div></div>` : ''}
          </div>
          <div class="price-no">${dead ? '' : noPct+'%'}</div>
          <div class="vol-cell"><span class="vol-num">${formatVol(m.volume)}</span></div>
          <div class="end-date">${m.endDate || '—'}</div>
          <div>
            ${!dead ? `<button class="trade-btn${isHot?' hot':''}" onclick="event.stopPropagation();openModal(${i})">TRADE</button>` : '<span style="color:var(--muted)">CLOSED</span>'}
          </div>
        </div>`;
    }).join('');

  } catch(e) {
    list.innerHTML = '<div class="loading" style="color:var(--red)">Fout bij laden: ' + e.message + '</div>';
  }
}

function openModal(idx) {
  currentMarket = markets[idx];
  if (!currentMarket || currentMarket.yes === 0 || currentMarket.yes === 1) return;

  document.getElementById('modal-question').textContent = currentMarket.question;
  document.getElementById('modal-yes-price').textContent = (currentMarket.yes*100).toFixed(0) + '¢';
  document.getElementById('modal-no-price').textContent  = (currentMarket.no*100).toFixed(0) + '¢';

  // Zet Kelly slider op marktprijs als startpunt
  document.getElementById('kelly-slider').value = Math.round(currentMarket.yes * 100);
  selectSide('yes');
  updateKelly();
  document.getElementById('modal').classList.add('open');
}

function updateKelly() {
  if (!currentMarket) return;

  const myPct      = parseInt(document.getElementById('kelly-slider').value);
  const bankroll   = parseFloat(document.getElementById('kelly-bankroll').value) || 500;
  const myProb     = myPct / 100;
  const marketPrice = selectedSide === 'yes' ? currentMarket.yes : currentMarket.no;

  document.getElementById('kelly-pct').textContent = myPct + '%';

  const b         = (1 - marketPrice) / marketPrice;
  const p         = myProb;
  const q         = 1 - p;
  const fullKelly = (b * p - q) / b;
  const fracKelly = fullKelly * 0.25;
  const edge      = ((p - marketPrice) * 100).toFixed(1);
  const bet       = Math.min(Math.max(fracKelly * bankroll, 0), bankroll * 0.10).toFixed(2);
  const ev        = ((p / marketPrice - 1) * 100).toFixed(1);

  const res = document.getElementById('kelly-result');

  if (fullKelly <= 0) {
    res.innerHTML = `<span class="kelly-bad">✗ NEGATIEVE EV — markt gelooft meer in YES dan jij. Skip deze trade.</span>`;
    return;
  }

  let edgeClass = 'kelly-bad';
  let advice    = 'Zwakke edge — overweeg te skippen';
  if (parseFloat(edge) >= 15) { edgeClass = 'kelly-good'; advice = 'Sterke edge'; }
  else if (parseFloat(edge) >= 8)  { edgeClass = 'kelly-good'; advice = 'Goede edge'; }
  else if (parseFloat(edge) >= 3)  { edgeClass = 'kelly-ok';   advice = 'Matige edge'; }

  const winProfit  = (parseFloat(bet) / marketPrice * (1 - marketPrice)).toFixed(2);
  const totalReturn = (parseFloat(bet) + parseFloat(winProfit)).toFixed(2);

  res.innerHTML = `
    <span class="${edgeClass}">${advice} (${edge}% verschil)</span><br>
    Aanbevolen inzet: <span class="kelly-value">$${bet}</span> (¼ Kelly van $${bankroll})<br>
    Bij winst: <span class="kelly-value">+$${winProfit}</span> winst → $${totalReturn} terug<br>
    Bij verlies: <span class="kelly-bad">-$${bet}</span>
  `;

  // Zet het aanbevolen bedrag automatisch in
  document.getElementById('amount-input').value = bet;
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
  currentMarket = null;
}

function selectSide(side) {
  selectedSide = side;
  document.getElementById('opt-yes').classList.toggle('selected', side === 'yes');
  document.getElementById('opt-no').classList.toggle('selected', side === 'no');
  updateKelly();
}

function setAmount(v) {
  document.getElementById('amount-input').value = v;
}

async function executeTrade() {
  if (!currentMarket) return;
  const amount = parseFloat(document.getElementById('amount-input').value);
  if (!amount || amount <= 0) return;

  const btn = document.querySelector('.btn-execute');
  btn.textContent = 'UITVOEREN...';
  btn.disabled = true;

  try {
    const r = await fetch('/api/trade', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        conditionId: currentMarket.conditionId,
        side: selectedSide,
        amount: amount,
      }),
    });
    const res = await r.json();
    if (res.ok) {
      btn.textContent = '✓ ORDER GEPLAATST';
      setTimeout(closeModal, 1500);
    } else {
      btn.textContent = '✗ FOUT: ' + (res.error || 'onbekend');
      setTimeout(() => { btn.textContent = 'EXECUTE ORDER'; btn.disabled = false; }, 2500);
    }
  } catch(e) {
    btn.textContent = '✗ FOUT';
    setTimeout(() => { btn.textContent = 'EXECUTE ORDER'; btn.disabled = false; }, 2000);
  }
}

// ── Weer Scanner ───────────────────────────────────────────
let weerMap     = null;
let weerMapInit = false;
let weerMarkers     = [];  // kansen-markers (worden gewist bij elke scan)
let weerHeatMarkers = [];  // temperatuur heatmap (blijft altijd staan)

const CITY_COORDS = {
  'amsterdam': [52.374, 4.890], 'austin': [30.267, -97.743],
  'chicago': [41.881, -87.628], 'dallas': [32.783, -96.800],
  'denver': [39.739, -104.984], 'helsinki': [60.169, 24.935],
  'hong kong': [22.320, 114.170], 'jakarta': [-6.211, 106.845],
  'miami': [25.774, -80.194], 'moscow': [55.751, 37.616],
  'new york': [40.713, -74.006], 'paris': [48.853, 2.350],
  'london': [51.509, -0.118], 'seoul': [37.566, 126.978],
  'singapore': [1.352, 103.820], 'sydney': [-33.868, 151.209],
  'tokyo': [35.689, 139.692], 'toronto': [43.653, -79.383],
  'sao paulo': [-23.550, -46.633], 'buenos aires': [-34.603, -58.382],
  'chengdu': [30.659, 104.065], 'berlin': [52.517, 13.388],
};

function tempColor(gap) {
  if (gap > 0.25) return '#ff6b35';
  if (gap > 0)    return '#ffa726';
  if (gap < -0.25) return '#388bfd';
  return '#42a5f5';
}

function tempClass(c) {
  if (c >= 35) return 'temp-hot';
  if (c >= 25) return 'temp-warm';
  if (c >= 15) return 'temp-mild';
  if (c >= 5)  return 'temp-cool';
  return 'temp-cold';
}

function tempToColor(c) {
  // Heatmap kleurschaal: blauw (koud) → groen → geel → oranje → rood (heet)
  if (c <= -10) return { h: 240, s: 80, l: 55 };
  if (c <= 0)   return { h: 210, s: 80, l: 60 };
  if (c <= 10)  return { h: 180, s: 70, l: 50 };
  if (c <= 15)  return { h: 140, s: 60, l: 45 };
  if (c <= 20)  return { h: 90,  s: 65, l: 42 };
  if (c <= 25)  return { h: 55,  s: 85, l: 50 };
  if (c <= 30)  return { h: 30,  s: 90, l: 52 };
  if (c <= 35)  return { h: 15,  s: 90, l: 50 };
  return { h: 0, s: 85, l: 48 };
}

function hsl(c) {
  const col = tempToColor(c);
  return `hsl(${col.h},${col.s}%,${col.l}%)`;
}

function initWeerMap() {
  weerMapInit = true;
  weerMap = L.map('weather-map', { zoomControl: false, attributionControl: false }).setView([48, 10], 3);
  // Lichte Positron tiles — veel beter leesbaar voor gekleurde markers
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 10
  }).addTo(weerMap);
  L.control.zoom({ position: 'bottomright' }).addTo(weerMap);
  setTimeout(loadHeatmap, 400);
}

async function loadHeatmap() {
  try {
    const r    = await fetch('/api/weather/heatmap');
    const data = await r.json();

    Object.entries(data).forEach(([city, info]) => {
      const c     = info.temp_c;
      const color = hsl(c);
      const rain  = info.rain_pct;

      // Grote gekleurde cirkel = heatmap bubble
      const bubble = L.circleMarker([info.lat, info.lon], {
        radius:      28,
        fillColor:   color,
        color:       'transparent',
        fillOpacity: 0.30,
        className:   'heat-bubble',
      }).addTo(weerMap);

      // Temperatuur label marker — licht design
      const label = L.divIcon({
        className: '',
        html: `<div style="
          background:#fff;
          border:2px solid ${color};
          border-radius:4px;
          padding:3px 6px;
          font-size:9px;
          font-weight:700;
          color:#1a1a2e;
          font-family:'IBM Plex Mono',monospace;
          white-space:nowrap;
          box-shadow:0 1px 6px rgba(0,0,0,0.15);
        "><span style="color:${color}">${c > 0 ? '+' : ''}${Math.round(c)}°C</span>${rain >= 40 ? ' 🌧' : ''}</div>`,
        iconSize:   [55, 20],
        iconAnchor: [27, 10],
      });

      const marker = L.marker([info.lat, info.lon], { icon: label }).addTo(weerMap);
      marker.bindPopup(`
        <b>${city.charAt(0).toUpperCase()+city.slice(1)}</b><br>
        Max: <b style="color:${color}">${c}°C</b><br>
        Regen: ${rain}%
      `);
      weerHeatMarkers.push(bubble);
      weerHeatMarkers.push(marker);
    });
  } catch(e) {
    console.error('Heatmap fout:', e);
  }
}

function updateWeerMap(opportunities) {
  if (!weerMap) return;

  // Alleen kansen-markers verwijderen, heatmap blijft staan
  weerMarkers.forEach(m => weerMap.removeLayer(m));
  weerMarkers = [];

  if (opportunities.length === 0) return;

  const bounds = [];

  opportunities.forEach(opp => {
    const city   = opp.city.toLowerCase();
    const coords = CITY_COORDS[city];
    if (!coords) return;

    bounds.push(coords);
    const color  = tempColor(opp.gap);
    const isHot  = opp.gap > 0;
    const gapAbs = Math.abs(opp.gap * 100).toFixed(0);

    // Op lichte kaart: witte achtergrond met gekleurde rand + donkere tekst
    const icon = L.divIcon({
      className: '',
      html: `<div style="
        position:relative;
        display:inline-block;
      ">
        <div style="
          background:#fff;
          border:2.5px solid ${color};
          border-radius:6px;
          padding:4px 7px;
          font-size:9px;font-weight:700;
          color:#1a1a2e;
          font-family:'IBM Plex Mono',monospace;
          white-space:nowrap;
          box-shadow:0 2px 8px rgba(0,0,0,0.18), 0 0 0 4px ${color}22;
          line-height:1.3;
          text-align:center;
        ">
          <div style="color:${color};font-size:10px;font-weight:800">${isHot?'+':''}${gapAbs}%</div>
          <div style="font-size:8px;color:#444;margin-top:1px">${opp.city}</div>
        </div>
      </div>`,
      iconSize: [70, 34],
      iconAnchor: [35, 17],
    });

    const popup = `
      <div style="line-height:1.7;font-family:'IBM Plex Mono',monospace;font-size:11px">
        <div style="font-size:12px;font-weight:700;color:#1a1a2e;margin-bottom:4px">${opp.city}</div>
        <div style="color:#888;font-size:9px;margin-bottom:8px">${opp.date} · ${opp.condition}</div>
        <div>Voorspeld: <b style="color:${color}">${opp.forecast_temp}°${opp.unit}</b></div>
        <div>Polymarket: <b>${(opp.poly_price*100).toFixed(0)}%</b> YES</div>
        <div>Model: <b>${(opp.model_prob*100).toFixed(0)}%</b> YES</div>
        <div style="margin-top:6px;color:${color};font-weight:700">${opp.direction} · ${isHot?'+':''}${(opp.gap*100).toFixed(0)}%</div>
      </div>`;

    const marker = L.marker(coords, { icon }).addTo(weerMap);
    marker.bindPopup(popup, { maxWidth: 200 });
    weerMarkers.push(marker);
  });

  if (bounds.length > 0) {
    weerMap.fitBounds(bounds, { padding: [40, 40], maxZoom: 5 });
  }

  // Update Windy embed naar centrum van kansen
  const centerLat = (bounds.reduce((s, c) => s + c[0], 0) / bounds.length).toFixed(1);
  const centerLon = (bounds.reduce((s, c) => s + c[1], 0) / bounds.length).toFixed(1);
  document.getElementById('windy-frame').src =
    `https://embed.windy.com/embed2.html?lat=${centerLat}&lon=${centerLon}&zoom=3&level=surface&overlay=temp&product=ecmwf&menu=&message=&marker=&calendar=now&pressure=&type=map&location=coordinates&detail=&metricWind=default&metricTemp=default&radarRange=-1`;
}

async function runWeerScan() {
  const btn  = document.getElementById('weer-scan-btn');
  const opps = document.getElementById('weer-opps');
  btn.disabled = true;
  btn.textContent = '⟳ SCANNEN...';
  opps.innerHTML = '<div class="loading" style="grid-column:1/-1"><div class="spinner"></div> Weermodellen vergelijken...</div>';

  try {
    // Haal scan + portfolio tegelijk op
    const [weatherR, portfolioR] = await Promise.all([
      fetch('/api/weather'),
      fetch('/api/portfolio'),
    ]);
    const data      = await weatherR.json();
    const portfolio = await portfolioR.json();

    if (data.error) {
      opps.innerHTML = `<div class="gap-empty" style="color:#f85149;grid-column:1/-1">Fout: ${data.error}</div>`;
      btn.disabled = false; btn.textContent = '⟳ SCAN NU'; return;
    }

    // Bouw set van open positie-vragen voor snelle lookup
    const openQuestions = new Set(
      (portfolio.positions || [])
        .filter(p => p.status === 'open')
        .map(p => p.question)
    );

    document.getElementById('weer-opp-count').textContent = data.length;
    const now = new Date();
    document.getElementById('weer-scan-time').textContent =
      'Scan ' + now.toLocaleTimeString('nl-NL') + ' · ' + data.length + ' kansen gevonden';

    if (data.length === 0) {
      opps.innerHTML = `<div class="gap-empty" style="color:#4a6080;grid-column:1/-1">
        Geen kansen — weermodel stemt overeen met Polymarket
      </div>`;
    } else {
      opps.innerHTML = data.map((o, i) => {
        const isBuyYes  = o.direction === 'BUY YES';
        const color     = isBuyYes ? 'var(--ice)' : 'var(--blue)';
        const cls       = isBuyYes ? 'buy-yes' : 'buy-no';
        const actionCls = isBuyYes ? 'action-buy-yes' : 'action-buy-no';
        const tc        = tempClass(o.forecast_temp_c || 0);
        const sign      = o.gap > 0 ? '+' : '';
        const delay     = i * 50;
        const inPortfolio = openQuestions.has(o.question);
        const gapAbs    = Math.abs(o.gap * 100);
        const strengthCls = gapAbs >= 40 ? 'gap-strong' : 'gap-good';

        const portfolioBadge = inPortfolio
          ? `<span style="font-size:9px;padding:2px 7px;border-radius:2px;background:rgba(0,223,122,0.12);color:var(--go);border:1px solid rgba(0,223,122,0.3);letter-spacing:0.1em;font-weight:700">✓ IN PORTFOLIO</span>`
          : `<button class="trade-btn" style="font-size:10px;padding:3px 10px" onclick="event.stopPropagation();openWeerTrade(${JSON.stringify(o).replace(/"/g,'&quot;')})">TRADE</button>`;

        return `
          <div class="weer-opp-card ${cls}" style="animation-delay:${delay}ms" onclick="openWeerTrade(${JSON.stringify(o).replace(/"/g,'&quot;')})">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">
              <div class="weer-opp-city">${o.city}</div>
              <span class="gap-badge ${strengthCls}">${sign}${gapAbs.toFixed(0)}%</span>
            </div>
            <div class="weer-opp-date">${o.date} · ${o.condition}</div>
            <div class="weer-opp-temps">
              <div class="temp-box">
                <div class="temp-box-label">MODEL</div>
                <div class="temp-box-val ${tc}">${o.forecast_temp}°${o.unit}</div>
              </div>
              <div class="temp-box">
                <div class="temp-box-label">POLY YES</div>
                <div class="temp-box-val" style="color:var(--text)">${(o.poly_price*100).toFixed(0)}%</div>
              </div>
            </div>
            <div class="weer-gap-row">
              <span class="weer-action ${actionCls}">${o.direction}</span>
              ${portfolioBadge}
            </div>
          </div>`;
      }).join('');
    }

    // Kaart bijwerken met kansen
    if (!weerMapInit) initWeerMap();
    setTimeout(() => updateWeerMap(data), weerMapInit ? 0 : 600);

  } catch(e) {
    opps.innerHTML = `<div class="gap-empty" style="color:#f85149;grid-column:1/-1">Fout: ${e.message}</div>`;
  }
  btn.disabled = false;
  btn.textContent = '⟳ SCAN NU';
}

function openWeerTrade(opp) {
  currentMarket = {
    question:    opp.question,
    conditionId: '',
    yes:         opp.poly_price,
    no:          1 - opp.poly_price,
  };
  document.getElementById('modal-question').textContent =
    opp.question + ` (model: ${opp.forecast_temp}°${opp.unit})`;
  document.getElementById('modal-yes-price').textContent = (opp.poly_price*100).toFixed(0) + '¢';
  document.getElementById('modal-no-price').textContent  = ((1-opp.poly_price)*100).toFixed(0) + '¢';
  document.getElementById('kelly-slider').value = Math.round(opp.model_prob * 100);
  selectSide(opp.direction === 'BUY YES' ? 'yes' : 'no');
  updateKelly();
  document.getElementById('modal').classList.add('open');
}

// Sluit modal bij klik buiten
document.getElementById('modal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

// Auto-refresh markten elke 30 seconden
setInterval(loadMarkets, 30000);

// Auto-refresh weer scanner elke 10 minuten (600.000ms)
setInterval(() => {
  if (currentTab === 'weer') runWeerScan();
}, 600000);

// ── Hurricane Scanner ────────────────────────────────────────────────────────

async function runHurricaneScan() {
  const btn  = document.getElementById('hurricane-scan-btn');
  const list = document.getElementById('hurricane-list');
  const sub  = document.getElementById('hurricane-sub');
  btn.disabled = true;
  btn.textContent = '⟳ LADEN...';
  list.innerHTML  = '<div class="loading"><div class="spinner"></div> NHC stormdata + Polymarket ophalen...</div>';

  try {
    const r = await fetch('/api/hurricane');
    const d = await r.json();

    if (d.error) {
      list.innerHTML = `<div class="no-opps">Fout: ${d.error}</div>`;
      return;
    }

    // Season bar
    const bar = document.getElementById('hurricane-season-bar');
    if (d.active_storms !== undefined) {
      bar.style.display = 'block';
      const seasonPct = d.season_month_prob != null ? (d.season_month_prob * 100).toFixed(0) : '?';
      bar.innerHTML = `
        <span style="color:var(--ice)">SEIZOEN STATUS</span>&nbsp;&nbsp;
        Actieve stormen: <strong style="color:${d.active_storms > 0 ? 'var(--danger)' : 'var(--text2)'}">${d.active_storms}</strong>
        &nbsp;|&nbsp; Maandkans landfall VS: <strong>${seasonPct}%</strong>
        &nbsp;|&nbsp; NOAA 2026 outlook: <strong style="color:var(--warn)">${d.outlook}</strong>
        &nbsp;|&nbsp; Seizoen: <strong>juni – november</strong>
      `;
    }

    sub.textContent = `${d.opportunities ? d.opportunities.length : 0} kans(en) gevonden · ${new Date().toLocaleTimeString('nl-NL')}`;

    const opps = d.opportunities || [];
    if (!opps.length) {
      list.innerHTML = `
        <div class="no-opps">
          Geen kansen gevonden<br>
          <span style="font-size:12px;color:var(--text2)">
            ${d.active_storms === 0
              ? 'Geen actieve stormen — seizoen start juni. Scanner klaar voor gebruik.'
              : 'Alle markten correct geprijsd (gap < 10%)'}
          </span>
        </div>`;
      return;
    }

    const labelColor = l => l === 'STERK' ? 'var(--danger)' : l === 'GOED' ? 'var(--warn)' : 'var(--text2)';
    list.innerHTML = opps.map(o => {
      const sign = o.gap > 0 ? '+' : '';
      const gapColor = Math.abs(o.gap) >= 0.25 ? 'var(--danger)' : Math.abs(o.gap) >= 0.12 ? 'var(--warn)' : 'var(--go)';
      return `
        <div class="opp-card">
          <div class="opp-header">
            <span class="opp-label" style="color:${labelColor(o.label)}">[${o.label}]</span>
            <span class="opp-q">${o.question}</span>
          </div>
          <div class="opp-meta">
            <span>TYPE: ${o.market_type.toUpperCase()}</span>
            <span>STORM: ${o.storm_info || '—'}</span>
            <span>LIQ: $${(o.liquidity||0).toLocaleString('en', {maximumFractionDigits:0})}</span>
          </div>
          <div class="opp-basis" style="font-size:11px;color:var(--text2);margin:4px 0 8px;font-style:italic">${o.basis}</div>
          <div class="opp-prices">
            <div class="opp-price-item">
              <div class="opp-price-label">POLYMARKET</div>
              <div class="opp-price-val">${(o.poly_price*100).toFixed(1)}%</div>
            </div>
            <div class="opp-price-item">
              <div class="opp-price-label">MODEL</div>
              <div class="opp-price-val" style="color:var(--ice)">${(o.model_prob*100).toFixed(1)}%</div>
            </div>
            <div class="opp-price-item">
              <div class="opp-price-label">GAP</div>
              <div class="opp-price-val" style="color:${gapColor}">${sign}${(o.gap*100).toFixed(1)}%</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-top:8px;">
            <span class="opp-direction">${o.direction}</span>
            <button class="trade-btn" onclick="openHurricaneTrade(${JSON.stringify(o).replace(/"/g,'&quot;')})">TRADE</button>
          </div>
        </div>`;
    }).join('');

  } catch (e) {
    list.innerHTML = `<div class="no-opps">Fout: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ SCAN NU';
  }
}

// ── Whale Tracker ────────────────────────────────────────────────────────────

async function runWhalesScan() {
  const btn  = document.getElementById('whales-scan-btn');
  const list = document.getElementById('whales-list');
  const sub  = document.getElementById('whales-sub');
  btn.disabled = true;
  btn.textContent = '⟳ LADEN...';
  list.innerHTML = '<div class="loading"><div class="spinner"></div> Whale posities ophalen...</div>';

  try {
    const r = await fetch('/api/whales');
    const d = await r.json();
    if (d.error) { list.innerHTML = `<div class="no-opps">Fout: ${d.error}</div>`; return; }

    const whales = d.whales || [];
    sub.textContent = `${whales.length} whale(s) getracked · ${new Date().toLocaleTimeString('nl-NL')}`;

    if (!whales.length) {
      list.innerHTML = '<div class="no-opps">Geen whales geconfigureerd</div>';
      return;
    }

    list.innerHTML = whales.map(whale => {
      const positions = whale.positions || [];
      const trades    = whale.trades    || [];

      const posHTML = positions.slice(0,15).map(p => {
        const pnlColor = p.cash_pnl >= 0 ? 'var(--go)' : 'var(--danger)';
        const pnlSign  = p.cash_pnl >= 0 ? '+' : '';
        return `<div class="whale-row">
          <span class="whale-outcome ${p.outcome.toLowerCase()}">${p.outcome}</span>
          <span class="whale-val">$${p.current_value.toFixed(0)}</span>
          <span class="whale-price">${(p.cur_price*100).toFixed(0)}%</span>
          <span style="color:${pnlColor};font-size:11px">${pnlSign}$${p.cash_pnl.toFixed(0)}</span>
          <span class="whale-title">${p.title}</span>
          <button class="whale-copy-btn" onclick="copyWhaleTrade(${JSON.stringify(p).replace(/"/g,'&quot;')})">COPY</button>
        </div>`;
      }).join('');

      const tradeHTML = trades.slice(0,10).map(t => {
        const arrow = t.side === 'BUY' ? '↑' : '↓';
        const col   = t.side === 'BUY' ? 'var(--go)' : 'var(--danger)';
        return `<div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--ice3);font-size:12px;">
          <span style="color:${col};width:12px">${arrow}</span>
          <span style="color:var(--text2);width:115px;flex-shrink:0">${t.timestamp}</span>
          <span style="color:var(--ice);width:30px">${t.side}</span>
          <span style="width:50px">$${t.usdc_size.toFixed(0)}</span>
          <span style="color:var(--text2);width:40px">${(t.price*100).toFixed(0)}%</span>
          <span style="color:var(--text);overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${t.title}</span>
        </div>`;
      }).join('');

      return `<div style="background:var(--panel);border:1px solid var(--ice3);border-radius:8px;padding:16px;margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <div>
            <span style="color:var(--ice);font-weight:700;font-size:15px">${whale.name}</span>
            <span style="color:var(--text2);font-size:11px;margin-left:8px">${whale.address.slice(0,10)}...${whale.address.slice(-4)}</span>
          </div>
          <div style="text-align:right;font-size:12px">
            <div style="color:var(--go)">Open: $${(whale.total_open_value||0).toLocaleString('en',{maximumFractionDigits:0})}</div>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div>
            <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Open Posities (${positions.length})</div>
            <div class="whale-positions-list">${posHTML || '<span style="color:var(--text2);font-size:12px">Geen open posities</span>'}</div>
          </div>
          <div>
            <div style="color:var(--text2);font-size:11px;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em">Recente Trades</div>
            ${tradeHTML || '<span style="color:var(--text2);font-size:12px">Geen recente trades</span>'}
          </div>
        </div>
      </div>`;
    }).join('');

  } catch(e) {
    list.innerHTML = `<div class="no-opps">Fout: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ REFRESH';
  }
}

async function addWhale() {
  const username = document.getElementById('whale-username')?.value?.trim();
  if (!username) return;
  const r = await fetch('/api/whales/add', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({username})
  });
  const d = await r.json();
  if (d.ok) {
    document.getElementById('whale-username').value = '';
    runWhalesScan();
  } else {
    alert(d.error || 'Niet gevonden');
  }
}

// ── WALLETS TAB ───────────────────────────────────────────────────────────────

let _wfAllTrades = [];
let _wfActiveFilter = 'all';

async function refreshWhalePf() {
  document.getElementById('wp-sub').textContent = 'Laden...';
  try {
    const r = await fetch('/api/whale-portfolio/stats');
    const d = await r.json();
    if (d.error) return;

    const pnlColor = v => v > 0 ? 'var(--go)' : v < 0 ? 'var(--danger)' : 'var(--text)';
    const pnlSign  = v => v > 0 ? '+' : '';

    const eq = document.getElementById('wp-equity');
    eq.textContent = `$${d.total_equity}`;
    eq.style.color  = pnlColor(d.total_pnl);
    document.getElementById('wp-cash').textContent = `$${d.cash}`;

    const rp = document.getElementById('wp-rpnl');
    rp.textContent = `${pnlSign(d.realized_pnl)}$${d.realized_pnl}`;
    rp.style.color  = pnlColor(d.realized_pnl);

    const up = document.getElementById('wp-upnl');
    up.textContent = `${pnlSign(d.unrealized_pnl)}$${d.unrealized_pnl}`;
    up.style.color  = pnlColor(d.unrealized_pnl);

    const wr = document.getElementById('wp-wr');
    wr.textContent = d.closed_positions ? `${d.win_rate}% (${d.wins}W/${d.losses}L)` : '—';
    wr.style.color  = pnlColor(d.win_rate - 50);

    const wpRisk = (id, v, fmt, good, warn) => {
      const el = document.getElementById(id);
      if (v === null || v === undefined) { el.textContent = '—'; el.style.color = 'var(--text2)'; return; }
      el.textContent = fmt(v);
      el.style.color = v >= good ? 'var(--go)' : v >= warn ? 'var(--ice)' : v < 0 ? 'var(--danger)' : 'var(--text)';
    };
    wpRisk('wp-sharpe',  d.sharpe,        v => v >= 99 ? '99+ ∞' : v.toFixed(2), 2.0, 1.0);
    wpRisk('wp-sortino', d.sortino,       v => v >= 99 ? '99+ ∞' : v.toFixed(2), 3.0, 1.5);
    wpRisk('wp-calmar',  d.calmar,        v => v >= 99 ? '99+ ∞' : v.toFixed(2), 3.0, 1.0);
    wpRisk('wp-pf',      d.profit_factor, v => v >= 99 ? '∞' : v.toFixed(2),     2.0, 1.5);
    wpRisk('wp-kelly',   d.kelly_pct,     v => `${v.toFixed(1)}%`,               50,  20);

    const dd = document.getElementById('wp-maxdd');
    if (d.max_drawdown !== null && d.max_drawdown !== undefined) {
      dd.textContent = `${d.max_drawdown}%`;
      dd.style.color = d.max_drawdown < -30 ? 'var(--danger)' : d.max_drawdown < -15 ? 'var(--ice)' : 'var(--go)';
    } else { dd.textContent = '—'; dd.style.color = 'var(--text2)'; }

    const wv = document.getElementById('wp-var95');
    if (d.var_95 !== null && d.var_95 !== undefined) {
      wv.textContent = `${d.var_95}%`;
      wv.style.color = d.var_95 < -80 ? 'var(--danger)' : d.var_95 < -50 ? 'var(--amber)' : 'var(--go)';
    } else { wv.textContent = '—'; wv.style.color = 'var(--text2)'; }

    document.getElementById('wp-sub').textContent =
      `$${d.starting_balance} start · ${d.trade_count} trades · ${pnlSign(d.total_pnl_pct)}${d.total_pnl_pct}% totaal`;

    // Posities tabel (identiek aan weather portfolio)
    const positions = d.positions || [];
    const posEl = document.getElementById('wp-positions');
    if (!positions.length) {
      posEl.innerHTML = '<div style="color:var(--text2);padding:20px;text-align:center">Geen posities — auto-follow kopieert bij nieuwe whale trades</div>';
    } else {
      const openPos   = positions.filter(p => p.status === 'open');
      const closedPos = positions.filter(p => p.status !== 'open');
      const rowGrid  = `display:grid;grid-template-columns:24px 40px 24px 50px 1fr 55px 50px 50px 75px 60px;gap:4px;padding:6px 4px;border-bottom:1px solid rgba(0,200,255,0.05);font-size:12px;align-items:center`;
      const headGrid = `display:grid;grid-template-columns:24px 40px 24px 50px 1fr 55px 50px 50px 75px 60px;gap:4px;padding:6px 4px;border-bottom:1px solid var(--ice3);font-size:10px;color:var(--text2);text-transform:uppercase`;
      const statusIcon = s => s==='open'?'◉':s==='won'?'✓':s==='sold'?'↩':'✗';
      const statusCol  = s => s==='open'?'var(--ice)':s==='won'||s==='sold'?'var(--go)':'var(--danger)';
      const renderWpRow = pos => {
        const pnl = pos.status==='open' ? (pos.unrealized_pnl||0) : (pos.pnl||0);
        const col = pnlColor(pnl);
        const noteLabel = (pos.note||'').replace('[AUTO:','').replace(']','');
        const tag = `<span style="font-size:9px;background:rgba(255,200,0,0.2);color:#ffc800;border:1px solid #ffc800;border-radius:3px;padding:1px 4px">🐋</span>`;
        const sellBtn = pos.status==='open'
          ? `<button onclick="sellWhalePfPos('${pos.id}')" style="font-size:10px;padding:2px 6px;background:rgba(255,80,80,0.15);border:1px solid var(--danger);color:var(--danger);border-radius:4px;cursor:pointer">SELL</button>`
          : `<span style="font-size:11px;color:${statusCol(pos.status)}">${pos.status.toUpperCase()}</span>`;
        return `<div style="${rowGrid}">
          <div style="color:${statusCol(pos.status)}">${statusIcon(pos.status)}</div>
          <div style="color:var(--text2);font-size:11px">${pos.id}</div>
          <div>${tag}</div>
          <div style="color:${pos.direction==='YES'?'var(--go)':'var(--danger)'};font-weight:700">${pos.direction}</div>
          <div style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--text)">${pos.question}</div>
          <div>$${pos.amount}</div>
          <div style="color:var(--text2)">${(pos.entry_price*100).toFixed(0)}%</div>
          <div style="color:var(--text2)">${pos.status==='open'?(pos.current_price*100).toFixed(0)+'%':(pos.exit_price*100).toFixed(0)+'%'}</div>
          <div style="color:${col};font-weight:700">${pnlSign(pnl)}$${Math.abs(pnl).toFixed(2)}</div>
          <div>${sellBtn}</div>
        </div>`;
      };
      const totalCPnl = closedPos.reduce((s,p)=>s+(p.pnl||0),0);
      const closedSec = closedPos.length ? `
        <div style="margin-top:20px;margin-bottom:6px;display:flex;align-items:center;gap:12px">
          <div style="font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase">Closed Trades (${closedPos.length})</div>
          <div style="font-size:11px;color:${pnlColor(totalCPnl)};font-weight:700">${pnlSign(totalCPnl)}$${totalCPnl.toFixed(2)} gerealiseerd</div>
        </div>
        <div style="${headGrid}"><div></div><div>ID</div><div></div><div>Dir</div><div>Markt</div><div>Inzet</div><div>Entry</div><div>Exit</div><div>P&L</div><div>Status</div></div>
        ${closedPos.map(renderWpRow).join('')}` : '';
      posEl.innerHTML = `
        <div style="margin-bottom:6px"><span style="font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase">Open Posities (${openPos.length})</span></div>
        <div style="${headGrid}"><div></div><div>ID</div><div></div><div>Dir</div><div>Markt</div><div>Inzet</div><div>Entry</div><div>Nu</div><div>P&L</div><div>Actie</div></div>
        ${openPos.map(renderWpRow).join('')}
        ${closedSec}`;
    }
  } catch(e) {
    document.getElementById('wp-sub').textContent = `Fout: ${e.message}`;
  }
  refreshWpEquityCurve();
}

async function refreshWpEquityCurve() {
  try {
    const r = await fetch('/api/whale-portfolio/history');
    const d = await r.json();
    const points = d.points || [];
    const svg   = document.getElementById('wp-curve-svg');
    const empty = document.getElementById('wp-curve-empty');
    if (points.length < 2) { svg.innerHTML = ''; empty.style.display = 'flex'; return; }
    empty.style.display = 'none';
    const W = svg.parentElement.clientWidth - 24;
    const H = svg.parentElement.clientHeight - 24;
    const equities = points.map(p => p.equity);
    const minE = Math.min(...equities), maxE = Math.max(...equities);
    const rangeE = maxE - minE || 1;
    const xs = points.map((_, i) => (i / Math.max(points.length-1,1)) * W);
    const ys = equities.map(e => H - ((e-minE)/rangeE)*H*0.9 - H*0.05);
    const lastColor = equities[equities.length-1] >= equities[0] ? '#00E87A' : '#FF3355';
    svg.innerHTML = `
      <polyline points="${xs.map((x,i)=>`${x},${ys[i]}`).join(' ')}" fill="none" stroke="${lastColor}" stroke-width="1.5" stroke-linejoin="round"/>
      <circle cx="${xs[xs.length-1]}" cy="${ys[ys.length-1]}" r="3" fill="${lastColor}"/>
      <text x="0" y="10" font-size="9" fill="var(--text2)">$${minE.toFixed(0)}</text>
      <text x="${W-40}" y="10" font-size="9" fill="${lastColor}">$${equities[equities.length-1].toFixed(2)}</text>`;
  } catch(e) {}
}

async function sellWhalePfPos(posId) {
  if (!confirm(`Positie ${posId} verkopen tegen huidige prijs?`)) return;
  const r = await fetch(`/api/whale-portfolio/sell/${posId}`, {method:'POST'});
  const d = await r.json();
  if (d.ok) refreshWhalePf();
  else alert(d.error);
}

async function loadWhalePortfolioStats() {
  return refreshWhalePf();
}

async function loadFollowStatus() {
  try {
    const r = await fetch('/api/whale-portfolio/follow-status');
    const s = await r.json();

    document.getElementById('af-config').textContent =
      `· ${s.copy_pct}% cash per trade · min $${s.min_size} whale-inzet · check elke ${s.interval_min}min`;

    const lastParts = Object.entries(s.last_checks || {}).map(([n,t]) => `${n}: ${t}`);
    document.getElementById('af-last').textContent =
      lastParts.length ? 'Laatste check — ' + lastParts.join(' · ') : 'Nog geen check';

    const logWrap = document.getElementById('af-log-wrap');
    const logEl   = document.getElementById('af-log');
    if (s.recent_copies && s.recent_copies.length) {
      logWrap.style.display = 'block';
      logEl.innerHTML = s.recent_copies.map(c => {
        const col = c.outcome === 'YES' ? 'var(--ice)' : 'var(--blue)';
        return `<div style="display:grid;grid-template-columns:100px 70px 50px 60px 1fr;gap:8px;padding:4px 0;border-bottom:1px solid var(--border);font-size:10px;font-family:'IBM Plex Mono',monospace">
          <span style="color:var(--text2)">${c.ts}</span>
          <span style="color:var(--go);font-weight:700">${c.whale}</span>
          <span style="color:${col}">${c.outcome}</span>
          <span>$${c.amount.toFixed(0)}</span>
          <span style="color:var(--text);overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${c.title}</span>
        </div>`;
      }).join('');
    } else {
      logWrap.style.display = 'none';
    }
  } catch(e) {}
}

async function loadWallets() {
  const grid = document.getElementById('wallets-grid');
  grid.innerHTML = '<div style="color:var(--text2);padding:40px;text-align:center;grid-column:1/-1"><div class="spinner" style="margin:0 auto 12px"></div>Whales laden...</div>';

  const r = await fetch('/api/whale-portfolio/list');
  const whales = await r.json();

  if (!whales.length) {
    grid.innerHTML = '<div style="color:var(--text2);padding:40px;text-align:center;grid-column:1/-1">Geen whales toegevoegd — voeg er één toe hierboven</div>';
    return;
  }

  // Laad stats per whale parallel
  const analyses = await Promise.all(whales.map(async w => {
    try {
      const r2 = await fetch(`/api/whale-portfolio/analyze/${w.address}`);
      const stats = await r2.json();
      return {...w, stats};
    } catch { return {...w, stats: null}; }
  }));

  grid.innerHTML = analyses.map(w => {
    const s = w.stats;
    const winColor = s && s.win_rate >= 60 ? 'var(--go)' : s && s.win_rate >= 45 ? 'var(--ice)' : 'var(--danger)';
    const pnlColor = s && s.realized_pnl >= 0 ? 'var(--go)' : 'var(--danger)';
    const pnlSign  = s && s.realized_pnl >= 0 ? '+' : '';
    const dom = s?.dominant_market || '—';
    const domIcon = {crypto:'₿', weer:'🌡', politiek:'🗳', sport:'🏆', overig:'◎'}[dom] || '◎';

    const pfLabel = w.portfolio === 'weather'
      ? `<span style="font-size:9px;padding:1px 6px;border-radius:3px;background:rgba(255,180,0,0.15);color:#ffb400;border:1px solid #ffb400;letter-spacing:0.1em">🌡 WEATHER</span>`
      : `<span style="font-size:9px;padding:1px 6px;border-radius:3px;background:rgba(0,200,255,0.12);color:var(--ice);border:1px solid var(--ice);letter-spacing:0.1em">₿ CRYPTO</span>`;
    return `<div class="whale-card" onclick="expandWhale('${w.address}')">
      <div class="whale-card-header">
        <div>
          <div class="whale-card-name" style="display:flex;align-items:center;gap:8px">${w.name} ${pfLabel}</div>
          <div class="whale-card-addr">${w.address.slice(0,10)}...${w.address.slice(-6)}</div>
          ${w.note ? `<div class="whale-card-note">${w.note}</div>` : ''}
        </div>
        <button class="whale-remove-btn" onclick="event.stopPropagation();removeWalletWhale('${w.address}','${w.name}')">✕ REMOVE</button>
      </div>

      ${s ? `
      <div class="whale-stats">
        <div class="whale-stat">
          <div class="whale-stat-label">Win rate</div>
          <div class="whale-stat-val" style="color:${winColor}">${s.win_rate}%</div>
        </div>
        <div class="whale-stat">
          <div class="whale-stat-label">PnL (realized)</div>
          <div class="whale-stat-val" style="color:${pnlColor}">${pnlSign}$${Math.abs(s.realized_pnl).toLocaleString('en',{maximumFractionDigits:0})}</div>
        </div>
        <div class="whale-stat">
          <div class="whale-stat-label">Specialiteit</div>
          <div class="whale-stat-val" style="font-size:11px">${domIcon} ${dom}</div>
        </div>
      </div>
      <div style="margin-top:10px;display:flex;gap:12px;font-size:10px;color:var(--text2)">
        <span>Vol: <b style="color:var(--text)">$${(s.total_volume/1000).toFixed(0)}K</b></span>
        <span>Open: <b style="color:var(--text)">$${(s.open_value/1000).toFixed(0)}K</b></span>
        <span>Trades: <b style="color:var(--text)">${s.trade_count}</b></span>
        <span>Gem: <b style="color:var(--text)">$${s.avg_trade_size.toFixed(0)}</b></span>
      </div>` : `<div style="color:var(--text2);font-size:11px;margin-top:8px">Stats laden mislukt</div>`}
    </div>`;
  }).join('');
}

async function addWalletWhale() {
  const address   = document.getElementById('wp-address').value.trim();
  const name      = document.getElementById('wp-name').value.trim();
  const note      = document.getElementById('wp-note').value.trim();
  const portfolio = document.getElementById('wp-portfolio').value;
  const msg       = document.getElementById('wp-add-msg');

  if (!address || !name) { msg.textContent = 'Adres en naam zijn verplicht'; msg.style.color='var(--danger)'; return; }

  msg.textContent = 'Toevoegen...'; msg.style.color = 'var(--text2)';
  const r = await fetch('/api/whale-portfolio/add', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({address, name, note, portfolio})
  });
  const d = await r.json();
  if (d.ok) {
    msg.textContent = `✓ ${d.name} toegevoegd`; msg.style.color = 'var(--go)';
    document.getElementById('wp-address').value = '';
    document.getElementById('wp-name').value    = '';
    document.getElementById('wp-note').value    = '';
    setTimeout(loadWallets, 400);
  } else {
    msg.textContent = d.error; msg.style.color = 'var(--danger)';
  }
}

async function removeWalletWhale(address, name) {
  if (!confirm(`${name} verwijderen uit whale lijst?`)) return;
  await fetch('/api/whale-portfolio/remove', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({address})
  });
  loadWallets();
}

async function expandWhale(address) {
  // Open analyse in een overlay
  const r = await fetch(`/api/whale-portfolio/analyze/${address}`);
  const s = await r.json();
  if (s.error) return;

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(2,11,18,0.92);z-index:9999;overflow-y:auto;padding:40px 20px';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

  const posRows = (s.top_positions||[]).map(p => {
    const pnlColor = p.pnl >= 0 ? 'var(--go)' : 'var(--danger)';
    const pnlSign  = p.pnl >= 0 ? '+' : '';
    return `<div style="display:grid;grid-template-columns:36px 70px 50px 1fr;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:11px;font-family:'IBM Plex Mono',monospace">
      <span style="color:${p.outcome==='Yes'?'var(--ice)':'var(--blue)'};">${p.outcome}</span>
      <span>$${p.value.toLocaleString('en',{maximumFractionDigits:0})}</span>
      <span style="color:${pnlColor}">${pnlSign}$${Math.abs(p.pnl).toFixed(0)}</span>
      <span style="color:var(--text2);overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${p.title}</span>
    </div>`;
  }).join('');

  const tradeRows = (s.recent_trades||[]).map(t => {
    const col = t.side==='BUY' ? 'var(--go)' : 'var(--danger)';
    const out = t.outcome==='YES' ? 'var(--ice)' : 'var(--blue)';
    return `<div style="display:grid;grid-template-columns:36px 36px 60px 50px 1fr;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:11px;font-family:'IBM Plex Mono',monospace">
      <span style="color:${col}">${t.side}</span>
      <span style="color:${out}">${t.outcome}</span>
      <span>$${t.size.toFixed(0)}</span>
      <span style="color:var(--text2)">${(t.price*100).toFixed(0)}%</span>
      <span style="color:var(--text);overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${t.title}</span>
    </div>`;
  }).join('');

  overlay.innerHTML = `<div style="max-width:800px;margin:0 auto;background:var(--panel);border:1px solid var(--blue);border-radius:8px;padding:24px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
      <div>
        <div style="font-size:9px;color:var(--blue);letter-spacing:0.2em;margin-bottom:4px">WHALE ANALYSE</div>
        <div style="font-size:16px;font-weight:800">${address.slice(0,10)}...${address.slice(-6)}</div>
      </div>
      <button onclick="this.closest('[style*=fixed]').remove()" style="background:transparent;border:1px solid var(--border2);color:var(--text2);padding:6px 14px;border-radius:4px;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:11px">✕ SLUITEN</button>
    </div>

    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px">
      ${[
        ['Win rate', s.win_rate+'%', s.win_rate>=60?'var(--go)':s.win_rate>=45?'var(--ice)':'var(--danger)'],
        ['Realized PnL', (s.realized_pnl>=0?'+':'')+'$'+Math.abs(s.realized_pnl).toLocaleString('en',{maximumFractionDigits:0}), s.realized_pnl>=0?'var(--go)':'var(--danger)'],
        ['Open waarde', '$'+(s.open_value/1000).toFixed(0)+'K', 'var(--text)'],
        ['Specialiteit', s.dominant_market, 'var(--blue)'],
      ].map(([l,v,c])=>`<div style="background:var(--deep);border:1px solid var(--border2);border-radius:4px;padding:10px;text-align:center">
        <div style="font-size:8px;color:var(--text2);letter-spacing:0.12em;margin-bottom:4px">${l}</div>
        <div style="font-size:14px;font-weight:700;color:${c}">${v}</div>
      </div>`).join('')}
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div>
        <div style="font-size:9px;color:var(--text2);letter-spacing:0.15em;margin-bottom:8px">OPEN POSITIES (TOP 10)</div>
        ${posRows || '<div style="color:var(--text2);font-size:11px">Geen open posities</div>'}
      </div>
      <div>
        <div style="font-size:9px;color:var(--text2);letter-spacing:0.15em;margin-bottom:8px">RECENTE TRADES</div>
        ${tradeRows || '<div style="color:var(--text2);font-size:11px">Geen trades</div>'}
      </div>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

// ── WHALE FEED TAB ────────────────────────────────────────────────────────────

async function loadWhaleFeed() {
  const list    = document.getElementById('wf-list');
  const counter = document.getElementById('wf-count');
  list.innerHTML = '<div style="color:var(--text2);padding:40px;text-align:center"><div class="spinner" style="margin:0 auto 12px"></div>Feed laden...</div>';

  const r = await fetch('/api/whale-portfolio/feed');
  _wfAllTrades = await r.json();

  if (_wfAllTrades.error) {
    list.innerHTML = `<div style="color:var(--danger);padding:20px">${_wfAllTrades.error}</div>`;
    return;
  }

  counter.textContent = `${_wfAllTrades.length} trades · ${new Date().toLocaleTimeString('nl-NL')}`;

  // Bouw filter knoppen
  const whaleNames = [...new Set(_wfAllTrades.map(t => t.whale_name))];
  const filterBar  = document.getElementById('wf-filters');
  filterBar.innerHTML = `<button class="wf-filter ${_wfActiveFilter==='all'?'active':''}" data-filter="all" onclick="setWfFilter('all',this)">ALLE</button>`
    + whaleNames.map(n => `<button class="wf-filter ${_wfActiveFilter===n?'active':''}" data-filter="${n}" onclick="setWfFilter('${n}',this)">${n}</button>`).join('');

  renderWhaleFeed();
}

function setWfFilter(filter, el) {
  _wfActiveFilter = filter;
  document.querySelectorAll('.wf-filter').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  renderWhaleFeed();
}

function renderWhaleFeed() {
  const list   = document.getElementById('wf-list');
  const trades = _wfActiveFilter === 'all'
    ? _wfAllTrades
    : _wfAllTrades.filter(t => t.whale_name === _wfActiveFilter);

  if (!trades.length) {
    list.innerHTML = '<div style="color:var(--text2);padding:40px;text-align:center">Geen trades gevonden</div>';
    return;
  }

  list.innerHTML = trades.map((t, i) => {
    const isBuy = t.side === 'BUY';
    const ts    = t.timestamp ? new Date(t.timestamp*1000).toLocaleString('nl-NL',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
    const tid   = `wft-${i}`;
    return `<div class="wf-row ${isBuy?'buy':'sell'}">
      <span class="wf-whale">${t.whale_name}</span>
      <span class="${isBuy?'wf-side-buy':'wf-side-sell'}">${t.side}</span>
      <span class="${t.outcome==='YES'?'wf-outcome-yes':'wf-outcome-no'}">${t.outcome}</span>
      <span class="wf-size">$${t.size.toLocaleString('en',{maximumFractionDigits:0})}</span>
      <span class="wf-price">${(t.price*100).toFixed(0)}%</span>
      <span class="wf-title">${t.title}</span>
      <span style="font-size:9px;color:var(--text2);white-space:nowrap">${ts}</span>
      <button id="${tid}" onclick="copyWhaleFeedTrade(${i},'${tid}')" style="font-size:9px;padding:3px 8px;background:transparent;border:1px solid var(--border2);color:var(--text2);border-radius:3px;cursor:pointer;white-space:nowrap;font-family:'IBM Plex Mono',monospace">COPY</button>
    </div>`;
  }).join('');
}

async function copyWhaleFeedTrade(idx, btnId) {
  const t   = _wfAllTrades[idx];
  if (!t) return;
  const btn = document.getElementById(btnId);
  if (btn) { btn.textContent = '...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/whale-portfolio/copy', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        question:     t.title,
        direction:    t.outcome === 'YES' ? 'YES' : 'NO',
        entry_price:  t.price,
        amount:       10,
        condition_id: t.condition_id || '',
        market_id:    '',
        note:         `[WHALE:${t.whale_name}]`,
      }),
    });
    const d = await r.json();
    if (btn) {
      if (d.ok) {
        btn.textContent = '✓';
        btn.style.color = 'var(--go)';
        btn.style.borderColor = 'var(--go)';
        loadWhalePortfolioStats();
      } else {
        btn.textContent = 'ERR';
        btn.style.color = 'var(--danger)';
        btn.disabled = false;
      }
    }
  } catch(e) {
    if (btn) { btn.textContent = 'ERR'; btn.disabled = false; }
  }
}

function copyWhaleTrade(pos) {
  openModal({
    question: pos.title,
    conditionId: pos.condition_id || '',
    slug: pos.slug || '',
    yes: pos.outcome === 'Yes' ? pos.cur_price : (1 - pos.cur_price),
    no:  pos.outcome === 'No'  ? pos.cur_price : (1 - pos.cur_price),
  }, pos.outcome === 'Yes' ? 'yes' : 'no');
}

function openHurricaneTrade(opp) {
  const side = opp.direction === 'BUY YES' ? 'yes' : 'no';
  const price = side === 'yes' ? opp.poly_price : (1 - opp.poly_price);
  openModal({
    question: opp.question,
    conditionId: '',
    slug: '',
    yes: opp.poly_price,
    no: 1 - opp.poly_price,
  }, side);
}

// ── Portfolio ─────────────────────────────────────────────────────────────────

async function updatePortfolioPrices() {
  const btn = document.getElementById('pf-update-prices-btn');
  if (btn) { btn.textContent = '↻ Bezig...'; btn.disabled = true; }
  try {
    await fetch('/api/portfolio/update_prices', {method:'POST'});
    await refreshPortfolio();
  } finally {
    if (btn) { btn.textContent = '↻ UPDATE PRICES'; btn.disabled = false; }
  }
}

async function refreshPortfolio() {
  document.getElementById('portfolio-sub').textContent = 'Laden...';
  try {
    const r = await fetch('/api/portfolio');
    const d = await r.json();
    if (d.error) { document.getElementById('portfolio-positions').innerHTML = `<div class="no-opps">${d.error}</div>`; return; }

    // Stats
    const pnlColor = v => v > 0 ? 'var(--go)' : v < 0 ? 'var(--danger)' : 'var(--text)';
    const pnlSign  = v => v > 0 ? '+' : '';
    document.getElementById('pf-equity').textContent    = `$${d.total_equity}`;
    document.getElementById('pf-equity').style.color    = pnlColor(d.total_pnl);
    document.getElementById('pf-cash').textContent      = `$${d.cash}`;
    document.getElementById('pf-realized').textContent  = `${pnlSign(d.realized_pnl)}$${d.realized_pnl}`;
    document.getElementById('pf-realized').style.color  = pnlColor(d.realized_pnl);
    document.getElementById('pf-unrealized').textContent= `${pnlSign(d.unrealized_pnl)}$${d.unrealized_pnl}`;
    document.getElementById('pf-unrealized').style.color= pnlColor(d.unrealized_pnl);
    document.getElementById('pf-winrate').textContent   = d.closed_positions ? `${d.win_rate}% (${d.wins}W/${d.losses}L)` : '—';

    // ── Risk metrics ─────────────────────────────────────────────────────
    const riskColor = (v, good, warn) => v >= good ? 'var(--go)' : v >= warn ? 'var(--ice)' : v < 0 ? 'var(--danger)' : 'var(--text)';
    const riskVal   = (id, v, fmt, good, warn) => {
      const el = document.getElementById(id);
      if (v === null || v === undefined) { el.textContent = '—'; el.style.color = 'var(--text2)'; return; }
      el.textContent = fmt(v);
      el.style.color = riskColor(v, good, warn);
    };

    riskVal('pf-sharpe', d.sharpe,       v => v >= 99 ? '99+ ∞' : v.toFixed(2), 2.0, 1.0);
    riskVal('pf-sortino', d.sortino,     v => v >= 99 ? '99+ ∞' : v.toFixed(2), 3.0, 1.5);
    riskVal('pf-calmar',  d.calmar,      v => v >= 99 ? '99+ ∞' : v.toFixed(2), 3.0, 1.0);
    riskVal('pf-pf',      d.profit_factor, v => v >= 99 ? '∞' : v.toFixed(2),  2.0, 1.5);
    riskVal('pf-kelly',   d.kelly_pct,   v => `${v.toFixed(1)}%`,              50,  20);

    const ddEl = document.getElementById('pf-maxdd');
    if (d.max_drawdown !== null && d.max_drawdown !== undefined) {
      ddEl.textContent = `${d.max_drawdown}%`;
      ddEl.style.color = d.max_drawdown < -30 ? 'var(--danger)' : d.max_drawdown < -15 ? 'var(--ice)' : 'var(--go)';
    } else { ddEl.textContent = '—'; ddEl.style.color = 'var(--text2)'; }

    const varEl = document.getElementById('pf-var95');
    if (d.var_95 !== null && d.var_95 !== undefined) {
      varEl.textContent = `${d.var_95}%`;
      varEl.style.color = d.var_95 < -80 ? 'var(--danger)' : d.var_95 < -50 ? 'var(--amber)' : 'var(--go)';
    } else { varEl.textContent = '—'; varEl.style.color = 'var(--text2)'; }

    document.getElementById('portfolio-sub').textContent =
      `$${d.starting_balance} start · ${d.trade_count} trades · ${pnlSign(d.total_pnl_pct)}${d.total_pnl_pct}% totaal`;

    // Trade source split (null-safe)
    const setEl = (id, text, color) => { const el = document.getElementById(id); if (el) { el.textContent = text; if (color) el.style.color = color; } };
    const wt = d.whale_trades ?? 0, mt = d.model_trades ?? 0;
    setEl('src-whale-trades', `${wt} trades (${d.whale_open ?? 0} open)`);
    setEl('src-model-trades', `${mt} trades (${d.model_open ?? 0} open)`);
    const wpnl = d.whale_pnl ?? 0, mpnl = d.model_pnl ?? 0;
    setEl('src-whale-pnl', `${pnlSign(wpnl)}$${wpnl.toFixed(2)}`, pnlColor(wpnl));
    setEl('src-model-pnl', `${pnlSign(mpnl)}$${mpnl.toFixed(2)}`, pnlColor(mpnl));

    // Posities tabel
    const positions = d.positions || [];
    if (!positions.length) {
      document.getElementById('portfolio-positions').innerHTML = '<div style="color:var(--text2);padding:20px;text-align:center">Geen posities — laat de auto trader draaien (DRY RUN)</div>';
      return;
    }

    const statusIcon = s => s === 'open' ? '◉' : s === 'won' ? '✓' : s === 'sold' ? '↩' : '✗';
    const statusCol  = s => s === 'open' ? 'var(--ice)' : s === 'won' || s === 'sold' ? 'var(--go)' : 'var(--danger)';

    const openPos   = positions.filter(p => p.status === 'open');
    const closedPos = positions.filter(p => p.status !== 'open');

    const rowGrid = `display:grid;grid-template-columns:24px 40px 24px 50px 1fr 55px 50px 50px 75px 60px;gap:4px;padding:6px 4px;border-bottom:1px solid rgba(0,200,255,0.05);font-size:12px;align-items:center`;
    const headGrid = `display:grid;grid-template-columns:24px 40px 24px 50px 1fr 55px 50px 50px 75px 60px;gap:4px;padding:6px 4px;border-bottom:1px solid var(--ice3);font-size:10px;color:var(--text2);text-transform:uppercase`;

    const renderRow = pos => {
      const pnl = pos.status === 'open' ? pos.unrealized_pnl : pos.pnl;
      const col = pnlColor(pnl);
      const sellBtn = pos.status === 'open'
        ? `<button onclick="sellPosition('${pos.id}')" style="font-size:10px;padding:2px 6px;background:rgba(255,80,80,0.15);border:1px solid var(--danger);color:var(--danger);border-radius:4px;cursor:pointer">SELL</button>`
        : `<span style="font-size:11px;color:${statusCol(pos.status)}">${pos.status.toUpperCase()}</span>`;
      const note = pos.note || '';
      const isWhale = note.includes('WHALE');
      const tag = isWhale
        ? `<span style="font-size:9px;background:rgba(255,200,0,0.2);color:#ffc800;border:1px solid #ffc800;border-radius:3px;padding:1px 4px">🐋</span>`
        : `<span style="font-size:9px;background:rgba(0,200,255,0.1);color:var(--accent);border:1px solid var(--accent);border-radius:3px;padding:1px 4px">M</span>`;
      return `<div style="${rowGrid}">
        <div style="color:${statusCol(pos.status)}">${statusIcon(pos.status)}</div>
        <div style="color:var(--text2);font-size:11px">${pos.id}</div>
        <div>${tag}</div>
        <div style="color:${pos.direction==='YES'?'var(--go)':'var(--danger)'};font-weight:700">${pos.direction}</div>
        <div style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--text)">${pos.question}</div>
        <div>$${pos.amount}</div>
        <div style="color:var(--text2)">${(pos.entry_price*100).toFixed(0)}%</div>
        <div style="color:var(--text2)">${pos.status==='open' ? (pos.current_price*100).toFixed(0)+'%' : (pos.exit_price*100).toFixed(0)+'%'}</div>
        <div style="color:${col};font-weight:700">${pnlSign(pnl)}$${Math.abs(pnl).toFixed(2)}</div>
        <div>${sellBtn}</div>
      </div>`;
    };

    // Closed trades samenvatting
    const totalClosedPnl = closedPos.reduce((s, p) => s + (p.pnl || 0), 0);
    const wins  = closedPos.filter(p => p.status === 'won' || (p.status === 'sold' && p.pnl > 0)).length;
    const losses= closedPos.filter(p => p.status === 'lost' || (p.status === 'sold' && p.pnl <= 0)).length;

    const closedSection = closedPos.length ? `
      <div style="margin-top:20px;margin-bottom:6px;display:flex;align-items:center;gap:12px">
        <div style="font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.08em">Closed Trades (${closedPos.length})</div>
        <div style="font-size:11px;color:${pnlColor(totalClosedPnl)};font-weight:700">${pnlSign(totalClosedPnl)}$${totalClosedPnl.toFixed(2)} gerealiseerd</div>
        <div style="font-size:11px;color:var(--text2)">${wins}W / ${losses}L</div>
      </div>
      <div style="${headGrid}">
        <div></div><div>ID</div><div></div><div>Dir</div><div>Markt</div><div>Inzet</div><div>Entry</div><div>Exit</div><div>P&L</div><div>Status</div>
      </div>
      ${closedPos.map(renderRow).join('')}
    ` : '';

    document.getElementById('portfolio-positions').innerHTML = `
      <div style="margin-bottom:6px;display:flex;align-items:center;gap:12px">
        <div style="font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.08em">Open Posities (${openPos.length})</div>
      </div>
      <div style="${headGrid}">
        <div></div><div>ID</div><div></div><div>Dir</div><div>Markt</div><div>Inzet</div><div>Entry</div><div>Nu</div><div>P&L</div><div>Actie</div>
      </div>
      ${openPos.map(renderRow).join('')}
      ${closedSection}`;
  } catch(e) {
    document.getElementById('portfolio-sub').textContent = `Fout: ${e.message}`;
  }
}

async function refreshEquityCurve() {
  try {
    const r = await fetch('/api/portfolio-history');
    const d = await r.json();
    const points = (d.points || []);
    const svg = document.getElementById('equity-curve-svg');
    const empty = document.getElementById('equity-curve-empty');
    if (!points.length) { svg.innerHTML = ''; empty.style.display = 'flex'; return; }
    empty.style.display = 'none';
    const W = svg.parentElement.clientWidth - 24;
    const H = svg.parentElement.clientHeight - 24;
    const equities = points.map(p => p.equity);
    const minE = Math.min(...equities);
    const maxE = Math.max(...equities);
    const rangeE = maxE - minE || 1;
    const xs = points.map((_, i) => (i / Math.max(points.length - 1, 1)) * W);
    const ys = equities.map(e => H - ((e - minE) / rangeE) * H * 0.9 - H * 0.05);
    const polyline = xs.map((x, i) => `${x},${ys[i]}`).join(' ');
    const lastColor = equities[equities.length - 1] >= equities[0] ? '#00E87A' : '#FF3355';
    svg.innerHTML = `
      <polyline points="${polyline}" fill="none" stroke="${lastColor}" stroke-width="1.5" stroke-linejoin="round"/>
      <circle cx="${xs[xs.length-1]}" cy="${ys[ys.length-1]}" r="3" fill="${lastColor}"/>
      <text x="0" y="10" font-size="9" fill="var(--text2)">$${minE.toFixed(0)}</text>
      <text x="0" y="${H}" font-size="9" fill="var(--text2)">$${maxE.toFixed(0)}</text>
      <text x="${W - 40}" y="10" font-size="9" fill="${lastColor}">$${equities[equities.length-1].toFixed(2)}</text>
    `;
  } catch(e) { /* stil falen */ }
}

async function confirmReset() {
  if (!confirm('Portfolio resetten? Een backup wordt automatisch gemaakt in data/backups/.')) return;
  const balance = parseFloat(prompt('Startkapitaal (USDC):', '500') || '500');
  if (isNaN(balance) || balance <= 0) return;
  const r = await fetch('/api/portfolio/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({balance})});
  const d = await r.json();
  if (d.ok) { alert(`Reset klaar. Backup opgeslagen in data/backups/.`); }
  refreshPortfolio();
}

async function backupPortfolio() {
  const r = await fetch('/api/portfolio/backup', {method:'POST'});
  const d = await r.json();
  if (d.ok) alert(`Backup: ${d.filename}`);
  else alert('Backup mislukt: ' + (d.error || '?'));
}

async function sellPosition(id) {
  if (!confirm(`Positie ${id} verkopen tegen huidige marktprijs?`)) return;
  const r = await fetch('/api/portfolio/sell', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  const d = await r.json();
  if (d.error) { alert('Fout: ' + d.error); return; }
  const pnl = d.pnl >= 0 ? '+$'+d.pnl : '-$'+Math.abs(d.pnl);
  alert(`${id} verkocht @ ${(d.exit_price*100).toFixed(0)}% — P&L: ${pnl}`);
  refreshPortfolio();
}

// Auto-refresh portfolio elke 60s (server update elke 5 min op achtergrond)
setInterval(() => { if (currentTab === 'portfolio') { refreshPortfolio(); refreshEquityCurve(); } }, 60000);

// ── Auto Trader ──────────────────────────────────────────────────────────────

let autoRefreshTimer = null;

function updateCfgLabel(sliderId, labelId, prefix) {
  const val = document.getElementById(sliderId).value;
  const el  = document.getElementById(labelId);
  el.textContent = prefix === '$' ? `$${val}` : prefix === '%' ? `${val}%` : `${val}${prefix}`;
}

async function toggleAutoTrader(enabled) {
  await fetch('/api/autotrader/toggle', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({enabled})
  });
  refreshAutoStatus();
}

async function saveAutoConfig() {
  const config = {
    min_gap:       parseFloat(document.getElementById('cfg-min-gap').value) / 100,
    max_trade:     parseFloat(document.getElementById('cfg-max-trade').value),
    daily_budget:  parseFloat(document.getElementById('cfg-budget').value),
    scan_interval: parseInt(document.getElementById('cfg-interval').value) * 60,
    dry_run:       document.getElementById('dry-run-toggle').checked,
    whale_copy:    document.getElementById('whale-copy-toggle').checked,
  };
  const r = await fetch('/api/autotrader/config', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(config)
  });
  const d = await r.json();
  document.getElementById('auto-sub').textContent = d.ok ? 'Config opgeslagen' : 'Fout bij opslaan';
}

async function runManualScan() {
  document.getElementById('auto-sub').textContent = 'Handmatige scan gestart...';
  await fetch('/api/autotrader/scan', {method: 'POST'});
  setTimeout(refreshAutoStatus, 2000);
}

async function refreshAutoStatus() {
  try {
    const r = await fetch('/api/autotrader/status');
    const d = await r.json();

    // Toggle
    document.getElementById('auto-enabled-toggle').checked = d.enabled;

    // Sliders
    document.getElementById('cfg-min-gap').value     = Math.round(d.min_gap * 100);
    document.getElementById('cfg-max-trade').value   = d.max_trade;
    document.getElementById('cfg-budget').value      = d.daily_budget;
    document.getElementById('cfg-interval').value    = Math.round(d.scan_interval / 60);
    document.getElementById('dry-run-toggle').checked  = d.dry_run;
    document.getElementById('whale-copy-toggle').checked = d.whale_copy;
    updateCfgLabel('cfg-min-gap',   'cfg-min-gap-val',   '%');
    updateCfgLabel('cfg-max-trade', 'cfg-max-trade-val', '$');
    updateCfgLabel('cfg-budget',    'cfg-budget-val',    '$');
    updateCfgLabel('cfg-interval',  'cfg-interval-val',  'm');

    // Stats
    document.getElementById('at-spent').textContent  = `$${d.spent_today}`;
    document.getElementById('at-budget').textContent = `$${d.budget_left}`;
    // Deployed % tonen indien beschikbaar
    const deployedEl = document.getElementById('at-deployed');
    if (deployedEl && d.max_deployed_pct !== undefined) {
      deployedEl.textContent = `max ${(d.max_deployed_pct*100).toFixed(0)}%`;
    }
    document.getElementById('at-trades').textContent = d.trades_today;
    document.getElementById('at-next').textContent   = d.next_scan || '—';

    // Status badge
    const badge = document.getElementById('auto-status-badge');
    const statusColors = {idle:'var(--text2)', scanning:'var(--warn)', trading:'var(--go)', error:'var(--danger)'};
    badge.textContent = (d.dry_run ? '[DRY] ' : '') + d.status.toUpperCase();
    badge.style.color = statusColors[d.status] || 'var(--text2)';

    // Log
    const logEl = document.getElementById('auto-log');
    if (d.log && d.log.length) {
      logEl.innerHTML = d.log.map(l => `<div>${l}</div>`).join('');
      logEl.scrollTop = 0;
    }

    // Recent trades
    if (d.recent_trades && d.recent_trades.length) {
      document.getElementById('auto-trades').innerHTML = d.recent_trades.map(t => {
        const col = t.success ? 'var(--go)' : 'var(--danger)';
        const icon = t.success ? '✓' : '✗';
        const dry = t.dry_run ? '[D]' : '';
        return `<div style="border-bottom:1px solid var(--ice3);padding:5px 0;font-size:11px">
          <div style="color:${col}">${icon} ${dry} ${t.direction} $${t.amount}</div>
          <div style="color:var(--text2)">${t.question}</div>
          <div style="color:var(--text2);font-size:10px">gap=${t.gap > 0 ? '+' : ''}${t.gap}% · ${t.timestamp}</div>
        </div>`;
      }).join('');
    }
  } catch(e) {}
}

// Auto-refresh status elke 5s als op de tab
setInterval(() => { if (currentTab === 'auto') refreshAutoStatus(); }, 5000);

// ── Settings / Telegram ───────────────────────────────────────────────────────

async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    if (d.telegram_token) document.getElementById('tg-token').value = d.telegram_token;
    if (d.telegram_chat)  document.getElementById('tg-chat').value  = d.telegram_chat;
    if (d.alert_min_gap)  document.getElementById('tg-min-gap').value = Math.round(d.alert_min_gap * 100);
  } catch(e) {}
}

async function saveTelegramSettings() {
  const r = await fetch('/api/settings/telegram', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      token:   document.getElementById('tg-token').value.trim(),
      chat_id: document.getElementById('tg-chat').value.trim(),
      min_gap: parseFloat(document.getElementById('tg-min-gap').value) / 100,
    })
  });
  const d = await r.json();
  document.getElementById('tg-status').textContent = d.ok ? '✓ Opgeslagen' : `✗ ${d.error}`;
  document.getElementById('tg-status').style.color = d.ok ? 'var(--go)' : 'var(--danger)';
}

async function testTelegram() {
  document.getElementById('tg-status').textContent = 'Testen...';
  const r = await fetch('/api/settings/telegram/test', {method: 'POST'});
  const d = await r.json();
  document.getElementById('tg-status').textContent = d.ok ? `✓ Verbonden (@${d.bot})` : `✗ ${d.error}`;
  document.getElementById('tg-status').style.color = d.ok ? 'var(--go)' : 'var(--danger)';
}

async function saveAlertSettings() {
  await fetch('/api/settings/alerts', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      weer_gap:       parseFloat(document.getElementById('alert-weer-gap').value) / 100,
      on_scan:        document.getElementById('alert-on-scan').checked,
      on_auto_trade:  document.getElementById('alert-auto-trade').checked,
    })
  });
}

// Initial load — start on WEER tab
document.getElementById('weer-panel').style.display      = 'block';
document.getElementById('portfolio-panel').style.display = 'none';
document.getElementById('whales-panel').style.display    = 'none';
document.getElementById('markten-panel').style.display   = 'none';
document.getElementById('auto-panel').style.display      = 'none';
document.getElementById('settings-panel').style.display  = 'none';
document.getElementById('momentum-panel').style.display  = 'none';
setTimeout(runWeerScan, 600);

// ── BTC Momentum ──────────────────────────────────────────────────────────────
let _momInterval = null;

async function loadMomentum() {
  const r = await fetch('/api/btc-momentum/status').catch(() => null);
  if (!r || !r.ok) return;
  const d = await r.json();

  const dot  = document.getElementById('mom-dot');
  const txt  = document.getElementById('mom-status-text');
  const dry  = document.getElementById('mom-dry-tag');
  const btn  = document.getElementById('mom-toggle-btn');
  const price = document.getElementById('mom-btc-price');

  if (d.running) {
    dot.style.background = 'var(--go)';
    dot.style.boxShadow  = '0 0 6px var(--go)';
    txt.style.color      = 'var(--go)';
    txt.textContent      = 'ACTIEF';
    btn.textContent      = '■ STOP';
    if (_momInterval === null) {
      _momInterval = setInterval(loadMomentum, 5000);
    }
  } else {
    dot.style.background = '#555';
    dot.style.boxShadow  = 'none';
    txt.style.color      = 'var(--text2)';
    txt.textContent      = 'GESTOPT';
    btn.textContent      = '▶ START';
    clearInterval(_momInterval);
    _momInterval = null;
  }

  dry.style.display = (d.running && d.dry_run) ? 'inline' : 'none';
  if (d.btc_price) price.textContent = 'BTC $' + d.btc_price.toLocaleString('en-US', {maximumFractionDigits: 0});

  document.getElementById('mom-trades-today').textContent = d.trades_today ?? '—';
  document.getElementById('mom-trades-total').textContent = d.total_trades ?? '—';
  document.getElementById('mom-wr').textContent  = d.win_rate ? d.win_rate + '%' : '—';
  const pnlEl = document.getElementById('mom-pnl');
  if (d.total_pnl !== undefined) {
    pnlEl.textContent = (d.total_pnl >= 0 ? '+' : '') + '$' + d.total_pnl.toFixed(2);
    pnlEl.style.color = d.total_pnl >= 0 ? 'var(--go)' : 'var(--stop)';
  }

  const sig = d.last_signal;
  const sigEl = document.getElementById('mom-signal');
  if (sig) {
    sigEl.textContent = sig.direction + ' $' + sig.move_usd.toFixed(0);
    sigEl.style.color = sig.direction === 'UP' ? 'var(--go)' : 'var(--stop)';
  }

  if (d.log && d.log.length) {
    document.getElementById('mom-log').innerHTML =
      d.log.map(l => `<div style="margin-bottom:2px">${l}</div>`).join('');
  }
}

async function toggleMomentum() {
  const r = await fetch('/api/btc-momentum/status').catch(() => null);
  if (!r) return;
  const d = await r.json();
  const method = 'POST';
  await fetch('/api/btc-momentum/toggle', {
    method, headers: {'Content-Type':'application/json'},
    body: JSON.stringify({dry_run: true})
  });
  setTimeout(loadMomentum, 500);
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/markets")
def api_markets():
    from flask import request
    category = request.args.get("category", "all")
    try:
        data = fetch_markets(category=category)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/kelly")
def api_kelly():
    from flask import request
    from kelly import kelly
    try:
        market_price     = float(request.args.get("price", 0.5))
        your_probability = float(request.args.get("prob", 0.5))
        bankroll         = float(request.args.get("bankroll", 500))
        fraction         = float(request.args.get("fraction", 0.25))
        result = kelly(market_price, your_probability, bankroll, fraction)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/weather/heatmap")
def api_weather_heatmap():
    try:
        from weather_scanner import fetch_all_city_temps
        data = fetch_all_city_temps()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/weather")
def api_weather():
    try:
        from weather_scanner import scan, CITIES
        opps = scan()

        # Telegram alerts + auto trades voor sterke kansen
        try:
            import alerts as _alerts
            from auto_trader import state as _at_state, calculate_trade_size, execute_trade, AutoTrade
            from datetime import datetime, timezone
            global _alerted_opportunities, _alerted_date
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != _alerted_date:
                _alerted_opportunities = set()
                _alerted_date = today
            min_gap = _settings.get("alert_min_gap", 0.20)
            for o in opps:
                if abs(o.gap) >= min_gap:
                    # Alleen alert als deze kans nog niet gestuurd is vandaag
                    alert_key = f"{o.question}:{round(o.gap,2)}"
                    if alert_key not in _alerted_opportunities:
                        _alerts.notify_opportunity(o, source="WEER")
                        _alerted_opportunities.add(alert_key)
                    # Trade plaatsen als auto trader aan staat
                    if _at_state.config.enabled and abs(o.gap) >= _at_state.config.min_gap:
                        market_key = o.question
                        if market_key not in _at_state.traded_markets and _at_state.budget_left > 1:
                            amount = calculate_trade_size(o, _at_state.config)
                            if amount >= 1:
                                success, note = execute_trade(o, amount, _at_state.config.dry_run)
                                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                                trade = AutoTrade(
                                    timestamp=ts, question=o.question, direction=o.direction,
                                    poly_price=o.poly_price, model_prob=o.model_prob, gap=o.gap,
                                    amount=amount if success else 0, dry_run=_at_state.config.dry_run,
                                    success=success, note=note,
                                )
                                with _at_state._lock:
                                    _at_state.trades_today.append(trade)
                                    if success:
                                        _at_state.traded_markets.add(market_key)
                                if success:
                                    try:
                                        from portfolio import record_trade
                                        outcome = "YES" if "YES" in o.direction else "NO"
                                        price = o.poly_price if outcome == "YES" else (1 - o.poly_price)
                                        record_trade(question=o.question, direction=outcome,
                                            amount=amount, entry_price=price, model_prob=o.model_prob,
                                            gap=o.gap, condition_id=getattr(o, "condition_id", ""),
                                            market_id=getattr(o, "market_id", ""))
                                    except Exception:
                                        pass
                                    _alerts.notify_auto_trade(trade, dry_run=_at_state.config.dry_run)
        except Exception:
            pass
        result = []
        for o in opps:
            city_lower = o.city.lower()
            coords = CITIES.get(city_lower, (0, 0))
            # forecast_temp_c voor kleur in frontend
            ft_c = o.forecast_temp if o.unit == "C" else (o.forecast_temp - 32) * 5 / 9
            result.append({
                "question":      o.question,
                "city":          o.city,
                "date":          o.date,
                "condition":     o.condition,
                "temp_low":      o.temp_low,
                "temp_high":     o.temp_high,
                "unit":          o.unit,
                "poly_price":    o.poly_price,
                "forecast_temp": o.forecast_temp,
                "forecast_temp_c": round(ft_c, 1),
                "model_prob":    o.model_prob,
                "gap":           o.gap,
                "direction":     o.direction,
                "volume":        o.volume,
                "lat":           coords[0],
                "lon":           coords[1],
                "condition_id":  o.condition_id,
                "market_id":     o.market_id,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/f1")
def api_f1():
    try:
        from f1_weather import fetch_upcoming_races, fetch_rain_forecast, fetch_f1_polymarkets, \
                               match_driver, WET_DELTA, RAIN_THRESHOLD
        import json as _json

        races = fetch_upcoming_races()
        poly_markets = fetch_f1_polymarkets()

        race_info    = None
        opportunities = []

        for race in races:
            rain_pct, rain_mm, weer_ok = fetch_rain_forecast(race["lat"], race["lon"], race["date"])
            race_info = {
                "name":     race["name"],
                "date":     race["date"],
                "rain_pct": rain_pct,
                "rain_mm":  rain_mm,
                "weer_ok":  weer_ok,
            }

            if not weer_ok:
                break  # weerdata nog niet beschikbaar, stop hier

            for market in poly_markets:
                q = market.get("question", "")
                race_keywords = [
                    w.lower() for w in race["name"].split()
                    if len(w) > 3 and w.lower() not in ("grand", "prix")
                ]
                if not any(kw in q.lower() for kw in race_keywords):
                    continue

                driver = match_driver(q)
                if not driver:
                    continue

                prices = market.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    try: prices = _json.loads(prices)
                    except: continue

                poly_price = float(prices[0]) if prices else 0
                if not (0.02 < poly_price < 0.98):
                    continue

                delta       = WET_DELTA.get(driver, 0)
                rain_factor = rain_pct / 100
                adj_delta   = delta * rain_factor
                adj_price   = max(0.02, min(0.98, poly_price + adj_delta / 100))
                gap         = (adj_price - poly_price) * 100

                if rain_pct >= RAIN_THRESHOLD and abs(gap) >= 5:
                    opportunities.append({
                        "question":   q,
                        "driver":     driver,
                        "poly_price": round(poly_price, 3),
                        "adj_price":  round(adj_price, 3),
                        "gap":        round(gap, 1),
                        "wet_delta":  delta,
                        "direction":  "BUY YES" if gap > 0 else "BUY NO",
                        "race":       race["name"],
                    })

        opportunities.sort(key=lambda o: abs(o["gap"]), reverse=True)
        return jsonify({"race": race_info, "opportunities": opportunities})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _calc_portfolio_metrics(positions: list) -> dict:
    """
    Professionele risicostatistieken — hedge fund stijl.

    Metrics:
      sharpe        — Jaarlijkse Sharpe ratio (risk-free = 0)
      sortino       — Sortino ratio (alleen downside volatiliteit)
      calmar        — Calmar ratio (return / |max drawdown|)
      max_drawdown  — Maximale drawdown in % op equity curve
      profit_factor — Bruto winst / bruto verlies
      var_95        — Value at Risk 95% (slechtste 5% van trades)
      kelly_pct     — Kelly criterion optimale bet size in %
      avg_trade_return — Gemiddeld rendement per trade in %
    """
    import math

    closed = [p for p in positions if p.get("status") in ("won", "lost", "sold")]

    # Gebruik ook open posities (met unrealized P&L) als er te weinig closed zijn
    open_pos = [p for p in positions if p.get("status") == "open"]
    all_trades = list(closed)
    for p in open_pos:
        amt   = float(p.get("amount") or 0)
        shares = float(p.get("shares") or 0)
        cur   = float(p.get("current_price") or p.get("entry_price") or 0)
        upnl  = round(shares * cur - amt, 4) if amt > 0 else 0
        if amt > 0:
            # Synthetische trade-entry voor berekening
            all_trades.append({"amount": amt, "pnl": upnl,
                                "timestamp": p.get("timestamp", ""),
                                "status": "open_synthetic"})

    if len(all_trades) < 2:
        return {
            "sharpe": None, "sortino": None, "calmar": None,
            "max_drawdown": None, "profit_factor": None,
            "var_95": None, "kelly_pct": None, "avg_trade_return": None,
        }

    # ── P&L-gebaseerde returns (% van inzet per trade) ────────────────────
    pct_returns = []
    for p in all_trades:
        amt = float(p.get("amount") or 0)
        pnl = float(p.get("pnl") or 0)
        if amt > 0:
            pct_returns.append(pnl / amt)

    n    = len(pct_returns)
    if n < 1:
        return {"sharpe": None, "sortino": None, "calmar": None,
                "max_drawdown": None, "profit_factor": None,
                "var_95": None, "kelly_pct": None, "avg_trade_return": None}
    mean = sum(pct_returns) / n

    # Standaard deviatie (sample)
    var  = sum((r - mean) ** 2 for r in pct_returns) / max(n - 1, 1)
    std  = var ** 0.5

    # Annualiseer: 150 trades/jaar
    TRADES_PER_YEAR = 150
    annual_return   = mean * TRADES_PER_YEAR
    annual_vol      = std  * (TRADES_PER_YEAR ** 0.5)

    # ── Sharpe ───────────────────────────────────────────────────────────
    if annual_vol > 1e-9:
        sharpe = round(annual_return / annual_vol, 2)
    else:
        sharpe = 99.0   # perfecte consistentie → top-tier

    # ── Sortino (downside std) ────────────────────────────────────────────
    neg_returns  = [r for r in pct_returns if r < 0]
    if neg_returns:
        down_var   = sum(r ** 2 for r in neg_returns) / max(n - 1, 1)
        down_std   = down_var ** 0.5
        annual_dv  = down_std * (TRADES_PER_YEAR ** 0.5)
        sortino    = round(annual_return / annual_dv, 2) if annual_dv > 1e-9 else 99.0
    else:
        sortino = 99.0   # geen verliezen → maximale Sortino

    # ── Max drawdown op cumulatieve P&L equity curve ─────────────────────
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    sorted_closed = sorted(all_trades, key=lambda x: x.get("timestamp", ""))
    for p in sorted_closed:
        amt = float(p.get("amount") or 0)
        pnl = float(p.get("pnl") or 0)
        if amt > 0:
            equity *= (1 + pnl / amt)
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak
            if dd < max_dd:
                max_dd = dd

    # ── Calmar ratio ──────────────────────────────────────────────────────
    abs_dd = abs(max_dd)
    calmar = round(annual_return / abs_dd, 2) if abs_dd > 1e-4 else 99.0

    # ── Profit factor ─────────────────────────────────────────────────────
    gross_win  = sum(p.get("pnl", 0) for p in all_trades if p.get("pnl", 0) > 0)
    gross_loss = sum(abs(p.get("pnl", 0)) for p in all_trades if p.get("pnl", 0) < 0)
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0

    # ── VaR 95% — slechtste 5% van trades ────────────────────────────────
    sorted_ret = sorted(pct_returns)
    var_idx    = max(int(n * 0.05) - 1, 0)
    var_95     = round(sorted_ret[var_idx] * 100, 1)   # in %

    # ── Kelly criterion — alleen op closed trades (won/lost/sold) ─────────
    closed_for_kelly = [p for p in all_trades if p.get("status") in ("won", "lost", "sold")]
    if not closed_for_kelly:
        closed_for_kelly = all_trades   # fallback als er geen closed zijn
    wins_lst   = [p for p in closed_for_kelly if p.get("pnl", 0) > 0]
    losses_lst = [p for p in closed_for_kelly if p.get("pnl", 0) < 0]
    win_rate_k = len(wins_lst) / max(len(closed_for_kelly), 1)
    if wins_lst and losses_lst:
        avg_win  = sum(p["pnl"] / p["amount"] for p in wins_lst) / len(wins_lst)
        avg_loss = sum(abs(p["pnl"] / p["amount"]) for p in losses_lst) / len(losses_lst)
        kelly = win_rate_k - (1 - win_rate_k) / (avg_win / avg_loss) if avg_win > 0 else 0
    elif not losses_lst:
        kelly = win_rate_k   # 100% winrate → Kelly = 100%
    else:
        kelly = 0.0
    kelly_pct = round(kelly * 100, 1)

    return {
        "sharpe":           sharpe,
        "sortino":          sortino,
        "calmar":           calmar,
        "max_drawdown":     round(max_dd * 100, 1),
        "profit_factor":    profit_factor,
        "var_95":           var_95,
        "kelly_pct":        kelly_pct,
        "avg_trade_return": round(mean * 100, 1),
    }


@app.route("/api/portfolio")
def api_portfolio():
    try:
        from portfolio import get_stats, load_portfolio
        stats = get_stats()
        metrics = _calc_portfolio_metrics(stats.get("positions", []))
        stats.update(metrics)
        # Whale vs model trade source split
        p = load_portfolio()
        whale_trades = [x for x in p.positions if "WHALE" in x.get("note", "").upper()]
        model_trades = [x for x in p.positions if "WHALE" not in x.get("note", "").upper()]
        stats["whale_trades"]      = len(whale_trades)
        stats["model_trades"]      = len(model_trades)
        stats["whale_open"]        = sum(1 for x in whale_trades if x["status"] == "open")
        stats["model_open"]        = sum(1 for x in model_trades if x["status"] == "open")
        stats["whale_pnl"]         = round(sum(x["pnl"] for x in whale_trades if x["status"] in ("won","lost","sold")), 2)
        stats["model_pnl"]         = round(sum(x["pnl"] for x in model_trades if x["status"] in ("won","lost","sold")), 2)
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio-history")
def api_portfolio_history():
    """Leest portfolio_log.jsonl en geeft equity over tijd terug."""
    import pathlib
    log_path = pathlib.Path(__file__).parent / "data" / "portfolio_log.jsonl"
    if not log_path.exists():
        return jsonify({"points": []})
    points = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts") or entry.get("timestamp", "")
                    equity = entry.get("equity") or entry.get("total_equity")
                    if ts and equity is not None:
                        points.append({"ts": ts[:16], "equity": float(equity)})
                except Exception:
                    continue
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"points": points})


@app.route("/api/portfolio/reset", methods=["POST"])
def api_portfolio_reset():
    from flask import request as req
    try:
        from portfolio import reset_portfolio, backup_portfolio
        body = req.get_json() or {}
        balance = float(body.get("balance", 100))
        # Backup wordt automatisch gemaakt in reset_portfolio()
        p = reset_portfolio(balance)
        return jsonify({"ok": True, "balance": balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio/backups")
def api_portfolio_backups():
    """Lijst van beschikbare portfolio backups."""
    import pathlib
    backup_dir = pathlib.Path(__file__).parent / "data" / "backups"
    if not backup_dir.exists():
        return jsonify({"backups": []})
    backups = sorted(backup_dir.glob("portfolio_*.json"), reverse=True)
    result = []
    for b in backups[:20]:
        try:
            with open(b) as f:
                data = json.load(f)
            result.append({
                "filename": b.name,
                "date":     b.stem.split("_", 1)[1] if "_" in b.stem else "",
                "trades":   len(data.get("positions", [])),
                "cash":     data.get("cash", 0),
                "equity":   round(data.get("cash", 0) + sum(
                    p["amount"] for p in data.get("positions", []) if p.get("status") == "open"
                ), 2),
            })
        except Exception:
            continue
    return jsonify({"backups": result})


@app.route("/api/portfolio/backup", methods=["POST"])
def api_portfolio_backup():
    """Handmatige backup van het huidige portfolio."""
    try:
        from portfolio import backup_portfolio
        path = backup_portfolio(label="manual")
        if not path:
            return jsonify({"ok": False, "error": "Geen portfolio bestand gevonden"})
        import os
        return jsonify({"ok": True, "filename": os.path.basename(path)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/portfolio/update_prices", methods=["POST"])
def api_portfolio_update():
    try:
        from portfolio import update_position_prices
        result = update_position_prices()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio/sell", methods=["POST"])
def api_portfolio_sell():
    from flask import request as req
    try:
        from portfolio import sell_position
        body = req.get_json() or {}
        position_id = body.get("id", "")
        exit_price  = body.get("exit_price")  # optioneel, anders current_price
        result = sell_position(position_id, exit_price)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/autotrader/status")
def api_auto_status():
    from auto_trader import state
    return jsonify(state.to_dict())


@app.route("/api/autotrader/toggle", methods=["POST"])
def api_auto_toggle():
    from flask import request as req
    from auto_trader import state, start
    body = req.get_json()
    enabled = body.get("enabled", False)
    state.config.enabled = enabled
    if enabled and not state.running:
        start()
    state.add_log(f"Auto trading {'ingeschakeld' if enabled else 'uitgeschakeld'}")
    # Sla op in .env zodat herstart de instelling bewaart
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = open(env_path).readlines() if os.path.exists(env_path) else []
        key = "AUTO_TRADE"
        val = "true" if enabled else "false"
        updated = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f"{key}="):
                new_lines.append(f"{key}={val}\n")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={val}\n")
        open(env_path, "w").writelines(new_lines)
        os.environ[key] = val
    except Exception:
        pass
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/autotrader/config", methods=["POST"])
def api_auto_config():
    from flask import request as req
    from auto_trader import state
    body = req.get_json()
    cfg = state.config
    if "min_gap"        in body: cfg.min_gap        = float(body["min_gap"])
    if "max_trade"      in body: cfg.max_trade       = float(body["max_trade"])
    if "daily_budget"   in body: cfg.daily_budget    = float(body["daily_budget"])
    if "scan_interval"  in body: cfg.scan_interval   = int(body["scan_interval"])
    if "dry_run"        in body: cfg.dry_run         = bool(body["dry_run"])
    if "whale_copy"     in body: cfg.whale_copy      = bool(body["whale_copy"])
    if "whale_min_size" in body: cfg.whale_min_size  = float(body["whale_min_size"])
    state.add_log(f"Config bijgewerkt: gap={cfg.min_gap*100:.0f}% max=${cfg.max_trade} whale_copy={cfg.whale_copy} dry={cfg.dry_run}")
    return jsonify({"ok": True})


@app.route("/api/autotrader/scan", methods=["POST"])
def api_auto_scan():
    import threading
    from auto_trader import state, run_scan_and_trade, start
    if not state.running:
        start()
    t = threading.Thread(target=run_scan_and_trade, daemon=True)
    t.start()
    return jsonify({"ok": True})


# Settings storage (in-memory voor nu, .env voor persistentie)
_settings = {
    "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat":  os.getenv("TELEGRAM_CHAT_ID", ""),
    "alert_min_gap":  0.20,
    "alert_on_scan":  False,
    "alert_auto_trade": True,
}

# Bijhouden welke kansen al een alert hebben gekregen (reset dagelijks)
_alerted_opportunities: set = set()
_alerted_date: str = ""


@app.route("/api/settings")
def api_settings():
    return jsonify({
        "telegram_token": "***" if _settings["telegram_token"] else "",
        "telegram_chat":  _settings["telegram_chat"],
        "alert_min_gap":  _settings["alert_min_gap"],
    })


@app.route("/api/settings/telegram", methods=["POST"])
def api_settings_telegram():
    from flask import request as req
    body = req.get_json()
    if body.get("token"):
        _settings["telegram_token"] = body["token"]
        os.environ["TELEGRAM_BOT_TOKEN"] = body["token"]
    if body.get("chat_id"):
        _settings["telegram_chat"] = body["chat_id"]
        os.environ["TELEGRAM_CHAT_ID"] = body["chat_id"]
    if "min_gap" in body:
        _settings["alert_min_gap"] = float(body["min_gap"])
    # Schrijf naar .env
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = open(env_path).readlines() if os.path.exists(env_path) else []
        keys_to_update = {
            "TELEGRAM_BOT_TOKEN": _settings["telegram_token"],
            "TELEGRAM_CHAT_ID":   _settings["telegram_chat"],
        }
        new_lines = []
        updated = set()
        for line in lines:
            key = line.split("=")[0].strip()
            if key in keys_to_update:
                new_lines.append(f"{key}={keys_to_update[key]}\n")
                updated.add(key)
            else:
                new_lines.append(line)
        for key, val in keys_to_update.items():
            if key not in updated and val:
                new_lines.append(f"{key}={val}\n")
        with open(env_path, "w") as f:
            f.writelines(new_lines)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/settings/telegram/test", methods=["POST"])
def api_settings_telegram_test():
    import importlib
    import alerts
    importlib.reload(alerts)
    result = alerts.test_connection()
    return jsonify(result)


@app.route("/api/settings/alerts", methods=["POST"])
def api_settings_alerts():
    from flask import request as req
    body = req.get_json()
    _settings.update({k: body[k] for k in body if k in _settings})
    return jsonify({"ok": True})


@app.route("/api/whales")
def api_whales():
    try:
        from whale_tracker import KNOWN_WHALES, fetch_whale_positions, fetch_whale_activity
        import json as _json

        result = []
        for name, address in KNOWN_WHALES.items():
            positions = fetch_whale_positions(name, address)
            trades    = fetch_whale_activity(name, address, limit=20)
            total_val = sum(p.current_value for p in positions)
            result.append({
                "name":             name,
                "address":          address,
                "total_open_value": round(total_val, 2),
                "positions": [
                    {
                        "title":          p.title,
                        "outcome":        p.outcome,
                        "size":           round(p.size, 2),
                        "avg_price":      round(p.avg_price, 4),
                        "cur_price":      round(p.cur_price, 4),
                        "current_value":  round(p.current_value, 2),
                        "cash_pnl":       round(p.cash_pnl, 2),
                        "direction":      p.direction,
                        "condition_id":   p.condition_id,
                        "slug":           p.slug,
                        "end_date":       p.end_date,
                    }
                    for p in positions
                ],
                "trades": [
                    {
                        "timestamp": t.timestamp,
                        "side":      t.side,
                        "title":     t.title,
                        "price":     round(t.price, 4),
                        "usdc_size": round(t.usdc_size, 2),
                        "outcome":   t.outcome,
                    }
                    for t in trades
                ],
            })
        return jsonify({"whales": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/whales/add", methods=["POST"])
def api_whales_add():
    from flask import request as req
    try:
        from whale_tracker import lookup_by_username, KNOWN_WHALES
        body = req.get_json()
        username = body.get("username", "").strip()
        if not username:
            return jsonify({"ok": False, "error": "Geen gebruikersnaam opgegeven"})
        address = lookup_by_username(username)
        if not address:
            return jsonify({"ok": False, "error": f"Wallet niet gevonden voor @{username}"})
        KNOWN_WHALES[username] = address
        return jsonify({"ok": True, "name": username, "address": address})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Whale Portfolio endpoints ─────────────────────────────────────────────────

_WHALES_FILE          = os.path.join(os.path.dirname(__file__), "data", "whales.json")
_WHALE_PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "data", "whale_portfolio.json")
_WHALE_FOLLOW_STATE   = os.path.join(os.path.dirname(__file__), "data", "whale_follow_state.json")

# Auto-follow config
_WHALE_COPY_PCT      = 0.05   # 5% van resterende cash per trade
_WHALE_MIN_COPY_SIZE = 100.0  # alleen kopiëren als whale >= $100 inzet
_WHALE_CHECK_SECS    = 90     # check elke 90 seconden

# In-memory log van recente auto-copies
_whale_copy_log: list = []


def _load_follow_state() -> dict:
    if os.path.exists(_WHALE_FOLLOW_STATE):
        try:
            with open(_WHALE_FOLLOW_STATE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_follow_state(state: dict):
    os.makedirs(os.path.dirname(_WHALE_FOLLOW_STATE), exist_ok=True)
    with open(_WHALE_FOLLOW_STATE, "w") as f:
        json.dump(state, f, indent=2)


def _run_whale_follow():
    """Controleert nieuwe trades van gevolgde whales en kopieert automatisch."""
    from portfolio import load_portfolio as _lp, record_trade as _rt
    from datetime import datetime, timezone

    whales = _load_whale_list()
    if not whales:
        return

    state  = _load_follow_state()
    DATA_API = "https://data-api.polymarket.com"

    for whale in whales:
        addr     = whale["address"]
        name     = whale["name"]
        last_ts  = state.get(addr, 0)
        new_max_ts = last_ts

        try:
            r = requests.get(
                f"{DATA_API}/activity",
                params={"user": addr, "limit": 20},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            trades = r.json()
        except Exception:
            continue

        # Portfolio-type bepalen: expliciete voorkeur of fallback op note-keywords
        _portfolio_type = whale.get("portfolio", "")
        _note_lower = whale.get("note", "").lower()
        _CRYPTO_KWS = {"bitcoin", "btc", "crypto", "eth", "coin", "token"}
        _WEATHER_KWS = {"temperature", "weather", "celsius", "fahrenheit", "rainfall",
                        "temperatuur", "weer", "neerslag", "regen"}

        if not _portfolio_type:
            # Fallback: bepaal op basis van note
            if any(k in _note_lower for k in _WEATHER_KWS) and not any(k in _note_lower for k in _CRYPTO_KWS):
                _portfolio_type = "weather"
            else:
                _portfolio_type = "crypto"

        # Weather whales: aparte copy-logica met strengere filters
        if _portfolio_type == "weather":
            _weather_kw = {"temperature", "celsius", "fahrenheit", "temp"}
            _weather_filter = lambda title: any(k in title.lower() for k in _weather_kw)
            _copied_cids_w = set(state.get(f"{addr}_cids", []))

            new_weather_trades = sorted([
                t for t in trades
                if int(t.get("timestamp", 0)) > last_ts
                and t.get("side", "").upper() == "BUY"
                and float(t.get("usdcSize") or 0) >= 50.0          # geen test-orders
                and float(t.get("price") or 1.0) < 0.80            # niet al zeker ingeprijsd
                and _weather_filter(t.get("title", ""))
                and t.get("conditionId", "") not in _copied_cids_w
            ], key=lambda t: int(t.get("timestamp", 0)))

            if trades:
                new_max_ts = max(int(t.get("timestamp", 0)) for t in trades)

            from portfolio import load_portfolio as _lp_main, record_trade as _rt_main
            for t in new_weather_trades:
                p      = _lp_main()
                amount = round(p.cash * _WHALE_COPY_PCT, 2)
                if amount < 1.0:
                    break

                outcome = "YES" if t.get("outcomeIndex", 0) == 0 else "NO"
                price   = float(t.get("price") or 0.5)
                title   = t.get("title", "?")
                cid     = t.get("conditionId", "")

                market_id = ""
                if cid:
                    try:
                        gm = requests.get(
                            "https://gamma-api.polymarket.com/markets",
                            params={"conditionIds": cid}, timeout=6,
                        )
                        if gm.status_code == 200:
                            gdata = gm.json()
                            if gdata:
                                market_id = str(gdata[0].get("id", ""))
                    except Exception:
                        pass

                result = _rt_main(
                    question=title,
                    direction=outcome,
                    amount=amount,
                    entry_price=price,
                    model_prob=price,
                    gap=0.0,
                    condition_id=cid,
                    market_id=market_id,
                    note=f"[WHALE:{name}:HOOG]",
                )

                if "error" not in result:
                    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                    log_entry = {
                        "ts":      ts_str,
                        "whale":   name,
                        "outcome": outcome,
                        "amount":  amount,
                        "price":   round(price, 4),
                        "title":   title,
                    }
                    _whale_copy_log.insert(0, log_entry)
                    if len(_whale_copy_log) > 50:
                        _whale_copy_log.pop()
                    print(f"[WHALE-WEATHER] {name}: {outcome} ${amount:.0f} @ {price*100:.0f}% | {title[:55]}")
                    if cid:
                        _copied_cids_w.add(cid)
                        state[f"{addr}_cids"] = list(_copied_cids_w)

            state[addr] = new_max_ts
            continue

        # Alleen BTC/crypto markten kopiëren
        _title_filter = lambda title: any(k in title.lower() for k in _CRYPTO_KWS)

        # Al gekopieerde condition_ids ophalen voor deduplicatie
        _copied_cids = set(state.get(f"{addr}_cids", []))

        # Nieuwe BUY trades since last check, min size + specialiteit filter
        new_trades = sorted([
            t for t in trades
            if int(t.get("timestamp", 0)) > last_ts
            and t.get("side", "").upper() == "BUY"
            and float(t.get("usdcSize") or 0) >= _WHALE_MIN_COPY_SIZE
            and _title_filter(t.get("title", ""))
            and t.get("conditionId", "") not in _copied_cids
        ], key=lambda t: int(t.get("timestamp", 0)))

        # Update watermark
        if trades:
            new_max_ts = max(int(t.get("timestamp", 0)) for t in trades)

        for t in new_trades:
            p      = _lp(_WHALE_PORTFOLIO_FILE)
            amount = round(p.cash * _WHALE_COPY_PCT, 2)
            if amount < 1.0:
                break  # cash op

            outcome = "YES" if t.get("outcomeIndex", 0) == 0 else "NO"
            price   = float(t.get("price") or 0.5)
            title   = t.get("title", "?")
            cid     = t.get("conditionId", "")

            # Gamma market_id opzoeken voor live prijsupdates
            market_id = ""
            if cid:
                try:
                    gm = requests.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"conditionIds": cid},
                        timeout=6,
                    )
                    if gm.status_code == 200:
                        gdata = gm.json()
                        if gdata:
                            market_id = str(gdata[0].get("id", ""))
                except Exception:
                    pass

            result = _rt(
                question=title,
                direction=outcome,
                amount=amount,
                entry_price=price,
                model_prob=price,
                gap=0.0,
                condition_id=cid,
                market_id=market_id,
                note=f"[AUTO:{name}]",
                portfolio_file=_WHALE_PORTFOLIO_FILE,
            )

            if "error" not in result:
                ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                log_entry = {
                    "ts":      ts_str,
                    "whale":   name,
                    "outcome": outcome,
                    "amount":  amount,
                    "price":   round(price, 4),
                    "title":   title,
                }
                _whale_copy_log.insert(0, log_entry)
                if len(_whale_copy_log) > 50:
                    _whale_copy_log.pop()
                print(f"[WHALE-AUTO] {name}: {outcome} ${amount:.0f} @ {price*100:.0f}% | {title[:55]}")
                if cid:
                    _copied_cids.add(cid)
                    state[f"{addr}_cids"] = list(_copied_cids)

        state[addr] = new_max_ts

    _save_follow_state(state)


def _whale_follow_loop():
    import time
    # Eerste run direct maar sla trades over die al bestaan (init watermark)
    whales = _load_whale_list()
    if whales:
        state    = _load_follow_state()
        DATA_API = "https://data-api.polymarket.com"
        for whale in whales:
            if whale["address"] not in state:
                try:
                    r = requests.get(f"{DATA_API}/activity",
                                     params={"user": whale["address"], "limit": 5},
                                     timeout=8)
                    if r.status_code == 200 and r.json():
                        state[whale["address"]] = max(
                            int(t.get("timestamp", 0)) for t in r.json()
                        )
                except Exception:
                    pass
        _save_follow_state(state)
        print("[WHALE-AUTO] Auto-follow gestart — bestaande trades overgeslagen")

    while True:
        time.sleep(_WHALE_CHECK_SECS)
        try:
            _run_whale_follow()
        except Exception as e:
            print(f"[WHALE-AUTO] Fout: {e}")


def _load_whale_list():
    os.makedirs(os.path.dirname(_WHALES_FILE), exist_ok=True)
    if os.path.exists(_WHALES_FILE):
        try:
            with open(_WHALES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_whale_list(whales):
    os.makedirs(os.path.dirname(_WHALES_FILE), exist_ok=True)
    with open(_WHALES_FILE, "w") as f:
        json.dump(whales, f, indent=2)


_CRYPTO_KWS  = {"bitcoin", "btc", "crypto", "eth", "coin", "token"}
_WEATHER_KWS = {"temperature", "weather", "celsius", "fahrenheit", "rainfall",
                "temperatuur", "weer", "neerslag", "regen"}


def _infer_portfolio_type(whale: dict) -> str:
    """Bepaal portfolio-type op basis van note-keywords als niet expliciet ingesteld."""
    if whale.get("portfolio"):
        return whale["portfolio"]
    note = whale.get("note", "").lower()
    if any(k in note for k in _WEATHER_KWS) and not any(k in note for k in _CRYPTO_KWS):
        return "weather"
    return "crypto"


@app.route("/api/whale-portfolio/list")
def api_whale_list():
    """Geeft lijst van alle gevolgde whales, inclusief portfolio-type inference."""
    whales = _load_whale_list()
    for w in whales:
        if not w.get("portfolio"):
            w["portfolio"] = _infer_portfolio_type(w)
    return jsonify(whales)


@app.route("/api/whale-portfolio/add", methods=["POST"])
def api_whale_portfolio_add():
    """Voeg een whale toe op adres + naam."""
    from flask import request as req
    try:
        body      = req.get_json()
        address   = body.get("address", "").strip().lower()
        name      = body.get("name", "").strip()
        note      = body.get("note", "").strip()
        portfolio = body.get("portfolio", "crypto")  # "crypto" of "weather"

        if not address or len(address) != 42 or not address.startswith("0x"):
            return jsonify({"ok": False, "error": "Ongeldig wallet adres (verwacht 0x...42 chars)"})
        if not name:
            return jsonify({"ok": False, "error": "Naam verplicht"})

        whales = _load_whale_list()
        if any(w["address"].lower() == address for w in whales):
            return jsonify({"ok": False, "error": "Whale al toegevoegd"})

        from datetime import datetime
        whales.append({"name": name, "address": address, "note": note,
                       "portfolio": portfolio,
                       "added_at": datetime.now().strftime("%Y-%m-%d")})
        _save_whale_list(whales)
        return jsonify({"ok": True, "name": name, "address": address})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/whale-portfolio/remove", methods=["POST"])
def api_whale_portfolio_remove():
    from flask import request as req
    try:
        body    = req.get_json()
        address = body.get("address", "").strip().lower()
        whales  = _load_whale_list()
        whales  = [w for w in whales if w["address"].lower() != address]
        _save_whale_list(whales)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/whale-portfolio/analyze/<address>")
def api_whale_analyze(address):
    """Volledige analyse van één whale wallet."""
    try:
        import requests as _req
        from collections import defaultdict
        DATA_API = "https://data-api.polymarket.com"

        def classify(title):
            t = (title or "").lower()
            if any(w in t for w in ["bitcoin","btc","eth","crypto","solana","doge","xrp","bnb"]): return "crypto"
            if any(w in t for w in ["temperature","weather","rain","snow","celsius","fahrenheit"]): return "weer"
            if any(w in t for w in ["president","election","trump","vote","democrat","republican"]): return "politiek"
            if any(w in t for w in ["nba","nfl","soccer","football","baseball","tennis","sport","game","match"]): return "sport"
            return "overig"

        # Activiteit
        r  = _req.get(f"{DATA_API}/activity", params={"user": address, "limit": 100}, timeout=12)
        trades = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []

        volumes = [float(t.get("usdcSize") or 0) for t in trades if float(t.get("usdcSize") or 0) > 0]
        buys    = [t for t in trades if t.get("side") == "BUY"]
        entries = [float(t.get("price") or 0) for t in buys if float(t.get("price") or 0) > 0]
        cats    = defaultdict(int)
        for t in trades: cats[classify(t.get("title",""))] += 1

        # Posities
        r2 = _req.get(f"{DATA_API}/positions", params={"user": address, "limit": 100, "sizeThreshold": 1}, timeout=12)
        positions = r2.json() if r2.status_code == 200 and isinstance(r2.json(), list) else []

        open_pos = [p for p in positions if 0.02 < float(p.get("curPrice") or 0) < 0.98]
        won_pos  = [p for p in positions if float(p.get("curPrice") or 0) >= 0.98]
        lost_pos = [p for p in positions if float(p.get("curPrice") or 0) <= 0.02]

        realized_pnl = sum(float(p.get("cashPnl") or 0) for p in won_pos + lost_pos)
        unrealized   = sum(float(p.get("cashPnl") or 0) for p in open_pos)
        open_value   = sum(float(p.get("currentValue") or 0) for p in open_pos)
        win_rate     = len(won_pos) / (len(won_pos) + len(lost_pos)) if (won_pos or lost_pos) else 0

        top_positions = sorted(open_pos, key=lambda x: float(x.get("currentValue") or 0), reverse=True)[:10]

        return jsonify({
            "address":        address,
            "trade_count":    len(trades),
            "total_volume":   round(sum(volumes), 2),
            "avg_trade_size": round(sum(volumes)/len(volumes), 2) if volumes else 0,
            "avg_entry_pct":  round(sum(entries)/len(entries)*100, 1) if entries else 0,
            "market_dist":    dict(cats),
            "dominant_market": max(cats, key=cats.get) if cats else "overig",
            "open_count":     len(open_pos),
            "won_count":      len(won_pos),
            "lost_count":     len(lost_pos),
            "win_rate":       round(win_rate * 100, 1),
            "realized_pnl":   round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized, 2),
            "open_value":     round(open_value, 2),
            "top_positions":  [
                {"title": p.get("title",""), "outcome": p.get("outcome",""),
                 "value": round(float(p.get("currentValue") or 0), 2),
                 "pnl":   round(float(p.get("cashPnl") or 0), 2),
                 "price": round(float(p.get("curPrice") or 0), 3)}
                for p in top_positions
            ],
            "recent_trades": [
                {"side": t.get("side",""), "title": t.get("title","")[:60],
                 "outcome": "YES" if t.get("outcomeIndex",0)==0 else "NO",
                 "size": round(float(t.get("usdcSize") or 0), 2),
                 "price": round(float(t.get("price") or 0), 3),
                 "timestamp": t.get("timestamp", "")}
                for t in trades[:15]
            ],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/whale-portfolio/feed")
def api_whale_feed():
    """Recente trades van alle gevolgde whales gecombineerd."""
    try:
        import requests as _req
        DATA_API = "https://data-api.polymarket.com"
        whales   = _load_whale_list()
        all_trades = []

        for w in whales:
            try:
                r = _req.get(f"{DATA_API}/activity",
                    params={"user": w["address"], "limit": 20}, timeout=10)
                if r.status_code != 200: continue
                for t in r.json():
                    size = float(t.get("usdcSize") or 0)
                    if size < 10: continue
                    all_trades.append({
                        "whale_name":  w["name"],
                        "whale_addr":  w["address"],
                        "side":        t.get("side",""),
                        "outcome":     "YES" if t.get("outcomeIndex",0)==0 else "NO",
                        "title":       t.get("title","")[:70],
                        "size":        round(size, 2),
                        "price":       round(float(t.get("price") or 0), 3),
                        "timestamp":   t.get("timestamp", 0),
                        "condition_id": t.get("conditionId",""),
                    })
            except Exception:
                continue

        all_trades.sort(key=lambda x: x["timestamp"], reverse=True)
        return jsonify(all_trades[:100])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/whale-portfolio/copy", methods=["POST"])
def api_whale_copy():
    """Registreer een whale copy trade in het aparte whale portfolio."""
    from flask import request as req
    try:
        from portfolio import record_trade as _record
        body       = req.get_json()
        question   = body.get("question","")
        direction  = body.get("direction","YES")  # YES of NO
        amount     = float(body.get("amount", 5))
        price      = float(body.get("price", 0.5))
        whale_name = body.get("whale_name","")
        note       = f"[WHALE:{whale_name}]"

        result = _record(
            question=question, direction=direction,
            amount=amount, entry_price=price,
            model_prob=price, gap=0.0,
            note=note,
            portfolio_file=_WHALE_PORTFOLIO_FILE,
        )
        return jsonify({"ok": "error" not in result, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/whale-portfolio/stats")
def api_whale_portfolio_stats():
    """P&L stats van het whale copy portfolio."""
    try:
        from portfolio import load_portfolio as _load, get_stats as _get_stats
        import os as _os

        if not _os.path.exists(_WHALE_PORTFOLIO_FILE):
            # Initialiseer leeg portfolio met $500 startkapitaal
            from portfolio import Portfolio, save_portfolio as _save
            from datetime import datetime, timezone
            p = Portfolio(starting_balance=500.0, cash=500.0)
            p.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            _save(p, portfolio_file=_WHALE_PORTFOLIO_FILE)

        p = _load(_WHALE_PORTFOLIO_FILE)
        open_pos   = p.open_positions
        closed_pos = p.closed_positions
        wins       = [c for c in closed_pos if c.status == "won"]
        losses     = [c for c in closed_pos if c.status in ("lost","sold")]

        all_pos = [pos.to_dict() for pos in
                   sorted(open_pos, key=lambda x: x.timestamp, reverse=True)] + \
                  [pos.to_dict() for pos in
                   sorted(closed_pos, key=lambda x: x.timestamp, reverse=True)]

        stats = {
            "starting_balance": p.starting_balance,
            "cash":             round(p.cash, 2),
            "open_value":       round(p.open_value, 2),
            "total_equity":     round(p.total_equity, 2),
            "realized_pnl":     round(p.realized_pnl, 3),
            "unrealized_pnl":   round(p.unrealized_pnl, 3),
            "total_pnl":        round(p.total_pnl, 3),
            "total_pnl_pct":    round(p.total_pnl / p.starting_balance * 100, 1),
            "trade_count":      p.trade_count,
            "open_positions":   len(open_pos),
            "closed_positions": len(closed_pos),
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate":         p.win_rate(),
            "positions":        all_pos,
        }
        metrics = _calc_portfolio_metrics(all_pos)
        stats.update(metrics)
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/whale-portfolio/history")
def api_whale_portfolio_history():
    """Equity curve data voor het whale portfolio."""
    import pathlib
    log_path = pathlib.Path(__file__).parent / "data" / "whale_portfolio_log.jsonl"
    points = []
    if log_path.exists():
        try:
            with open(log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("date") and entry.get("equity") is not None:
                        points.append({"ts": entry["date"], "equity": float(entry["equity"])})
        except Exception:
            pass
    # Voeg huidig punt toe als meest recente
    try:
        from portfolio import load_portfolio as _lp
        p = _lp(_WHALE_PORTFOLIO_FILE)
        from datetime import datetime, timezone
        points.append({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "equity": round(p.total_equity, 2)})
    except Exception:
        pass
    return jsonify({"points": points})


@app.route("/api/whale-portfolio/sell/<position_id>", methods=["POST"])
def api_whale_sell(position_id):
    """Verkoop een whale portfolio positie."""
    try:
        from portfolio import sell_position as _sell, load_portfolio as _lp, save_portfolio as _sp
        with open(_WHALE_PORTFOLIO_FILE) as f:
            import json as _j
            data = _j.load(f)
        from portfolio import Portfolio
        p = Portfolio(
            starting_balance=data.get("starting_balance", 500.0),
            cash=data.get("cash", 500.0),
            positions=data.get("positions", []),
            trade_count=data.get("trade_count", 0),
            created_at=data.get("created_at", ""),
        )
        from datetime import datetime, timezone
        for pos_dict in p.positions:
            if pos_dict["id"] == position_id and pos_dict["status"] == "open":
                from portfolio import Position
                pos = Position(**{k: v for k, v in pos_dict.items() if k != "unrealized_pnl"})
                price = pos.current_price
                proceeds = round(pos.shares * price, 3)
                pnl = round(proceeds - pos.amount, 3)
                pos_dict.update({
                    "status": "sold",
                    "exit_price": round(price, 4),
                    "pnl": pnl,
                    "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "current_price": round(price, 4),
                })
                p.cash += proceeds
                from portfolio import save_portfolio as _save
                _save(p, portfolio_file=_WHALE_PORTFOLIO_FILE)
                return jsonify({"ok": True, "pnl": pnl})
        return jsonify({"ok": False, "error": "Positie niet gevonden"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/whale-portfolio/update_prices", methods=["POST"])
def api_whale_update_prices():
    """Update live prijzen voor open whale portfolio posities."""
    try:
        from portfolio import update_position_prices as _upp
        result = _upp(portfolio_file=_WHALE_PORTFOLIO_FILE)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/whale-portfolio/follow-status")
def api_whale_follow_status():
    """Status van de auto-follow thread + recente copies."""
    state = _load_follow_state()
    whales = _load_whale_list()
    last_checks = {}
    for w in whales:
        ts = state.get(w["address"], 0)
        if ts:
            from datetime import datetime, timezone
            last_checks[w["name"]] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        else:
            last_checks[w["name"]] = "—"
    return jsonify({
        "active":        True,
        "interval_min":  _WHALE_CHECK_SECS // 60,
        "copy_pct":      int(_WHALE_COPY_PCT * 100),
        "min_size":      _WHALE_MIN_COPY_SIZE,
        "last_checks":   last_checks,
        "recent_copies": _whale_copy_log[:20],
    })


@app.route("/api/btc-momentum/status")
def api_btc_momentum_status():
    """Status van de BTC momentum trader."""
    try:
        from btc_momentum import get_state as _btc_state
        return jsonify(_btc_state().to_dict())
    except Exception as e:
        return jsonify({"error": str(e), "running": False})


@app.route("/api/btc-momentum/toggle", methods=["POST"])
def api_btc_momentum_toggle():
    """Zet BTC momentum trader aan of uit."""
    try:
        from btc_momentum import start as _btc_start, stop as _btc_stop, get_state as _btc_state
        s = _btc_state()
        if s.running:
            _btc_stop()
            return jsonify({"running": False})
        else:
            dry = request.json.get("dry_run", True) if request.json else True
            _btc_start(dry_run=dry)
            return jsonify({"running": True, "dry_run": dry})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/hurricane")
def api_hurricane():
    try:
        from hurricane_scanner import (
            fetch_active_storms, fetch_hurricane_markets, seasonal_probability,
            parse_nhc_advisory, storm_hits_us, storm_makes_category,
            wind_to_category, MONTHLY_LANDFALL_PROB, NOAA_2026_OUTLOOK, MIN_GAP
        )
        import json as _json
        from datetime import datetime, timezone

        storms  = fetch_active_storms()
        markets = fetch_hurricane_markets()

        # Storm data met tracks
        storm_data = {}
        for storm in storms:
            name = storm.get("name", "").lower()
            wind = int(storm.get("intensity") or 0)
            track = parse_nhc_advisory(storm)
            storm_data[name] = {
                "wind_kts":  wind,
                "category":  wind_to_category(wind),
                "lat":       storm.get("latitudeNumeric"),
                "lon":       storm.get("longitudeNumeric"),
                "headline":  storm.get("headline", ""),
                "track":     track,
            }

        opportunities = []
        for market in markets:
            q = market.get("question", "")
            q_lower = q.lower()

            prices = market.get("outcomePrices", "[]")
            if isinstance(prices, str):
                try: prices = _json.loads(prices)
                except: continue
            poly_price = float(prices[0]) if prices else 0
            if not (0.01 < poly_price < 0.99):
                continue

            liq = float(market.get("liquidity") or 0)
            vol = float(market.get("volume24hr") or 0)
            model_prob = None
            basis = ""
            market_type = "seasonal"
            storm_info = ""

            result = seasonal_probability(q)
            if result:
                model_prob, basis = result

            if model_prob is None:
                for sname, sdata in storm_data.items():
                    if sname in q_lower:
                        track_pos = sdata["track"]["positions"] if sdata["track"] else []
                        if "category 5" in q_lower or "category 4" in q_lower:
                            min_cat = 5 if "category 5" in q_lower else 4
                            model_prob, basis = storm_makes_category(track_pos, min_cat)
                        elif "landfall" in q_lower:
                            model_prob, basis = storm_hits_us(track_pos)
                        else:
                            model_prob, basis = 0.5, "geen conditie herkend"
                        market_type = "active_storm"
                        storm_info = f"{sname.upper()} Cat{sdata['category']} {sdata['wind_kts']}kt"
                        break

            if model_prob is None:
                continue

            gap = model_prob - poly_price
            if abs(gap) >= MIN_GAP:
                label = "STERK" if abs(gap) >= 0.25 else "GOED" if abs(gap) >= 0.12 else "ZWAK"
                opportunities.append({
                    "question":    q,
                    "market_type": market_type,
                    "poly_price":  round(poly_price, 4),
                    "model_prob":  round(model_prob, 4),
                    "gap":         round(gap, 4),
                    "direction":   "BUY YES" if gap > 0 else "BUY NO",
                    "volume":      vol,
                    "liquidity":   liq,
                    "basis":       basis,
                    "storm_info":  storm_info,
                    "label":       label,
                })

        opportunities.sort(key=lambda o: abs(o["gap"]), reverse=True)
        month = datetime.now(timezone.utc).month
        return _json.dumps({
            "active_storms":     len(storms),
            "season_month_prob": MONTHLY_LANDFALL_PROB.get(month, 0),
            "outlook":           f"{NOAA_2026_OUTLOOK['named_storms_low']}-{NOAA_2026_OUTLOOK['named_storms_high']} named storms",
            "opportunities":     opportunities,
        }, default=str), 200, {"Content-Type": "application/json"}

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sports")
def api_sports():
    try:
        from sports_scanner import scan
        opportunities = scan()
        result = [
            {
                "question":      o.question,
                "outcome":       o.outcome,
                "poly_price":    o.poly_price,
                "book_price":    o.book_price,
                "gap":           round(o.gap, 4),
                "best_bookmaker": o.best_bookmaker,
                "poly_volume":   o.poly_volume,
                "sport":         o.sport,
            }
            for o in opportunities
        ]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trade", methods=["POST"])
def api_trade():
    from flask import request as req
    body = req.get_json()

    condition_id = body.get("conditionId")
    side         = body.get("side", "yes")
    amount       = float(body.get("amount", 10))

    pk         = os.getenv("PK")
    api_key    = os.getenv("CLOB_API_KEY")
    secret     = os.getenv("CLOB_SECRET")
    passphrase = os.getenv("CLOB_PASS_PHRASE")

    if not all([pk, api_key, secret, passphrase]):
        return jsonify({"ok": False, "error": ".env niet ingesteld — run setup_keys.py"}), 400

    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import MarketOrderArgs, ApiCreds
        from py_clob_client_v2.constants import POLYGON

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=pk,
            chain_id=POLYGON,
            creds=ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase),
        )

        clob_market = client.get_market(condition_id)
        tokens      = clob_market.get("tokens", [])
        token_id    = None

        for t in tokens:
            if side.lower() in t.get("outcome", "").lower():
                token_id = t["token_id"]
                break

        if not token_id:
            return jsonify({"ok": False, "error": "Token niet gevonden"}), 400

        order = client.create_market_order(MarketOrderArgs(token_id=token_id, amount=amount))
        resp  = client.post_order(order, orderType="FOK")
        return jsonify({"ok": True, "response": str(resp)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/flow-signals")
def api_flow_signals():
    """Geeft recente flow scanner signalen terug."""
    try:
        import flow_scanner as _fs_module
        scanner = getattr(_fs_module, '_flow_scanner', None)
        signals = scanner.recent_signals(50) if scanner else []
        last_scan = datetime.now(timezone.utc).strftime("%H:%M") if signals else "—"
        return jsonify({"signals": signals, "last_scan": last_scan})
    except Exception as e:
        return jsonify({"signals": [], "last_scan": "—", "error": str(e)})


if __name__ == "__main__":
    print("── Polymarket Dashboard ──────────────────")
    print("Open: http://localhost:5000")
    print("Stop: Ctrl+C")
    print("──────────────────────────────────────────")

    # Start auto trader achtergrond thread
    from auto_trader import start as start_auto_trader
    start_auto_trader()

    # Start Telegram bot command handler
    from telegram_bot import start as start_tg_bot
    start_tg_bot()

    # Start portfolio prijsupdate thread (elke 5 minuten)
    import threading
    def _price_updater():
        import time
        while True:
            time.sleep(300)
            try:
                from portfolio import update_position_prices
                update_position_prices()
            except Exception:
                pass
    threading.Thread(target=_price_updater, daemon=True, name="price-updater").start()

    # Start whale auto-follow thread
    threading.Thread(target=_whale_follow_loop, daemon=True, name="whale-follow").start()

    # Start BTC momentum trader
    try:
        from btc_momentum import start as _btc_start
        _btc_start(dry_run=True)
    except Exception as _e:
        print(f"[BTC momentum] start fout: {_e}")

    # Start flow scanner (smart money detector)
    try:
        import flow_scanner as _fs_module
        _fs_module._flow_scanner = _fs_module.FlowScanner()
        _fs_module._flow_scanner.start()
        print("[Flow scanner] gestart")
    except Exception as _e:
        print(f"[Flow scanner] start fout: {_e}")

    # Start weather scanner (achtergrond polling + alerts)
    try:
        import weather_scanner as _ws_module
        _ws_module._weather_scanner = _ws_module.WeatherScanner()
        _ws_module._weather_scanner.start()
        print("[Weather scanner] gestart")
    except Exception as _e:
        print(f"[Weather scanner] start fout: {_e}")

    app.run(debug=False, port=8080, host="0.0.0.0", threaded=True)
