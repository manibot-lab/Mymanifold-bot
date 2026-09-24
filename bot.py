import os, json, requests

H = {"Authorization": f"Key {os.environ['MANIFOLD_KEY']}"}
MANIFOLD = "https://api.manifold.markets/v0"
MIN_GAP = 0.05    # only bet if odds differ by 5+ points
BET_AMOUNT = 10   # mana per bet, keep small at first

def polymarket(slug):
    r = requests.get("https://gamma-api.polymarket.com/markets",
                     params={"slug": slug}, timeout=30)
    r.raise_for_status()
    m = r.json()[0]
    prices = json.loads(m["outcomePrices"])
    outcomes = json.loads(m["outcomes"])
    i = outcomes.index("Yes") if "Yes" in outcomes else 0
    return float(prices[i])

def kalshi(ticker):
    r = requests.get(
        f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}",
        timeout=30)
    r.raise_for_status()
    m = r.json()["market"]
    bid, ask = m.get("yes_bid"), m.get("yes_ask")
    if bid and ask:
        return (bid + ask) / 200
    return m["last_price"] / 100

SOURCES = {"polymarket": polymarket, "kalshi": kalshi}

with open("markets.json") as f:
    entries = json.load(f)

for e in entries:
    try:
        target = SOURCES[e["source"]](e["id"])
        m = requests.get(f"{MANIFOLD}/slug/{e['manifold']}", timeout=30).json()
        if m.get("outcomeType") != "BINARY" or m.get("isResolved"):
            print("skip", e["manifold"])
            continue
        current = m["probability"]
        print(e["manifold"], "manifold", round(current, 3),
              "source", round(target, 3))
        if abs(target - current) < MIN_GAP:
            continue
        outcome = "YES" if target > current else "NO"
        limit = min(max(round(target, 2), 0.01), 0.99)
        r = requests.post(f"{MANIFOLD}/bet", headers=H, json={
            "contractId": m["id"],
            "amount": BET_AMOUNT,
            "outcome": outcome,
            "limitProb": limit,
            "expiresMillisDelta": 600000,
        }, timeout=30)
        print(r.status_code, r.text[:200])
    except Exception as err:
        print("error on", e, err)
