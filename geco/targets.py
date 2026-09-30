"""Price targets and sell ladders.

A target is a price at which you want to act: sell some of a holding when the market pays
at least that much, or buy when it can be had for that little. A ladder splits a holding
into steps (for example a third at +5%, a third at +10%, a third at +15%) so you take some
profit early without selling everything at the first bump.

Buy targets respect the GE buy limit: the plan says how many limit windows (4 hours each)
the quantity needs. Hits notify you in the app and, if switched on, on your phone.
"""
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    tid INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, side TEXT NOT NULL, price INTEGER NOT NULL,
    qty INTEGER, note TEXT, created INTEGER, hit_at INTEGER, ladder TEXT);
"""


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


def ladder(qty, base, steps, side="sell"):
    """[(qty, price)] splitting qty evenly over steps like [0.05, 0.10, 0.15] from base."""
    steps = [s for s in steps if s is not None]
    if not steps or qty <= 0:
        return []
    each, extra = divmod(int(qty), len(steps))
    out = []
    for i, s in enumerate(steps):
        q = each + (1 if i < extra else 0)
        p = int(round(base * (1 + s))) if side == "sell" else int(round(base * (1 - s)))
        if q > 0:
            out.append((q, p))
    return out


def view(db, engine, now=None):
    now = now or time.time()
    out = []
    for t in db.q("SELECT * FROM targets ORDER BY hit_at IS NOT NULL, created DESC"):
        live = engine.by_id.get(t["item_id"]) or {}
        m = engine.mapping.get(t["item_id"]) or {}
        ref = live.get("high") if t["side"] == "sell" else live.get("low")
        dist = (t["price"] / ref - 1) if ref else None
        row = dict(t, name=m.get("name"), icon=m.get("icon"), market=ref, distance=dist)
        if t["side"] == "sell" and live.get("high"):
            row["netEach"] = t["price"] - engine.tax(t["price"], t["item_id"])
        if t["side"] == "buy" and m.get("limit") and t["qty"]:
            row["windows"] = -(-t["qty"] // m["limit"])
        out.append(row)
    return out


def check(db, engine, notify, now=None):
    """Mark targets the market has reached and notify once for each."""
    now = int(now or time.time())
    hits = 0
    for t in db.q("SELECT * FROM targets WHERE hit_at IS NULL"):
        live = engine.by_id.get(t["item_id"]) or {}
        name = (engine.mapping.get(t["item_id"]) or {}).get("name") or f"Item {t['item_id']}"
        if t["side"] == "sell" and live.get("high") and live["high"] >= t["price"]:
            msg = f"{name} reached your sell target {t['price']:,} (instant buys at {live['high']:,})"
        elif t["side"] == "buy" and live.get("low") and live["low"] <= t["price"]:
            msg = f"{name} reached your buy target {t['price']:,} (instant sells at {live['low']:,})"
        else:
            continue
        if t["qty"]:
            msg += f": {t['qty']:,} to {'sell' if t['side'] == 'sell' else 'buy'}"
        db.run("UPDATE targets SET hit_at=? WHERE tid=?", (now, t["tid"]))
        notify(msg, t["item_id"])
        hits += 1
    return hits
