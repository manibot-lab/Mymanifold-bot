import os, json, time, requests

H = {"Authorization": f"Key {os.environ['MANIFOLD_KEY']}"}
MANIFOLD = "https://api.manifold.markets/v0"

MIN_GAP = 0.03             # only bet if odds differ by 3+ points
MARGIN = 0.025             # stop 2.5 points short of the source's odds
BET_AMOUNT = 25            # mana per bet
MAX_PER_MARKET = 250       # stop betting on a market after this much
MIN_POLY_LIQUIDITY = 2000  # Polymarket liquidity in USD
MIN_KALSHI_VOLUME = 1000   # Kalshi contracts traded
MAX_KALSHI_SPREAD = 0.05   # skip if bid/ask are wider than 5 points

class Skip(Exception):
    pass

def num(m, *names):
    for n in names:
        v = m.get(n)
        if v not in (None, ""):
            try:
                return float(v)
            except ValueError:
                pass
    return None

def kalshi_price(m, name):
    v = num(m, name + "_dollars")
    if v is not None:
        return v
    v = num(m, name)  # older format, in cents
    return None if v is None else v / 100

def polymarket(slug):
    r = requests.get("https://gamma-api.polymarket.com/markets",
                     params={"slug": slug}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise Skip("Polymarket market not found")
    m = data[0]
    if m.get("closed") or m.get("active") is False:
        raise Skip("Polymarket market closed")
    liq = num(m, "liquidityNum", "liquidity") or 0
    if liq < MIN_POLY_LIQUIDITY:
        raise Skip(f"low liquidity ({liq:.0f})")
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
    if m.get("status") not in ("open", "active"):
        raise Skip("Kalshi market not open")
    vol = num(m, "volume_fp", "volume") or 0
    if vol < MIN_KALSHI_VOLUME:
        raise Skip(f"low volume ({vol:.0f})")
    bid = kalshi_price(m, "yes_bid")
    ask = kalshi_price(m, "yes_ask")
    if not bid or not ask:
        raise Skip("no two-sided quotes")
    if ask - bid > MAX_KALSHI_SPREAD + 1e-9:
        raise Skip(f"wide spread ({ask - bid:.2f})")
    return (bid + ask) / 2

SOURCES = {"polymarket": polymarket, "kalshi": kalshi}

ME = requests.get(f"{MANIFOLD}/me", headers=H, timeout=30).json()["id"]

def spent(contract_id):
    r = requests.get(f"{MANIFOLD}/bets",
                     params={"userId": ME, "contractId": contract_id,
                             "limit": 1000}, timeout=30)
    r.raise_for_status()
    return sum(b.get("amount", 0) for b in r.json())

def check(e):
    target = SOURCES[e["source"]](e["id"])
    m = requests.get(f"{MANIFOLD}/slug/{e['manifold']}", timeout=30).json()
    if m.get("outcomeType") != "BINARY" or m.get("isResolved"):
        raise Skip("Manifold market not binary or already resolved")
    current = m["probability"]
    print(e["manifold"], "manifold", round(current, 3),
          "source", round(target, 3))
    if abs(target - current) < MIN_GAP:
        return
    used = spent(m["id"])
    if used + BET_AMOUNT > MAX_PER_MARKET:
        print("  cap reached:", round(used), "mana")
        return
    outcome = "YES" if target > current else "NO"
    if outcome == "YES":
        limit = target - MARGIN
        if limit <= current:
            return
    else:
        limit = target + MARGIN
        if limit >= current:
            return
    limit = min(max(round(limit, 3), 0.001), 0.999)
    r = requests.post(f"{MANIFOLD}/bet", headers=H, json={
        "contractId": m["id"], "amount": BET_AMOUNT, "outcome": outcome,
        "limitProb": limit,
        "expiresAt": int(time.time() * 1000) + 600000}, timeout=30)
    print("  bet", outcome, r.status_code, r.text[:150])

with open("markets.json") as f:
    entries = json.load(f)

for e in entries:
    try:
        check(e)
    except Skip as s:
        print("skip", e["manifold"], "-", s)
    except Exception as err:
        print("error on", e, err)
