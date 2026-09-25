import os, json, re, sys, time, requests
from datetime import datetime, timezone

H = {"Authorization": f"Key {os.environ['MANIFOLD_KEY']}"}
MANIFOLD = "https://api.manifold.markets/v0"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

def die(msg):
    print(msg)
    sys.exit(1)

def num(m, *names):
    for n in names:
        v = m.get(n)
        if v not in (None, ""):
            try:
                return float(v)
            except ValueError:
                pass
    return None

def price(m, name):
    v = num(m, name + "_dollars")
    if v is not None:
        return v
    v = num(m, name)
    return None if v is None else v / 100

def mid(m):
    bid, ask = price(m, "yes_bid"), price(m, "yes_ask")
    if bid and ask:
        return (bid + ask) / 2
    return price(m, "last_price")

def norm(s):
    return " ".join(str(s or "").lower().split())

def to_ms(s):
    try:
        return int(datetime.fromisoformat(
            str(s).replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None

url = os.environ.get("KALSHI_URL", "").strip()
create = os.environ.get("CREATE", "false").strip().lower() == "true"
try:
    liquidity_tier = float(os.environ.get("LIQUIDITY_TIER", "").strip() or "100")
    if liquidity_tier == int(liquidity_tier):
        liquidity_tier = int(liquidity_tier)
except ValueError:
    die("liquidity_tier must be a number, e.g. 100")
try:
    max_answers = max(2, min(int(os.environ.get("MAX_ANSWERS") or 10), 30))
except ValueError:
    max_answers = 10

me = requests.get(f"{MANIFOLD}/me", headers=H, timeout=30).json()
balance = me.get("balance", 0)
print(f"Bot account balance: {balance:.0f} mana")
print(f"Liquidity tier requested: {liquidity_tier}")

path = url.split("?")[0].split("#")[0].rstrip("/")
ticker = path.split("/")[-1].upper()
if not re.fullmatch(r"[A-Z0-9._-]+", ticker):
    die("Could not read a Kalshi ticker from that link.")

def get_event(t, depth=0):
    r = requests.get(f"{KALSHI}/events/{t}",
                     params={"with_nested_markets": "true"}, timeout=30)
    if r.status_code == 200:
        d = r.json()
        ev = d.get("event", d)
        ms = d.get("markets") or ev.get("markets") or []
        if not ms:
            r2 = requests.get(f"{KALSHI}/markets",
                              params={"event_ticker": t, "limit": 200},
                              timeout=30)
            if r2.status_code == 200:
                ms = r2.json().get("markets", [])
        return ev, ms
    if depth == 0:
        r3 = requests.get(f"{KALSHI}/markets/{t}", timeout=30)
        if r3.status_code == 200:
            return get_event(r3.json()["market"]["event_ticker"], 1)
    die(f"Kalshi has no event or market called {t} (HTTP {r.status_code}).")

ev, ms = get_event(ticker)
live = [m for m in ms if m.get("status") in ("active", "open", "initialized")]
if not live:
    die("That Kalshi event has no open markets.")
title = (ev.get("title") or live[0].get("title") or "").strip()
if not title:
    die("Could not read a title from Kalshi.")

multi = len(live) > 1
chosen = []
if multi:
        def uncertainty(m):
        p = mid(m)
        return 1.0 if p is None else abs(p - 0.5)  # unknowns go last
    live.sort(key=uncertainty)

    seen = set()
    for m in live:
        text = (m.get("yes_sub_title") or m.get("subtitle")
                or m.get("title") or "").strip()
        if not text or norm(text) in seen:
            continue
        seen.add(norm(text))
        chosen.append((text[:200], m))
        if len(chosen) >= max_answers:
            break
    if len(chosen) < 2:
        die("Not enough distinct answers to make a multi-answer market.")

r = requests.get(f"{MANIFOLD}/search-markets",
                 params={"term": title, "limit": 5}, timeout=30)
found = r.json() if r.status_code == 200 else []
for f in found:
    if norm(f.get("question")) == norm(title):
        die("A market with this exact title already exists: " + str(f.get("url")))

closes = [c for c in (to_ms(m.get("close_time")) for m in live
                      if m.get("close_time")) if c]
close_ms = max(closes) if closes else None
if close_ms and close_ms < time.time() * 1000 + 3600000:
    close_ms = None

rules = (live[0].get("rules_primary") or "").strip()[:1200]
desc = ("Mirrors this Kalshi market: " + url + "\n\n"
        "Resolves according to Kalshi's final result, using Kalshi's rules "
        "for this market.\n\n"
        + (("Kalshi's rules: " + rules + "\n\n") if rules else ""))

if multi:
    body = {"outcomeType": "MULTIPLE_CHOICE", "question": title,
            "answers": [t for t, _ in chosen],
            "shouldAnswersSumToOne": bool(ev.get("mutually_exclusive")),
            "addAnswersMode": "DISABLED",
            "visibility": "public",
            "liquidityTier": liquidity_tier,
            "descriptionMarkdown": desc}
else:
    p = mid(live[0])
    prob = 50 if p is None else min(max(round(p * 100), 1), 99)
    body = {"outcomeType": "BINARY", "question": title,
            "initialProb": prob, "visibility": "public",
            "liquidityTier": liquidity_tier,
            "descriptionMarkdown": desc}
if close_ms:
    body["closeTime"] = close_ms

print("Question:", title)
print("Type:", body["outcomeType"])
if multi:
    print("Answers add up to 100%:", body["shouldAnswersSumToOne"])
    for text, m in chosen:
        p = mid(m)
        print("  -", text, "| Kalshi", "n/a" if p is None else f"{p:.0%}",
              "|", m["ticker"])
else:
    print("Starting probability:", body["initialProb"], "| ticker",
          live[0]["ticker"])
if close_ms:
    print("Closes:", datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc),
          "UTC")
for f in found[:3]:
    print("Similar market already on Manifold:", f.get("url"))

if not create:
    print("PREVIEW ONLY. Nothing was created. Run again with the box "
          "ticked to create it.")
    sys.exit(0)

resp = None
last_text = ""
for _ in range(3):
    r = requests.post(f"{MANIFOLD}/market", headers=H, json=body, timeout=60)
    if r.status_code == 200:
        resp = r.json()
        break
    last_text = r.text
    print("Manifold said:", r.status_code, r.text[:400])
    km = re.search(r"Unrecognized key\(s\) in object: ([^\"]+)", r.text)
    removed = []
    if r.status_code == 400 and km:
        bad = re.findall(r"'([^']+)'", km.group(1))
        removed = [k for k in bad
                   if k in body and k not in ("outcomeType", "question")]
    if not removed:
        break
    for k in removed:
        body.pop(k)
    print("Retrying without:", removed)

if resp is None:
    if "insufficient" in last_text.lower() or "balance" in last_text.lower():
        die("Manifold rejected this for insufficient mana in the bot "
            "account. Add mana, or lower max_answers/liquidity_tier, "
            "and try again.")
    die("Could not create the market. See Manifold's message above.")

slug = resp.get("slug") or str(resp.get("url", "")).rstrip("/").split("/")[-1]
print("Created:", resp.get("url") or slug)

new = []
if multi:
    for text, m in chosen:
        new.append({"manifold": slug, "answer": text,
                    "source": "kalshi", "id": m["ticker"]})
else:
    new.append({"manifold": slug, "source": "kalshi",
                "id": live[0]["ticker"]})

with open("markets.json") as f:
    entries = json.load(f)
have = {(e.get("manifold"), e.get("answer")) for e in entries}
for e in new:
    if (e["manifold"], e.get("answer")) not in have:
        entries.append(e)
lines = ",\n".join("  " + json.dumps(e) for e in entries)
with open("markets.json", "w") as f:
    f.write("[\n" + lines + "\n]\n")
print("Saved to markets.json.")
