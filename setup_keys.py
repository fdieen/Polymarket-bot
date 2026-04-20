"""
Eenmalig uitvoeren om je Polymarket API keys te genereren.
Zet daarna de output in je .env bestand.

Gebruik: python setup_keys.py
"""
import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.constants import POLYGON

load_dotenv()

pk = os.getenv("PK")
if not pk:
    print("Zet eerst je PK (private key) in .env")
    exit(1)

client = ClobClient(
    host="https://clob.polymarket.com",
    key=pk,
    chain_id=POLYGON,
)

creds = client.derive_api_key()

print("\n── Kopieer dit naar je .env ──────────────────")
print(f"CLOB_API_KEY={creds.api_key}")
print(f"CLOB_SECRET={creds.api_secret}")
print(f"CLOB_PASS_PHRASE={creds.api_passphrase}")
print("──────────────────────────────────────────────\n")
