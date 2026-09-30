"""Guide prices, clue casket values and custom index categories.

Guide price: Jagex's official Grand Exchange price (what the in-game GE shows as the guide
and what item values on death and many interfaces use). It updates about once a day and
lags real trades. Fetched from Jagex's item database one item at a time when you open it
(there is no bulk endpoint), cached for 6 hours, with its last 180 days.

Clue caskets: from the Loot Tracker events the RuneLite plugin records, the value of every
casket you opened by tier, valued at today's prices.

Custom categories: your own baskets of items (for example "Barrows armour" or "My merch
items") that get a market index and a place on the heatmap like the built in classes.
"""
import json
import re
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS guide_prices (id INTEGER NOT NULL, ts INTEGER NOT NULL, price INTEGER,
    PRIMARY KEY (id, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS custom_categories (cid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    items TEXT NOT NULL, created INTEGER);
"""
CLUE_RE = re.compile(r"clue|casket", re.I)
TIERS = ["Beginner", "Easy", "Medium", "Hard", "Elite", "Master"]


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


# Guide prices -------------------------------------------------------------------------

def guide(db, client, iid, now=None):
    """{"now", "series": [{t, v}]} from cache, refreshed from Jagex when older than 6 hours."""
    now = now or time.time()
    fetched = db.kv_get(f"guide_at:{iid}") or 0
    if now - fetched > 6 * 3600 and hasattr(client, "guide_graph"):
        try:
            data = client.guide_graph(iid)
            rows = [(iid, int(int(k) / 1000), int(v)) for k, v in (data.get("daily") or {}).items()]
            if rows:
                db.many("INSERT OR REPLACE INTO guide_prices (id, ts, price) VALUES (?,?,?)", rows)
            db.kv_set(f"guide_at:{iid}", int(now))
        except Exception as e:  # keep working from the cache
            return dict(_cached(db, iid), error=str(e))
    return _cached(db, iid)


def _cached(db, iid):
    rows = db.q("SELECT ts, price FROM guide_prices WHERE id=? ORDER BY ts", (iid,))
    return {"now": rows[-1]["price"] if rows else None, "asOf": rows[-1]["ts"] if rows else None,
            "series": [{"t": r["ts"], "v": r["price"]} for r in rows]}


# Clue caskets -------------------------------------------------------------------------

def _tier(source):
    for t in TIERS:
        if t.lower() in (source or "").lower():
            return t
    return None


def clues(db, engine, valuer):
    by = {}
    for r in db.q("SELECT source, t, items FROM loot ORDER BY t"):
        if not CLUE_RE.search(r["source"] or ""):
            continue
        tier = _tier(r["source"]) or "Other"
        v = sum(q * valuer.each(i)[0] for i, q in json.loads(r["items"]))
        b = by.setdefault(tier, {"tier": tier, "caskets": 0, "values": [], "best": None})
        b["caskets"] += 1
        b["values"].append(v)
        items = json.loads(r["items"])
        top = max(items, key=lambda x: x[1] * valuer.each(x[0])[0]) if items else None
        if top and (b["best"] is None or v > b["best"]["value"]):
            b["best"] = {"value": v, "t": r["t"], "item": (engine.mapping.get(top[0]) or {}).get("name")}
    out = []
    for t in TIERS + ["Other"]:
        b = by.get(t)
        if not b:
            continue
        vals = sorted(b["values"])
        out.append({"tier": t, "caskets": b["caskets"], "total": sum(vals), "avg": sum(vals) / len(vals),
                    "median": vals[len(vals) // 2], "best": b["best"]})
    return out


# Custom categories --------------------------------------------------------------------

def custom_categories(db):
    return [dict(r, items=json.loads(r["items"])) for r in db.q("SELECT * FROM custom_categories ORDER BY cid")]


def add_category(db, name, items):
    name = (name or "").strip()[:40]
    ids = sorted({int(i) for i in items})
    if not name or len(ids) < 2:
        raise ValueError("give the category a name and at least two items")
    return db.run("INSERT INTO custom_categories (name, items, created) VALUES (?,?,?)",
                  (name, json.dumps(ids), int(time.time())))
