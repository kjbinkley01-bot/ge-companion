"""Your real fill rates, measured from your own GE offers.

The flip model assumes you win a fixed share (default 20%) of the volume on the side you
trade against: a patient buy fills against instant sells, a patient sell against instant
buys. With the RuneLite plugin every offer is logged from open to finish, so the real share
can be measured:

    share = quantity you got / market volume on that side while the offer was open

Only offers priced near the market count (within 2% of the average price on your side
while it was open): an offer priced over the market fills at once and one priced far under
it barely fills, and neither says anything about competition. Offers seen for the first
time at login (filled while the app was closed) have no reliable open time and are skipped.
The typical (median) share per item is used once an item has 3 measured offers, and your
overall median once there are 8; otherwise the setting in Settings is used.
"""
import time

MIN_ITEM = 3
MIN_ALL = 8
NEAR = 0.02


def _window_volume(rows, side, t0, t1):
    """Volume on one side and its average price between t0 and t1, from 5 minute or hourly rows."""
    vol = num = 0.0
    for r in rows:
        size = r["_size"]
        a, b = max(t0, r["ts"]), min(t1, r["ts"] + size)
        if b <= a:
            continue
        frac = (b - a) / size
        v = (r["lv"] if side == "buy" else r["hv"]) or 0
        p = r["al"] if side == "buy" else r["ah"]
        vol += v * frac
        if p:
            num += p * v * frac
    return vol, (num / vol if vol else None)


def measure(db, days=60, now=None):
    """Per offer measurements: [{item, side, share, near, ...}] for recent finished offers."""
    now = now or time.time()
    since = int(now - days * 86400)
    offers = db.q("SELECT * FROM ge_offer_log WHERE closed>=? AND done>0 AND caught_up=0 AND closed>opened",
                  (since,))
    if not offers:
        return []
    ids = sorted({o["item"] for o in offers})
    m5 = db.history_many(ids, since - 3600, table="m5")
    h1 = db.history_many(ids, since - 3600)
    out = []
    for o in offers:
        t0, t1 = o["opened"], o["closed"]
        if t1 - t0 < 120:
            continue  # filled at once: priced through the market
        rows = [dict(r, _size=300) for r in m5.get(o["item"], []) if t0 - 300 <= r["ts"] <= t1]
        if not rows:
            rows = [dict(r, _size=3600) for r in h1.get(o["item"], []) if t0 - 3600 <= r["ts"] <= t1]
        vol, avg = _window_volume(rows, o["side"], t0, t1)
        if not vol or not avg:
            continue
        gap = o["price"] / avg - 1
        near = abs(gap) <= NEAR
        out.append({"item": o["item"], "acct": o["acct"], "side": o["side"], "done": o["done"], "total": o["total"],
                    "vol": vol, "share": min(1.0, o["done"] / vol), "gap": gap, "near": near,
                    "hours": (t1 - t0) / 3600, "opened": t0, "closed": t1})
    return out


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def rates(db, days=60, now=None):
    """{"overall": share or None, "n": count, "items": {id: {"share", "n"}}}."""
    rows = [r for r in measure(db, days, now) if r["near"]]
    by_item = {}
    for r in rows:
        by_item.setdefault(r["item"], []).append(r["share"])
    items = {i: {"share": _median(v), "n": len(v)} for i, v in by_item.items() if len(v) >= MIN_ITEM}
    overall = _median([r["share"] for r in rows]) if len(rows) >= MIN_ALL else None
    return {"overall": overall, "n": len(rows), "items": items,
            "sides": {s: _median([r["share"] for r in rows if r["side"] == s]) for s in ("buy", "sell")}}


def share_for(rates_, default):
    """A function item -> (share, source) using measured rates where there is enough data."""
    items = (rates_ or {}).get("items") or {}
    overall = (rates_ or {}).get("overall")

    def pick(iid):
        it = items.get(iid)
        if it:
            return max(0.01, min(1.0, it["share"])), "item"
        if overall:
            return max(0.01, min(1.0, overall)), "you"
        return default, "setting"
    return pick
