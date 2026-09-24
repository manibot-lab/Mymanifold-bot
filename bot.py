import os, json, time, requests

H = {"Authorization": f"Key {os.environ['MANIFOLD_KEY']}"}
MANIFOLD = "https://api.manifold.markets/v0"

MIN_GAP = 0.05             # only bet if odds differ by 5+ points
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
    if vol 
