import os, json, time, math, unicodedata, requests

H = {"Authorization": f"Key {os.environ['MANIFOLD_KEY']}"}
MANIFOLD = "https://api.manifold.markets/v0"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"

# Defaults. Any entry in markets.json can override min_gap, max_gap,
# margin (in percentage points), bet, cap (in mana) and max_spread
# (in percentage points, Kalshi entries only).
MIN_GAP = 0.03             # only bet if odds differ by 5+ points
MAX_GAP = 1.0              # skip if odds differ by more than this
MARGIN = 0.025             # stop 2.5 points short of the source's odds
BET_AMOUNT = 25            # mana per bet
MAX_PER_MARKET = 250       # stop betting on a market/answer after this much
MIN_POLY_LIQUIDITY = 2000  # Polymarket liquidity in USD
MIN_KALSHI_VOLUME = 1000   # Kalshi contracts traded
MAX_KALSHI_SPREAD = 0.4   # skip if Kalshi bid/ask are wider than 15 points

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

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())

def pick(items, fields, wanted, label):
    w = norm(wanted)
    last = w.split()[-1] if w else ""
    tests = [
        lambda v: v == w,
        lambda v: w in v,
        lambda v: last in v.split(),
    ]
    for test in tests:
        hits = [it for it in items
                if any(test(norm(it.get(f))) for f in fields)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise Skip(f"several matches for '{wanted}'")
    options = ", ".join(str(label(it)) for it in items[:15])
    raise Skip(f"no match for '{wanted}'. Options: {options}")

def kalshi_price(m, name):
    v = num(m, name + "_dollars")
    if v is not None:
        return v
    v = num(m, name)  # older format, in cents
    return None if v is None else v / 100

def kalshi_odds(m):
    # Returns (bid, ask) as a tuple -- the real two-sided Kalshi quote.
    if m.get("status") not in ("open", "active"):
        raise Skip("Kalshi market not open")
    vol = num(m, "volume_fp", "volume") or 0
    if vol < MIN_KALSHI_VOLUME:
        raise Skip(f"low volume ({vol:.0f})")
    bid = kalshi_price(m, "yes_bid")
    ask = kalshi_price(m, "yes_ask")
    if not bid or not ask:
        raise Skip("no two-sided quotes")
    return bid, ask

def kalshi(ticker, answer):
    r = requests.get(f"{KALSHI}/markets/{ticker}", timeout=30)
    r.raise_for_status()
    return kalshi_odds(r.json()["market"])

def kalshi_event(event, answer):
    r = requests.get(f"{KALSHI}/markets",
                     params={"event_ticker": event, "limit": 200}, timeout=30)
    r.raise_for_status()
    ms = r.json().get("markets", [])
    if not ms:
        raise Skip("Kalshi event not found")
    m = pick(ms, ["yes_sub_title", "subtitle", "title"], answer,
             lambda x: x.get("yes_sub_title") or x.get("title"))
    print("  matched Kalshi:", m.get("ticker"), "-",
          m.get("yes_sub_title") or m.get("title"))
    return kalshi_odds(m)

def poly_odds(m):
    # Returns a single float -- Polymarket's gamma API only gives a
    # last-traded-style price here, not a real two-sided quote.
    if m.get("closed") or m.get("active") is False:
        raise Skip("Polymarket market closed")
    liq = num(m, "liquidityNum", "liquidity") or 0
    if liq < MIN_POLY_LIQUIDITY:
        raise Skip(f"low liquidity ({liq:.0f})")
    prices = json.loads(m["outcomePrices"])
    outcomes = json.loads(m["outcomes"])
    i = outcomes.index("Yes") if "Yes" in outcomes else 0
    return float(prices[i])

def polymarket(slug, answer):
    r = requests.get(f"{POLY}/markets", params={"slug": slug}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise Skip("Polymarket market not found")
    return poly_odds(data[0])

def polymarket_event(slug, answer):
    r = requests.get(f"{POLY}/events", params={"slug": slug}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise Skip("Polymarket event not found")
    ms = data[0].get("markets", [])
    m = pick(ms, ["groupItemTitle", "question"], answer,
             lambda x: x.get("groupItemTitle") or x.get("question"))
    print("  matched Polymarket:",
          m.get("groupItemTitle") or m.get("question"))
    return poly_odds(m)

SOURCES = {
    "kalshi": kalshi,
    "kalshi_event": kalshi_event,
    "polymarket": polymarket,
    "polymarket_event": polymarket_event,
}

ME = requests.get(f"{MANIFOLD}/me", headers=H, timeout=30).json()["id"]

def spent(contract_id, answer_id=None):
    r = requests.get(f"{MANIFOLD}/bets",
                     params={"userId": ME, "contractId": contract_id,
                             "limit": 1000}, timeout=30)
    r.raise_for_status()
    bets = r.json()
    if answer_id:
        bets = [b for b in bets if b.get("answerId") == answer_id]
    return sum(b.get("amount", 0) for b in bets)

def check(e, label):
    # per-entry overrides (gaps, margin and max_spread are in percentage
    # points)
    min_gap = e.get("min_gap", MIN_GAP * 100) / 100
    max_gap = e.get("max_gap", MAX_GAP * 100) / 100
    margin = e.get("margin", MARGIN * 100) / 100
    bet = e.get("bet", BET_AMOUNT)
    cap = e.get("cap", MAX_PER_MARKET)
    max_spread = e.get("max_spread", MAX_KALSHI_SPREAD * 100) / 100

    # "answer" = the answer's name on Manifold (multi-answer markets).
    # "source_answer" = the name to look for on Kalshi/Polymarket, if it
    # differs (or if the Manifold market is a plain yes/no).
    # "invert" = true means flip the source's odds (100% minus).
    answer = e.get("answer")
    src_answer = e.get("source_answer", answer)
    raw = SOURCES[e["source"]](e["id"], src_answer)

    two_sided = isinstance(raw, tuple)
    if two_sided:
        bid, ask = raw
        if e.get("invert"):
            bid, ask = 1 - ask, 1 - bid
        if (ask - bid) > max_spread + 1e-9:
            raise Skip(f"wide spread ({ask - bid:.2f})")
        mid = (bid + ask) / 2
    else:
        p = raw
        if e.get("invert"):
            p = 1 - p
        bid = ask = p
        mid = p

    m = requests.get(f"{MANIFOLD}/slug/{e['manifold']}", timeout=30).json()
    if m.get("isResolved"):
        raise Skip("Manifold market already resolved")
    answer_id = None
    if answer:
        if not m.get("answers"):
            raise Skip("Manifold market has no answers (is it yes/no?)")
        a = pick(m.get("answers", []), ["text"], answer,
                 lambda x: x.get("text"))
        if a.get("resolution"):
            raise Skip("Manifold answer already resolved")
        current = num(a, "probability", "prob")
        if current is None:
            raise Skip("could not read the Manifold answer's probability")
        answer_id = a["id"]
    else:
        if m.get("outcomeType") != "BINARY":
            raise Skip("Manifold market is not yes/no")
        current = m["probability"]

    if two_sided:
        print(label, "manifold", round(current, 3),
              "kalshi bid/ask", f"{bid:.3f}/{ask:.3f}", "mid", round(mid, 3))
    else:
        print(label, "manifold", round(current, 3), "source", round(mid, 3))

    gap = abs(mid - current)
    if gap < min_gap:
        return
    if gap > max_gap:
        print(f"  skip: gap is {gap * 100:.1f} points, over max_gap")
        return
    used = spent(m["id"], answer_id)
    if used + bet > cap:
        print("  cap reached:", round(used), "mana")
        return

    outcome = "YES" if mid > current else "NO"
    if outcome == "YES":
        # ceiling: how far up we'll bid = the conservative (bid) side
        raw_target = bid
    else:
        # floor: how far down we'll go. For a real two-sided Kalshi
        # quote, use the conservative complement of the ask (100% minus
        # yes_ask). For a single-number source (Polymarket), bid==ask,
        # so this is just that same number -- unchanged behaviour there.
        raw_target = (1 - ask) if two_sided else ask

    if outcome == "YES":
        limit = math.floor(round((raw_target - margin) * 100, 6)) / 100
        if limit <= current:
            return
    else:
        limit = math.ceil(round((raw_target + margin) * 100, 6)) / 100
        if limit >= current:
            return
    limit = min(max(limit, 0.01), 0.99)

    body = {"contractId": m["id"], "amount": bet, "outcome": outcome,
            "limitProb": limit,
            "expiresAt": int(time.time() * 1000) + 60000}
    if answer_id:
        body["answerId"] = answer_id
    r = requests.post(f"{MANIFOLD}/bet", headers=H, json=body, timeout=30)
    print("  bet", outcome, "HTTP", r.status_code, r.text[:150])
    if r.status_code == 200:
        res = r.json()
        bet_id = res.get("betId") or res.get("id")
        if bet_id and not res.get("isFilled"):
            c = requests.post(f"{MANIFOLD}/bet/cancel/{bet_id}",
                              headers=H, timeout=30)
            print("  cancelled leftover order, HTTP", c.status_code)

with open("markets.json") as f:
    entries = json.load(f)

for e in entries:
    label = e["manifold"] + (" / " + e["answer"] if e.get("answer") else "")
    try:
        check(e, label)
    except Skip as s:
        print("skip", label, "-", s)
    except Exception as err:
        print("error on", label, err)
