"""
Snelle order placer — gebruik dit als je een kans ziet.

Gebruik:
  python trade.py                        # interactieve modus
  python trade.py --market "Iran" --yes --amount 20
  python trade.py --market "Iran" --no  --amount 20
"""
import sys
import json
import requests
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, ApiCreds
from py_clob_client.constants import POLYGON

load_dotenv()

GAMMA_API = "https://gamma-api.polymarket.com/markets"


def get_client() -> ClobClient:
    pk         = os.getenv("PK")
    api_key    = os.getenv("CLOB_API_KEY")
    secret     = os.getenv("CLOB_SECRET")
    passphrase = os.getenv("CLOB_PASS_PHRASE")

    if not all([pk, api_key, secret, passphrase]):
        print("Stel eerst je .env in. Run: python setup_keys.py")
        sys.exit(1)

    return ClobClient(
        host="https://clob.polymarket.com",
        key=pk,
        chain_id=POLYGON,
        creds=ApiCreds(
            api_key=api_key,
            api_secret=secret,
            api_passphrase=passphrase,
        ),
    )


def search_market(keyword: str) -> list[dict]:
    r = requests.get(
        GAMMA_API,
        params={"limit": 100, "order": "volume24hr", "ascending": "false", "active": "true"},
        timeout=10,
    )
    markets = r.json()
    return [m for m in markets if keyword.lower() in m.get("question", "").lower()]


def get_token_id(condition_id: str, outcome: str, client: ClobClient) -> str | None:
    try:
        market = client.get_market(condition_id)
        tokens = market.get("tokens", []) if isinstance(market, dict) else []
        for t in tokens:
            if outcome.lower() in t.get("outcome", "").lower():
                return t["token_id"]
    except Exception as e:
        print(f"Fout bij ophalen token: {e}")
    return None


def place_market_order(client: ClobClient, token_id: str, side: str, amount: float) -> bool:
    """Plaatst een marktorder voor 'amount' USDC."""
    try:
        order = client.create_market_order(MarketOrderArgs(
            token_id=token_id,
            amount=amount,
        ))
        resp = client.post_order(order, orderType="FOK")
        print(f"\n✓ Order uitgevoerd: {side} ${amount} USDC")
        print(f"  Response: {resp}")
        return True
    except Exception as e:
        print(f"\nFout bij order: {e}")
        return False


def interactive_mode(client: ClobClient):
    print("\n── Polymarket Snelle Handel ────────────────────────────")
    print("Stap 1: Zoek een markt\n")

    keyword = input("Zoekterm (bijv. 'Iran', 'NBA', 'Bitcoin'): ").strip()
    if not keyword:
        return

    results = search_market(keyword)
    if not results:
        print(f"Geen markten gevonden voor '{keyword}'")
        return

    print(f"\n{len(results)} markt(en) gevonden:\n")
    for i, m in enumerate(results[:8], 1):
        vol = float(m.get("volume24hr") or 0)

        prices_raw = m.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            try:
                prices = json.loads(prices_raw)
                yes_price = float(prices[0]) if prices else 0
                no_price  = float(prices[1]) if len(prices) > 1 else 0
                price_str = f"YES={yes_price:.2f}  NO={no_price:.2f}"
            except Exception:
                price_str = "prijs onbekend"
        else:
            price_str = "prijs onbekend"

        print(f"  {i}. {m['question'][:60]}")
        print(f"     ${vol/1000:.0f}k vol/dag | {price_str}\n")

    choice = input("Kies markt (1-8): ").strip()
    try:
        idx = int(choice) - 1
        market = results[idx]
    except (ValueError, IndexError):
        print("Ongeldige keuze")
        return

    side = input("\nJe wilt kopen: (j)es of (n)o? ").strip().lower()
    outcome = "Yes" if side == "j" else "No"

    amount_str = input("Hoeveel USDC? ").strip()
    try:
        amount = float(amount_str)
    except ValueError:
        print("Ongeldig bedrag")
        return

    condition_id = market.get("conditionId")
    if not condition_id:
        print("Geen condition ID gevonden")
        return

    token_id = get_token_id(condition_id, outcome, client)
    if not token_id:
        print(f"Geen token gevonden voor {outcome}")
        return

    print(f"\nSamenvatting:")
    print(f"  Markt:   {market['question'][:60]}")
    print(f"  Koop:    {outcome}")
    print(f"  Bedrag:  ${amount} USDC")

    confirm = input("\nBevestigen? (ja/nee): ").strip().lower()
    if confirm == "ja":
        place_market_order(client, token_id, outcome, amount)
    else:
        print("Geannuleerd.")


def main():
    args = sys.argv[1:]
    client = get_client()

    # CLI modus
    if "--market" in args:
        idx     = args.index("--market")
        keyword = args[idx + 1] if idx + 1 < len(args) else ""
        side    = "Yes" if "--yes" in args else "No"
        amount  = float(args[args.index("--amount") + 1]) if "--amount" in args else 10.0

        results = search_market(keyword)
        if not results:
            print(f"Geen markt gevonden voor '{keyword}'")
            sys.exit(1)

        market       = results[0]
        condition_id = market.get("conditionId")
        token_id     = get_token_id(condition_id, side, client)

        if token_id:
            place_market_order(client, token_id, side, amount)
        else:
            print("Token niet gevonden")
        return

    # Interactieve modus
    interactive_mode(client)


if __name__ == "__main__":
    main()
