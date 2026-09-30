"""Event study: how items moved around the posts that mentioned them.

For every saved post and every tradeable item it mentions, the item's price change is
measured over windows around the post day, minus the market index's change over the same
days (the "abnormal" move, so a market wide rise is not credited to the post):

  pre7   the 7 days before the post (people trading on leaks and expectations)
  d1     the day before to the day after
  d7     the day before to a week after
  d30    the day before to a month after

The same windows are measured on ordinary days for the same items, so the size of moves
around posts can be compared with normal (updates often bring bigger moves in either
direction even when the average direction is unclear). A t statistic says whether an
average is distinguishable from noise: below about 2 it is not.
"""
import json
import math
import random
import time

from . import networth, risk

DAY = 86400
WINDOWS = {"pre7": (-8, -1), "d1": (-1, 1), "d7": (-1, 7), "d30": (-1, 30)}
GROUPS = {"game": "Game updates (released)", "announce": "Announcements (blogs, polls, future updates)"}


def _stats(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return {"n": 0}
    xs_s = sorted(xs)
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "mean": mean, "median": xs_s[n // 2], "up": sum(1 for x in xs if x > 0) / n,
            "absMean": sum(abs(x) for x in xs) / n,
            "t": mean / (sd / math.sqrt(n)) if sd > 0 and n > 2 else None}


def _verdict(s):
    if not s.get("n") or s["n"] < 15 or s.get("t") is None:
        return "too few"
    if abs(s["t"]) >= 2:
        return "rises" if s["mean"] > 0 else "falls"
    return "no clear direction"


class Prices:
    """Daily prices and the market index on one continuous grid."""

    def __init__(self, db, engine, ids, days=800):
        series = networth._daily_prices(db, engine, ids, days + 40)
        self.grid, self.vals = risk._grid(series, days)
        self.pos = {t: i for i, t in enumerate(self.grid)}
        m = risk.market_index(db, engine, days)["series"]
        by_t = {p["t"]: p["v"] for p in m}
        last, self.mkt = None, []
        for t in self.grid:
            last = by_t.get(t, last)
            self.mkt.append(last)

    def abnormal(self, iid, t, a, b):
        v = self.vals.get(iid)
        i = self.pos.get(t // DAY * DAY)
        if v is None or i is None or i + a < 0 or i + b >= len(self.grid):
            return None
        p0, p1, m0, m1 = v[i + a], v[i + b], self.mkt[i + a], self.mkt[i + b]
        if not (p0 and p1 and m0 and m1):
            return None
        r = (p1 / p0 - 1) - (m1 / m0 - 1)
        return r if abs(r) < 1.5 else None  # a broken print, not a move


def study(db, engine, days=800, max_items_per_post=15):
    """Market wide results by post type and item category, plus a control on ordinary days."""
    posts = db.q("SELECT * FROM news WHERE t>=? ORDER BY t", (int(time.time() - days * DAY),))
    pairs = []
    for p in posts:
        ids = json.loads(p["items"] or "[]")[:max_items_per_post]
        group = "announce" if p["upcoming"] else ("game" if p["kind"] == "game" else None)
        if group:
            pairs.extend((p, iid, group) for iid in ids)
    ids = sorted({iid for _, iid, _ in pairs})
    if not ids:
        return {"ok": False, "reason": "No posts that mention tradeable items yet."}
    px = Prices(db, engine, ids, days)
    res = {g: {w: [] for w in WINDOWS} for g in GROUPS}
    by_cat = {}
    event_days = {}
    for p, iid, group in pairs:
        event_days.setdefault(iid, set()).add(p["t"] // DAY * DAY)
        name = (engine.mapping.get(iid) or {}).get("name") or ""
        cat = networth.category(name)
        for w, (a, b) in WINDOWS.items():
            r = px.abnormal(iid, p["t"], a, b)
            res[group][w].append(r)
            if group == "game":
                by_cat.setdefault(cat, {x: [] for x in WINDOWS})[w].append(r)
    # Control: the same items on random ordinary days (no post within two weeks).
    rng = random.Random(1)
    control = {w: [] for w in WINDOWS}
    for iid in px.vals:
        busy = event_days.get(iid, set())
        for _ in range(6):
            i = rng.randrange(40, max(41, len(px.grid) - 40))
            t = px.grid[i]
            if any(abs(t - e) < 14 * DAY for e in busy):
                continue
            for w, (a, b) in WINDOWS.items():
                control[w].append(px.abnormal(iid, t, a, b))
    out = {"ok": True, "posts": len(posts), "pairs": len(pairs), "items": len(px.vals),
           "from": px.grid[0] if px.grid else None, "groups": [], "categories": [],
           "control": {w: _stats(v) for w, v in control.items()}}
    for g, label in GROUPS.items():
        st = {w: _stats(v) for w, v in res[g].items()}
        out["groups"].append({"key": g, "label": label, "windows": st,
                              "verdict": {w: _verdict(s) for w, s in st.items()}})
    for cat, wins in by_cat.items():
        st = {w: _stats(v) for w, v in wins.items()}
        if st["d7"]["n"] >= 8:
            out["categories"].append({"category": cat, "windows": st, "verdict": _verdict(st["d7"])})
    out["categories"].sort(key=lambda c: -c["windows"]["d7"]["n"])
    return out


def item_history(db, engine, iid, days=800):
    """This item's moves around each post that mentioned it."""
    posts = [p for p in db.q("SELECT * FROM news WHERE t>=? ORDER BY t DESC", (int(time.time() - days * DAY),))
             if iid in json.loads(p["items"] or "[]")]
    if not posts:
        return {"events": [], "summary": {}}
    px = Prices(db, engine, [iid], days)
    events = []
    for p in posts:
        row = {"t": p["t"], "title": p["title"], "kind": p["kind"], "upcoming": bool(p["upcoming"]), "url": p["url"]}
        for w, (a, b) in WINDOWS.items():
            row[w] = px.abnormal(iid, p["t"], a, b)
        events.append(row)
    summary = {w: _stats([e[w] for e in events]) for w in WINDOWS}
    return {"events": events, "summary": summary}
