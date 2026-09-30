"""Wealth goals, and where your gp actually comes from.

Goals: a target net worth, or an item to afford (its target follows the live price). The
projection uses your own recent pace, split the same way as the net worth attribution:

    next day = today * (1 + daily return) + daily income

where the daily return is your time weighted return (price moves and trading) and the income
is loot and everything else (skilling, alching), both averaged over the last 30 days. A
cautious and a hopeful date use the return one standard deviation below and above.

Sources: gp per hour played for each way the account made money, using the play sessions
the RuneLite plugin saw. Trading keeps working while you are logged out (offers fill
offline), so it also shows per calendar day.
"""
import json
import math
import time

from . import account, networth

DAY = 86400
SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_goals (
    gid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, target INTEGER, item_id INTEGER,
    qty INTEGER DEFAULT 1, deadline INTEGER, created INTEGER);
"""


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


def pace(db, days=30, now=None):
    """Daily return, its day to day spread, and daily income, from recorded net worth."""
    now = now or time.time()
    rows = db.q("SELECT ts, total, loot, other, twr FROM account_worth WHERE acct='*' AND ts>=? ORDER BY ts",
                (int(now - days * DAY),))
    if len(rows) < 2 or rows[-1]["ts"] - rows[0]["ts"] < 6 * 3600:
        return None
    span = (rows[-1]["ts"] - rows[0]["ts"]) / DAY
    t0, t1 = rows[0]["twr"] or 1.0, rows[-1]["twr"] or 1.0
    daily = (t1 / t0) ** (1 / span) - 1 if t0 > 0 and t1 > 0 else 0.0
    income = ((rows[-1]["loot"] or 0) - (rows[0]["loot"] or 0) + (rows[-1]["other"] or 0) - (rows[0]["other"] or 0)) / span
    # Day to day spread of the return (daily buckets).
    by_day = {}
    for r in rows:
        by_day[r["ts"] // DAY] = r["twr"] or 1.0
    vals = [by_day[k] for k in sorted(by_day)]
    rets = [b / a - 1 for a, b in zip(vals, vals[1:]) if a]
    sd = math.sqrt(sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / (len(rets) - 1)) if len(rets) > 2 else None
    return {"daily": daily, "sd": sd, "income": income, "days": span, "start": rows[0]["total"], "now": rows[-1]["total"]}


def project(now_value, target, daily, income, max_days=3650):
    """Days until now_value reaches target at a daily return and daily income (None if never)."""
    if now_value >= target:
        return 0
    v = now_value
    for d in range(1, max_days + 1):
        v = v * (1 + daily) + income
        if v >= target:
            return d
        if v <= 0:
            return None
    return None


def goals(db, engine, cfg, now=None):
    now = now or time.time()
    total = networth.combined_view(db, engine, cfg)["total"]
    p = pace(db, now=now)
    out = []
    for g in db.q("SELECT * FROM wealth_goals ORDER BY gid"):
        target = g["target"]
        if g["item_id"]:
            live = engine.by_id.get(g["item_id"]) or {}
            price = live.get("high") or live.get("low")
            target = price * (g["qty"] or 1) if price else target
        row = dict(g, targetNow=target, current=total, progress=min(1.0, total / target) if target else None,
                   name=g["name"], itemName=(engine.mapping.get(g["item_id"]) or {}).get("name") if g["item_id"] else None,
                   icon=(engine.mapping.get(g["item_id"]) or {}).get("icon") if g["item_id"] else None)
        if target and p:
            mid = project(total, target, p["daily"], p["income"])
            lo = project(total, target, p["daily"] - (p["sd"] or 0), p["income"]) if p["sd"] else None
            hi = project(total, target, p["daily"] + (p["sd"] or 0), p["income"]) if p["sd"] else None
            row.update(days=mid, daysCautious=lo, daysHopeful=hi,
                       eta=now + mid * DAY if mid is not None else None)
            if g["deadline"] and g["deadline"] > now and target > total:
                d = (g["deadline"] - now) / DAY
                # Income per day needed on top of your current return to arrive on time.
                need = (target - total * (1 + p["daily"]) ** d) / max(1.0, d)
                row["neededPerDay"] = max(0.0, need)
        out.append(row)
    return {"goals": out, "pace": p, "total": total}


def sources(db, engine, cfg, days=30, now=None):
    """Where the account's gp came from over a period, per hour played and per day."""
    now = now or time.time()
    since = int(now - days * DAY)
    perf = networth.performance(db, None, days=days, now=now)
    hours = account.hours_played(db, since)
    parts = perf["parts"] if perf.get("ok") else {"mkt": 0, "trade": 0, "loot": 0, "other": 0}
    span = ((perf["to"] - perf["from"]) / DAY) if perf.get("ok") else days
    valuer = networth.Valuer(engine, cfg.get("networth_value", "sell"))
    by_src = {}
    for r in db.q("SELECT source, items FROM loot WHERE t>=?", (since,)):
        v = sum(q * valuer.each(i)[0] for i, q in json.loads(r["items"]))
        s = by_src.setdefault(r["source"] or "Other", {"source": r["source"] or "Other", "gp": 0.0, "kills": 0})
        s["gp"] += v
        s["kills"] += 1
    flips = db.q("SELECT qty, buy_price, sell_price, item_id FROM flips WHERE sell_ts>=? AND sell_price IS NOT NULL",
                 (since,))
    realized = sum(f["qty"] * (f["sell_price"] - engine.tax(f["sell_price"], f["item_id"]) - f["buy_price"])
                   for f in flips)
    # These four add up to the net worth change (the attribution); realized flip profit is shown
    # separately because a flip's result is partly its trading edge and partly market moves.
    rows = [
        {"key": "trade", "label": "Trading edge", "gp": parts["trade"],
         "hint": "Buying under and selling over what items were worth at the time"},
        {"key": "mkt", "label": "Market moves", "gp": parts["mkt"], "hint": "Price changes on what you held"},
        {"key": "loot", "label": "Loot", "gp": parts["loot"], "hint": "Drops recorded by the Loot Tracker"},
        {"key": "other", "label": "Skilling, alching and spending", "gp": parts["other"],
         "hint": "Everything else: skilling produce, alchs, supplies used, player trades, deaths"},
    ]
    for r in rows:
        r["perHour"] = r["gp"] / hours if hours else None
        r["perDay"] = r["gp"] / span if span else None
    return {"days": days, "hoursPlayed": hours, "span": span, "rows": rows, "realizedFlips": realized,
            "realizedPerHour": realized / hours if hours else None,
            "lootSources": sorted(by_src.values(), key=lambda s: -s["gp"])[:12],
            "note": "Per hour uses time logged in. Trading and market moves also happen while you are offline."}
