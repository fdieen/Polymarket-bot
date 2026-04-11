"""
Polymarket client wrapper — authenticatie en basis API calls.
"""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.constants import POLYGON

load_dotenv()


def get_client() -> ClobClient:
    pk = os.getenv("PK")
    api_key = os.getenv("CLOB_API_KEY")
    secret = os.getenv("CLOB_SECRET")
    passphrase = os.getenv("CLOB_PASS_PHRASE")

    if not all([pk, api_key, secret, passphrase]):
        raise EnvironmentError(
            "Ontbrekende .env variabelen. "
            "Run eerst: python setup_keys.py"
        )

    creds = ApiCreds(
        api_key=api_key,
        api_secret=secret,
        api_passphrase=passphrase,
    )

    return ClobClient(
        host="https://clob.polymarket.com",
        key=pk,
        chain_id=POLYGON,
        creds=creds,
    )
