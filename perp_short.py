"""Add a small short to the Anthropic MNX perp on Manifold each time it runs.

Safety rules built in:
- skips if an earlier order is still pending (perp orders fill at the next oracle update)
- skips if you already hold a long (a short would queue a flip, not an add)
"""
import os
import random
import string
import sys

import requests

API = "https://api.manifold.markets/v0"
KEY = os.environ["MANIFOLD_API_KEY"]  # same secret name your main bot uses
HEADERS = {"Authorization": f"Key {KEY}", "Content-Type": "application/json"}

SLUG = "anthropic-mnx-valuation-futures-pri"
SHORT_MANA = 3189.41      # margin added per run
LEVERAGE = 10         # position notional = SHORT_MANA x this; must be <= the market max
MAX_FEE = 125           # fee allowance in mana, not a price limit


def get(path, **params):
    r = requests.get(f"{API}/{path}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


market = get(f"slug/{SLUG}")
contract_id = market["id"]
my_id = get("me")["id"]

order = get("get-perp-order", contractId=contract_id).get("order")
if order and order.get("status") == "pending":
    print("Earlier perp order still pending, skipping")
    sys.exit(0)

positions = get("get-perp-positions", contractId=contract_id, userId=my_id)
if any(p["direction"] == "long" for p in positions):
    print("Long position open, skipping (a short would flip it)")
    sys.exit(0)

key = "".join(random.choices(string.ascii_letters + string.digits, k=10))
resp = requests.post(
    f"{API}/place-perp-trade",
    headers=HEADERS,
    json={
        "contractId": contract_id,
        "direction": "short",
        "mana": SHORT_MANA,
        "leverage": LEVERAGE,
        "idempotencyKey": key,
        "maxFee": MAX_FEE,
    },
    timeout=30,
)
print(resp.status_code, resp.text)
resp.raise_for_status()
