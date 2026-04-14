"""Scan alle kansen inclusief morgen."""
import json
from dotenv import load_dotenv
load_dotenv()
from weather_scanner import fetch_temperature_markets, parse_temperature_question, model_probability
from weather_sources import multi_source_forecast
from auto_trader import calculate_trade_size, execute_trade, state
from portfolio import record_trade
from datetime import datetime, timezone, timedelta, date as _date

# Polymarket fee (~2%) verrekend in effectieve gap-drempel
FEE_CORRECTION = 0.02

markets = fetch_temperature_markets()
now = datetime.now(timezone.utc)
min_date = (now + timedelta(days=1)).strftime('%Y-%m-%d')

candidates = []
for market in markets:
    q = market.get('question', '')
    parsed = parse_temperature_question(q)
    if not parsed: continue
    if parsed['date'] < min_date: continue
    prices = market.get('outcomePrices', '[]')
    if isinstance(prices, str):
        try: prices = json.loads(prices)
        except: continue
    poly_price = float(prices[0]) if prices else 0
    if not (0.01 < poly_price < 0.99): continue
    liq = float(market.get('liquidity') or 0)
    if liq < 300: continue
    candidates.append((market, parsed, poly_price))

city_date_pairs = list({(p['city'], p['date']) for _, p, _ in candidates})
forecasts = {}
for city, date in city_date_pairs:
    forecasts[(city, date)] = multi_source_forecast(city, date)

print(f"{'Gap':>6} | {'Dir':<8} | {'Poly':>5} | {'Model':>6} | {'Cond':<7} | n | d | Markt")
print("-"*105)
results = []
for market, parsed, poly_price in candidates:
    key = (parsed['city'], parsed['date'])
    fc = forecasts.get(key)
    if not fc or 'error' in fc: continue
    if fc.get('n_sources', 0) < 3: continue
    forecast_c = fc['consensus']
    spread = fc.get('spread', 2.0)
    if fc.get('spread_source') == 'ensemble': spread = spread / 2.56
    days = (_date.fromisoformat(parsed['date']) - _date.today()).days
    model_p = model_probability(forecast_c, parsed, spread=spread, days_ahead=days)
    gap = model_p - poly_price
    vol24 = float(market.get('volume24hr') or 0)
    if vol24 < 50: continue

    # 1. YES-bets op lage prijzen historisch verliesgevend
    if gap > 0 and poly_price < 0.20:
        continue

    # 2. Exact-match: hoge-variantie, vereist grotere gap (25%) en alleen NO-richting
    if parsed['condition'] == 'exact' and abs(gap) < 0.25:
        continue
    if parsed['condition'] == 'exact' and gap > 0:
        continue

    # 3. Sweet spot: 2-4 dagen vooruit krijgt bonuspunten in ranking
    #    1 dag = te weinig tijd, 5+ dagen = onzekerder forecast
    if days < 1:
        continue
    day_bonus = 0.05 if 2 <= days <= 4 else 0.0

    # 4. Above/below markten krijgen bonuspunten boven exact-match
    cond_bonus = 0.03 if parsed['condition'] in ('above', 'below') else 0.0

    # Effectieve gap na fees en bonussen voor sortering
    sort_key = abs(gap) - FEE_CORRECTION + day_bonus + cond_bonus

    results.append((sort_key, abs(gap), gap, market, parsed, poly_price, model_p, fc, days))

results.sort(reverse=True)
cfg = state.config
# 5. Effectieve min_gap na fees: verhoog drempel met 2%
effective_min_gap = cfg.min_gap + FEE_CORRECTION
print(f"Config: max=${cfg.max_trade} dry={cfg.dry_run} min_gap={cfg.min_gap*100:.0f}% (+{FEE_CORRECTION*100:.0f}% fees={effective_min_gap*100:.0f}% effectief)\n")
for rank, (_, abs_gap, gap, market, parsed, poly_price, model_p, fc, days) in enumerate(results[:15], 1):
    direction = "BUY YES" if gap > 0 else "BUY NO"
    flag = " ← TRADE" if abs_gap >= effective_min_gap else ""
    cond = parsed['condition'][:5]
    print(f"#{rank} {gap*100:+5.1f}% {direction:<8} poly={poly_price*100:.0f}% model={model_p*100:.0f}% {cond:<7} n={fc['n_sources']} d={days} | {market.get('question','')[:45]}{flag}")

# Alle kansen boven effectieve drempel plaatsen
from weather_scanner import WeatherOpportunity
from portfolio import load_portfolio

tradeable = [(gap, market, parsed, poly_price, model_p, fc)
             for _, abs_gap, gap, market, parsed, poly_price, model_p, fc, _ in results
             if abs_gap >= effective_min_gap]

if not tradeable:
    print("\nGeen kansen boven drempel op dit moment.")
else:
    import re as _re

    def get_open_questions():
        return {p['question'] for p in load_portfolio().positions if p.get('status') == 'open'}

    def get_open_condition_ids():
        """Geeft alle condition_ids van open posities — blokkeert tegengestelde positie op zelfde markt."""
        return {p['condition_id'] for p in load_portfolio().positions
                if p.get('status') == 'open' and p.get('condition_id', '')}

    def count_open_per_date():
        """Tel open posities per resolutiedatum."""
        counts = {}
        for p in load_portfolio().positions:
            if p.get('status') != 'open': continue
            m = _re.search(r'\d{4}-\d{2}-\d{2}', p.get('question', ''))
            if not m:
                m2 = _re.search(r'April (\d+)', p.get('question', ''))
                key = m2.group(0) if m2 else 'unknown'
            else:
                key = m.group(0)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def count_open_per_city():
        """Tel open posities per stad — max 2 per stad om concentratie te voorkomen."""
        counts = {}
        for p in load_portfolio().positions:
            if p.get('status') != 'open': continue
            city = p.get('question', '').lower()
            # Extraheer stad uit vraag (eerste stad-token na "in ")
            m = _re.search(r'temperature in ([a-z ]+?) be', city)
            key = m.group(1).strip() if m else 'unknown'
            counts[key] = counts.get(key, 0) + 1
        return counts

    MAX_PER_CITY = 2  # max posities per stad tegelijk

    open_questions = get_open_questions()
    print(f"\n{len(tradeable)} kansen boven drempel, al open: {len(open_questions & {m.get('question','') for _,m,*_ in tradeable})}")

    for gap, market, parsed, poly_price, model_p, fc in tradeable:
        q = market.get('question', '')
        # Herlaad elke iteratie
        open_questions = get_open_questions()
        if q in open_questions:
            print(f"  Skip (al open): {q[:60]}")
            continue

        # Blokkeer tegengestelde positie op zelfde markt
        cid = market.get('conditionId', '')
        open_cids = get_open_condition_ids()
        if cid and cid in open_cids:
            print(f"  Skip (al positie op markt): {q[:55]}")
            continue

        # Spreiding over datums
        date_counts = count_open_per_date()
        res_date = parsed['date']
        if date_counts.get(res_date, 0) >= cfg.max_per_date:
            print(f"  Skip (max {cfg.max_per_date}/datum voor {res_date}): {q[:50]}")
            continue

        # Spreiding over steden — max MAX_PER_CITY posities per stad
        city_counts = count_open_per_city()
        city_key = parsed['city'].lower()
        if city_counts.get(city_key, 0) >= MAX_PER_CITY:
            print(f"  Skip (max {MAX_PER_CITY}/stad voor {city_key}): {q[:50]}")
            continue

        opp = WeatherOpportunity(
            question=q,
            city=parsed['city'].title(),
            date=parsed['date'],
            condition=parsed['condition'],
            temp_low=parsed['temp_low'],
            temp_high=parsed['temp_high'],
            unit=parsed['unit'],
            poly_price=poly_price,
            forecast_temp=round(fc['consensus'], 1),
            model_prob=round(model_p, 3),
            gap=round(gap, 3),
            direction="BUY YES" if gap > 0 else "BUY NO",
            volume=float(market.get('volume24hr') or 0),
            condition_id=market.get('conditionId', ''),
            slug=market.get('slug', ''),
            market_id=str(market.get('id', '')),
        )
        amount = min(calculate_trade_size(opp, cfg), cfg.max_trade)
        success, note = execute_trade(opp, amount, dry_run=False)
        if success:
            outcome = "YES" if "YES" in opp.direction else "NO"
            price = opp.poly_price if outcome == "YES" else (1 - opp.poly_price)
            import re as _re2
            oid_match = _re2.search(r'GTC order geplaatst: (\S+)', note)
            order_id = oid_match.group(1) if oid_match else ""
            record_trade(question=opp.question, direction=outcome, amount=amount,
                         entry_price=price, model_prob=opp.model_prob, gap=opp.gap,
                         market_id=opp.market_id, condition_id=opp.condition_id,
                         order_id=order_id)
            fill_status = "order geplaatst, wacht op fill" if order_id else "direct gevuld"
            print(f"✓ {opp.direction} ${amount:.2f} @ {price*100:.0f}% | {fill_status} | {q[:50]}")
        else:
            print(f"✗ Mislukt: {note[:60]} | {q[:40]}")
