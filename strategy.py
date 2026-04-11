"""
Strategie: Value scanning op liquide Polymarket markten.

Logica:
1. Haal de meest liquide markten op via de Gamma API (gesorteerd op 24h volume)
2. Vergelijk de marktprijs met de orderbook bid/ask
3. Zoek markten waar de spread handelbaar is (3-15 cent)
4. Plaats limit orders net binnen de spread om hem te vangen
"""
import os
import requests
from dataclasses import dataclass
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
BUY  = "BUY"
SELL = "SELL"

GAMMA_API    = "https://gamma-api.polymarket.com/markets"
MIN_SPREAD   = float(os.getenv("MIN_SPREAD", "0.03"))
MAX_SPREAD   = float(os.getenv("MAX_SPREAD", "0.15"))   # Te grote spread = leeg boek
MIN_VOLUME   = float(os.getenv("MIN_VOLUME", "50000"))  # Min $50k volume per dag
MAX_POSITION = float(os.getenv("MAX_POSITION_SIZE", "10"))


@dataclass
class Opportunity:
    question: str
    outcome: str
    token_id: str
    best_bid: float
    best_ask: float
    spread: float
    our_buy: float
    our_sell: float
    volume_24h: float


def fetch_liquid_markets(limit: int = 50) -> list[dict]:
    """Haal liquide markten op via Gamma API, gesorteerd op 24h volume."""
    try:
        r = requests.get(
            GAMMA_API,
            params={"limit": limit, "order": "volume24hr", "ascending": "false", "active": "true"},
            timeout=10,
        )
        r.raise_for_status()
        markets = r.json()
        # Filter op voldoende volume
        return [m for m in markets if float(m.get("volume24hr") or 0) >= MIN_VOLUME]
    except Exception as e:
        print(f"  Gamma API fout: {e}")
        return []


def find_opportunities(client: ClobClient) -> list[Opportunity]:
    """Scant liquide markten en geeft handelbare kansen terug."""
    opportunities = []
    markets = fetch_liquid_markets()

    if not markets:
        return []

    for market in markets:
        condition_id = market.get("conditionId")
        question     = market.get("question", "?")
        volume_24h   = float(market.get("volume24hr") or 0)
        outcomes     = market.get("outcomes", "[]")
        prices_raw   = market.get("outcomePrices", "[]")

        # Outcomes en prijzen zijn soms JSON strings
        if isinstance(outcomes, str):
            import json
            try:
                outcomes = json.loads(outcomes)
                prices_raw = json.loads(prices_raw)
            except Exception:
                continue

        if not condition_id:
            continue

        # Haal CLOB markt op voor de token IDs
        try:
            clob_market = client.get_market(condition_id)
        except Exception:
            continue

        tokens = clob_market.get("tokens", []) if isinstance(clob_market, dict) else []

        for token in tokens:
            token_id = token.get("token_id")
            outcome  = token.get("outcome", "")

            if not token_id:
                continue

            try:
                ob = client.get_order_book(token_id)
            except Exception:
                continue

            bids = ob.bids or []
            asks = ob.asks or []

            if not bids or not asks:
                continue

            best_bid = float(bids[0].price)
            best_ask = float(asks[0].price)
            spread   = best_ask - best_bid

            # Alleen echte markten met zinvolle prijzen
            if not (0.03 <= best_bid <= 0.97):
                continue

            if MIN_SPREAD <= spread <= MAX_SPREAD:
                our_buy  = round(best_bid + 0.01, 2)
                our_sell = round(best_ask - 0.01, 2)

                if our_sell > our_buy:
                    opportunities.append(Opportunity(
                        question=question,
                        outcome=outcome,
                        token_id=token_id,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        spread=spread,
                        our_buy=our_buy,
                        our_sell=our_sell,
                        volume_24h=volume_24h,
                    ))

    # Sorteer op meeste volume (meeste kans op fill)
    return sorted(opportunities, key=lambda o: o.volume_24h, reverse=True)


def execute_opportunity(client: ClobClient, opp: Opportunity) -> bool:
    """Plaatst BUY en SELL limit orders voor een kans."""
    size = round(MAX_POSITION / opp.our_buy, 2)

    try:
        buy = client.create_order(OrderArgs(
            token_id=opp.token_id,
            price=opp.our_buy,
            size=size,
            side=BUY,
        ))
        client.post_order(buy)
        print(f"  ✓ BUY  {size} shares @ {opp.our_buy}")

        sell = client.create_order(OrderArgs(
            token_id=opp.token_id,
            price=opp.our_sell,
            size=size,
            side=SELL,
        ))
        client.post_order(sell)
        print(f"  ✓ SELL {size} shares @ {opp.our_sell}")
        return True

    except Exception as e:
        print(f"  Order fout: {e}")
        return False
