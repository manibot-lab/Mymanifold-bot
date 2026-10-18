"""Create a Manifold market from a Polymarket link. Preview unless CREATE=true."""
import json, os, sys, time
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests

GAMMA = "https://gamma-api.polymarket.com"
MANIFOLD = "https://api.manifold.markets/v0"
STATE_FILE = "polymarket_markets.json"

def env_bool(name):
    return os.environ.get(name, "").strip().lower() == "true"

URL = os.environ["POLYMARKET_URL"].strip()
CREATE = env_bool("CREATE")
MAX_ANSWERS = int(os.environ.get("MAX_ANSWERS") or 10)
TIER = int(os.environ.get("LIQUIDITY_TIER") or 100)
ALLOW_DUP = env_bool("ALLOW_DUPLICATE")
INCLUDE_OTHER = env_bool("INCLUDE_OTHER")
ALLOW_NEW = env_bool("ALLOW_NEW_ANSWERS")
SUM_RAW = os.environ.get("SUM_TO_ONE", "").strip().lower()
KEY = os.environ.get("MANIFOLD_KEY", "")

def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def yes_price(m):
    return float(json.loads(m["outcomePrices"])[0])

def end_ms(m):
    s = m.get("endDate") or m.get("endDateIso")
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) == 10:
        s += "T23:59:00+00:00"
    return int(datetime.fromisoformat(s).astimezone(timezone.utc).timestamp() * 1000)

def fail(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)

# ---- Parse link and load from Polymarket ----
parts = [p for p in urlparse(URL).path.split("/") if p]
if len(parts) < 2 or parts[0] not in ("event", "market"):
    fail("Link should look like polymarket.com/event/<slug> or /event/<slug>/<market-slug>")

event = None
if parts[0] == "market":
    arr = get(f"{GAMMA}/markets", slug=parts[1])
    if not arr:
        fail("Polymarket market not found")
    markets = [arr[0]]
else:
    arr = get(f"{GAMMA}/events", slug=parts[1])
    if not arr:
        fail("Polymarket event not found")
    event = arr[0]
    markets = event.get("markets", [])
    if len(parts) > 2:  # a specific market inside the event
        markets = [m for m in markets if m.get("slug") == parts[2]]
        if not markets:
            fail("Market slug not found inside that event")

markets = [m for m in markets if not m.get("closed") and m.get("active", True)]
if not markets:
    fail("No open markets found")

rules = (event or markets[0]).get("description") or markets[0].get("description") or ""
rules = rules[:3000]

# ---- Build the Manifold body ----
if len(markets) == 1:
    m = markets[0]
    if len(json.loads(m["outcomes"])) != 2:
        fail("Not a two-outcome market")
    title = m["question"]
    close = end_ms(m)
    prob = max(1, min(99, round(yes_price(m) * 100)))
    body = {"outcomeType": "BINARY", "initialProb": prob}
    summary = f"Binary, starts at {prob}%"
    table = f"Polymarket odds at creation: {prob}%"
else:
    markets.sort(key=yes_price, reverse=True)
    chosen = markets[:MAX_ANSWERS]
    title = (event or {}).get("title") or markets[0]["question"]
    close = max([c for c in (end_ms(m) for m in chosen) if c] or [0]) or None
    labels = [(m.get("groupItemTitle") or m["question"]).strip() for m in chosen]
    if len(set(labels)) != len(labels):
        fail("Duplicate answer labels in this event")
    if SUM_RAW in ("true", "false"):
        sum_one = SUM_RAW == "true"
    else:  # use Polymarket's own setting (neg-risk = only one can win)
        sum_one = bool((event or {}).get("negRisk"))
    answers = labels + (["Other"] if INCLUDE_OTHER else [])
    body = {
        "outcomeType": "MULTIPLE_CHOICE",
        "answers": answers,
        "shouldAnswersSumToOne": sum_one,
        "addAnswersMode": "ANYONE" if ALLOW_NEW else "DISABLED",
    }
    table = "Polymarket odds at creation:\n" + "\n".join(
        f"- {l}: {yes_price(m)*100:.0f}%" for l, m in zip(labels, chosen))
    summary = f"Multi-answer ({len(answers)} answers, sum_to_one={sum_one}), starts evenly split"

title = title[:120]
if not close or close < int(time.time() * 1000) + 3600_000:
    fail("Missing or past end date")

desc = f"{rules}\n\n{table}\n\nSource: [Polymarket]({URL}). Resolves per the Polymarket rules above."
body.update({
    "question": title,
    "descriptionMarkdown": desc,
    "closeTime": close,
    "liquidityTier": TIER,
    "visibility": "public",
})

print(f"Title: {title}")
print(summary)
print(f"Liquidity tier: {TIER}")
print(table)

# ---- Duplicate check ----
if not ALLOW_DUP:
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else []
    if any(s["polymarket_url"] == URL for s in state):
        fail("Already created from this link (tick allow_duplicate to override)")
    found = requests.get(f"{MANIFOLD}/search-markets", params={"term": title, "limit": 10}, timeout=30).json()
    for f in found:
        if f.get("question", "").strip().lower() == title.strip().lower():
            fail(f"A market with this exact title exists: {f.get('url')}")

if not CREATE:
    print("\nPREVIEW ONLY. Nothing created. Re-run with 'create' ticked to make it.")
    sys.exit(0)

if not KEY:
    fail("MANIFOLD_KEY secret is missing")

r = requests.post(f"{MANIFOLD}/market", json=body,
                  headers={"Authorization": f"Key {KEY}", "Content-Type": "application/json"}, timeout=60)
if not r.ok:
    fail(f"Manifold said {r.status_code}: {r.text}")
made = r.json()
print(f"\nCreated: {made.get('url')}")

state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else []
state.append({
    "polymarket_url": URL,
    "manifold_url": made.get("url"),
    "title": title,
    "answers": body.get("answers"),
    "created_at": datetime.now(timezone.utc).isoformat(),
})
json.dump(state, open(STATE_FILE, "w"), indent=2)
