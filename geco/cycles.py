"""Low and high cycles: buy in the hours (or weekday) an item is usually cheap, sell when it is
usually dear.

Many items repeat a daily rhythm (prices sag while one region sleeps and recover when it
plays) and some a weekly one. For each liquid item this finds the 3 hour block of the day
where the price usually sits lowest and the block where it sits highest, measured against
each day's own average so trends do not count. Weekly cycles do the same with weekdays on
daily history.

A pattern is only recommended if it would have worked on days it was not fitted on: the
windows are picked on older days, then the trade (a patient buy at the instant-sell price
during the low block, a sell at the instant-buy price in the next high block, after GE tax) is
replayed on the most recent days. It must make money there, win most of the time, and beat
the same trade started at random times of day (the spread alone pays something, so random
timing is the fair comparison). Everything here reads saved history only.

Hours and weekdays are UTC; the dashboard and panel show them in local time.
"""
import math
import threading
import time

BLOCK = 3
DAY = 86400
WEEK = 7 * DAY
_cache = {}
_lock = threading.Lock()


def _mid(r):
    v = [x for x in (r["ah"], r["al"]) if x]
    return sum(v) / len(v) if v else None


def _block_mean(rows_by_ts, start_ts, hours, key, need=2):
    vals = [rows_by_ts[t][key] for t in range(start_ts, start_ts + hours * 3600, 3600)
            if t in rows_by_ts and rows_by_ts[t][key]]
    return sum(vals) / len(vals) if len(vals) >= need else None


def _stats(xs):
    n = len(xs)
    if not n:
        return {"n": 0, "mean": None, "sd": None, "t": None}
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else None
    t = m / (sd / math.sqrt(n)) if sd else None
    return {"n": n, "mean": m, "sd": sd, "t": t}


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k - 1] + xs[k]) / 2


MIN_EDGE = 0.002  # timing must add at least 0.2% over random timing on unseen days
MIN_ROI = 0.005  # and the trade at today's prices must return at least 0.5% after tax


def _passes(te_ret, te_edge, tr_edge, out, min_n):
    """Made money after tax on unseen days, won most of them, and the timing itself helped
    (beat random start times there and on the days it was fitted on)."""
    return bool(te_ret["n"] >= min_n and (te_ret["mean"] or 0) > 0
                and (te_edge["mean"] or 0) >= MIN_EDGE and (te_edge["t"] or 0) >= 1.0
                and (tr_edge["mean"] or 0) > 0 and (out["test"]["winRate"] or 0) >= 0.6
                and out.get("profit", 0) > 0 and (out.get("roi") or 0) >= MIN_ROI)


# Daily cycle --------------------------------------------------------------------------------

def daily_profile(rows, days):
    """Average log deviation from the day's mean, per UTC hour, over the given days."""
    by_day = {}
    for r in rows:
        d = r["ts"] // DAY
        if d in days:
            m = _mid(r)
            if m:
                by_day.setdefault(d, []).append(((r["ts"] % DAY) // 3600, math.log(m)))
    acc = [[0.0, 0] for _ in range(24)]
    used = 0
    for pts in by_day.values():
        if len(pts) < 18:
            continue
        used += 1
        mean = sum(p[1] for p in pts) / len(pts)
        for h, lm in pts:
            acc[h][0] += lm - mean
            acc[h][1] += 1
    prof = [a[0] / a[1] if a[1] >= max(3, used * 0.6) else None for a in acc]
    return prof, used


def _blocks(prof):
    """Circular 3 hour block averages; None where any hour is missing."""
    out = []
    for s in range(24):
        v = [prof[(s + k) % 24] for k in range(BLOCK)]
        out.append(sum(v) / BLOCK if all(x is not None for x in v) else None)
    return out


def _trade(rows_by_ts, buy_ts, sell_ts, tax, iid, hours=BLOCK):
    buy = _block_mean(rows_by_ts, buy_ts, hours, "al")
    sell = _block_mean(rows_by_ts, sell_ts, hours, "ah")
    if not buy or not sell:
        return None
    return (sell - tax(int(sell), iid) - buy) / buy


def _replay_daily(rows_by_ts, days, low, gap, tax, iid):
    """Returns per day (cycle return, random timing return) for the days given."""
    out = []
    for d in sorted(days):
        r = _trade(rows_by_ts, d * DAY + low * 3600, d * DAY + (low + gap) * 3600, tax, iid)
        if r is None:
            continue
        ctrl = [_trade(rows_by_ts, d * DAY + h * 3600, d * DAY + (h + gap) * 3600, tax, iid) for h in range(24)]
        ctrl = [c for c in ctrl if c is not None]
        if len(ctrl) >= 12:
            out.append((r, sum(ctrl) / len(ctrl)))
    return out


def analyze_daily(rows, tax, iid, now=None, share=0.2, limit=None):
    """Daily low and high windows for one item, tested on the most recent days."""
    now = now or time.time()
    rows = [r for r in rows if r["ah"] and r["al"]]
    if len(rows) < 24 * 10:
        return {"ok": False, "reason": "needs at least 10 days of hourly history"}
    rows_by_ts = {r["ts"]: r for r in rows}
    full = sorted({r["ts"] // DAY for r in rows})
    counts = {}
    for r in rows:
        counts[r["ts"] // DAY] = counts.get(r["ts"] // DAY, 0) + 1
    days = [d for d in full if counts[d] >= 18]
    if len(days) < 10:
        return {"ok": False, "reason": "needs at least 10 full days of hourly history"}
    n_test = max(4, len(days) // 3)
    train, test = set(days[:-n_test]), set(days[-n_test:])
    prof, used = daily_profile(rows, train)
    blocks = _blocks(prof)
    if sum(b is not None for b in blocks) < 18:
        return {"ok": False, "reason": "too many missing hours"}
    low = min((s for s in range(24) if blocks[s] is not None), key=lambda s: blocks[s])
    high = max((s for s in range(24) if blocks[s] is not None), key=lambda s: blocks[s])
    gap = (high - low) % 24
    if gap < BLOCK:
        return {"ok": False, "reason": "no separate low and high windows"}
    tr = _replay_daily(rows_by_ts, train, low, gap, tax, iid)
    te = _replay_daily(rows_by_ts, test, low, gap, tax, iid)
    tr_edge = _stats([a - b for a, b in tr])
    te_edge = _stats([a - b for a, b in te])
    te_ret = _stats([a for a, _ in te])
    wins = sum(1 for a, _ in te if a > 0)
    # Target prices and volume from the last week.
    recent = [d for d in days if d >= days[-1] - 6]
    buys = [_block_mean(rows_by_ts, d * DAY + low * 3600, BLOCK, "al") for d in recent]
    sells = [_block_mean(rows_by_ts, d * DAY + (low + gap) * 3600, BLOCK, "ah") for d in recent]
    buy_at, sell_at = _median(buys), _median(sells)
    vol = _median([sum(rows_by_ts[t]["lv"] for t in range(d * DAY + low * 3600, d * DAY + (low + BLOCK) * 3600, 3600)
                       if t in rows_by_ts) for d in recent])
    out = {
        "ok": True, "kind": "daily", "low": low, "high": high, "gap": gap, "block": BLOCK,
        "profile": [None if p is None else round(p, 5) for p in prof], "amplitude": blocks[high] - blocks[low],
        "train": {"days": len(train), "edge": tr_edge["mean"]},
        "test": {"days": te_ret["n"], "ret": te_ret["mean"], "edge": te_edge["mean"], "t": te_edge["t"],
                 "wins": wins, "winRate": wins / te_ret["n"] if te_ret["n"] else None},
        "buyAt": round(buy_at) if buy_at else None, "sellAt": round(sell_at) if sell_at else None,
    }
    if buy_at and sell_at:
        out["profit"] = int(sell_at - tax(int(sell_at), iid) - buy_at)
        out["roi"] = out["profit"] / buy_at
        qty = int((vol or 0) * share)
        if limit:
            qty = min(qty, limit)
        out["qty"] = qty
        out["perCycle"] = qty * out["profit"]
    out["passed"] = _passes(te_ret, te_edge, tr_edge, out, 4)
    out["confidence"] = "strong" if out["passed"] and te_ret["n"] >= 7 and (te_edge["t"] or 0) >= 2 else "moderate"
    out.update(_timing_daily(low, low + gap, now))
    return out


def _timing_daily(low, high, now):
    h = (now % DAY) / 3600.0

    def until(start):
        d = (start - h) % 24
        return d * 3600

    def inside(start):
        return (h - start) % 24 < BLOCK
    return {"inBuy": inside(low % 24), "inSell": inside(high % 24),
            "nextBuyIn": 0 if inside(low % 24) else int(until(low % 24)),
            "nextSellIn": 0 if inside(high % 24) else int(until(high % 24))}


# Weekly cycle -------------------------------------------------------------------------------

def analyze_weekly(rows, tax, iid, now=None, share=0.2, limit=None):
    """Cheapest and dearest weekday (UTC days) for one item, from daily history."""
    now = now or time.time()
    rows = [r for r in rows if r["ah"] and r["al"]]
    by_day = {r["ts"] // DAY: r for r in rows}
    weeks = {}
    for d, r in by_day.items():
        m = _mid(r)
        if m:
            weeks.setdefault((d + 3) // 7, []).append(((d + 3) % 7, math.log(m)))  # day 0 = Monday
    full = sorted(w for w, pts in weeks.items() if len(pts) >= 6)
    if len(full) < 12:
        return {"ok": False, "reason": "needs about 12 weeks of daily history"}
    n_test = max(6, len(full) // 4)
    train, test = full[:-n_test], full[-n_test:]
    acc = [[0.0, 0] for _ in range(7)]
    for w in train:
        pts = weeks[w]
        mean = sum(p[1] for p in pts) / len(pts)
        for wd, lm in pts:
            acc[wd][0] += lm - mean
            acc[wd][1] += 1
    prof = [a[0] / a[1] if a[1] else None for a in acc]
    if any(p is None for p in prof):
        return {"ok": False, "reason": "missing weekdays"}
    low = min(range(7), key=lambda d: prof[d])
    high = max(range(7), key=lambda d: prof[d])
    gap = (high - low) % 7

    def trade(d0, g):
        b, s = by_day.get(d0), by_day.get(d0 + g)
        if not b or not s:
            return None
        return (s["ah"] - tax(int(s["ah"]), iid) - b["al"]) / b["al"]

    def replay(ws):
        out = []
        for w in ws:
            monday = w * 7 - 3
            r = trade(monday + low, gap)
            ctrl = [trade(monday + k, gap) for k in range(7)]
            ctrl = [c for c in ctrl if c is not None]
            if r is not None and len(ctrl) >= 4:
                out.append((r, sum(ctrl) / len(ctrl)))
        return out
    tr, te = replay(train), replay(test)
    tr_edge = _stats([a - b for a, b in tr])
    te_edge = _stats([a - b for a, b in te])
    te_ret = _stats([a for a, _ in te])
    wins = sum(1 for a, _ in te if a > 0)
    last = max(by_day)
    recent_mondays = [w * 7 - 3 for w in full[-4:]]
    buy_at = _median([(by_day.get(m + low) or {}).get("al") for m in recent_mondays])
    sell_at = _median([(by_day.get(m + low + gap) or {}).get("ah") for m in recent_mondays])
    vol = _median([(by_day.get(m + low) or {}).get("lv") for m in recent_mondays])
    out = {
        "ok": True, "kind": "weekly", "low": low, "high": high, "gap": gap,
        "profile": [round(p, 5) for p in prof], "amplitude": prof[high] - prof[low],
        "train": {"weeks": len(tr), "edge": tr_edge["mean"]},
        "test": {"weeks": te_ret["n"], "ret": te_ret["mean"], "edge": te_edge["mean"], "t": te_edge["t"],
                 "wins": wins, "winRate": wins / te_ret["n"] if te_ret["n"] else None},
        "buyAt": round(buy_at) if buy_at else None, "sellAt": round(sell_at) if sell_at else None,
        "lastDay": last * DAY,
    }
    if buy_at and sell_at:
        out["profit"] = int(sell_at - tax(int(sell_at), iid) - buy_at)
        out["roi"] = out["profit"] / buy_at
        qty = int((vol or 0) * share * 0.5)  # half a day's window: you will not sit there all day
        if limit:
            qty = min(qty, limit * 2)
        out["qty"] = qty
        out["perCycle"] = qty * out["profit"]
    out["passed"] = _passes(te_ret, te_edge, tr_edge, out, 6)
    out["confidence"] = "strong" if out["passed"] and te_ret["n"] >= 10 and (te_edge["t"] or 0) >= 2 else "moderate"
    today = (int(now // DAY) + 3) % 7
    out["inBuy"], out["inSell"] = today == low, today == (low + gap) % 7
    secs_today = now % DAY
    out["nextBuyIn"] = 0 if out["inBuy"] else int(((low - today) % 7) * DAY - secs_today)
    out["nextSellIn"] = 0 if out["inSell"] else int((((low + gap) % 7 - today) % 7) * DAY - secs_today)
    return out


# All items ----------------------------------------------------------------------------------

def _candidates(db, engine, n):
    with engine.lock:
        rows = list(engine.rows)
    liquid = [r for r in rows if r.get("high") and r.get("low") and (r.get("vol24") or 0) >= 2000]
    liquid.sort(key=lambda r: -(r.get("vol24") or 0) * r["high"])
    ids = [r["id"] for r in liquid[:n]]
    extra = [r["id"] for r in db.q("SELECT id FROM watchlist")]
    return list(dict.fromkeys(ids + extra))


def scan(db, engine, cfg, kind="daily", now=None, n=400, ttl=1800):
    """Every liquid item's cycle (cached for `ttl` seconds). Passing ones first, best first."""
    now = now or time.time()
    key = kind
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit["at"] < ttl:
            return hit["data"]
    ids = _candidates(db, engine, n)
    share = cfg.get("fill_share", 0.2)
    if kind == "daily":
        hist = db.history_many(ids, int(now - 30 * DAY))
        fn = analyze_daily
    else:
        hist = db.history_many(ids, int(now - 400 * DAY), table="d1")
        fn = analyze_weekly
    items, tested = [], 0
    for iid in ids:
        rows = hist.get(iid) or []
        m = engine.mapping.get(iid) or {}
        res = fn(rows, engine.tax, iid, now=now, share=share, limit=m.get("limit"))
        if not res.get("ok"):
            continue
        tested += 1
        if res["passed"]:
            live = engine.by_id.get(iid) or {}
            res.update(id=iid, name=m.get("name") or f"Item {iid}", icon=m.get("icon"),
                       liveLow=live.get("low"), liveHigh=live.get("high"))
            res.pop("profile", None)
            items.append(res)
    items.sort(key=lambda r: -(r.get("perCycle") or 0) * (1.5 if r["confidence"] == "strong" else 1))
    data = {"kind": kind, "computedAt": int(now), "tested": tested, "candidates": len(ids), "items": items}
    with _lock:
        _cache[key] = {"at": now, "data": data}
    return data


def refresh_timing(data, now=None):
    """Recomputes the 'now' fields of cached results (windows move with the clock)."""
    now = now or time.time()
    for r in data["items"]:
        if r["kind"] == "daily":
            r.update(_timing_daily(r["low"], r["low"] + r["gap"], now))
    return data


def item(db, engine, cfg, iid, now=None):
    """Both cycles for one item, with the profile, whether or not it passed."""
    now = now or time.time()
    m = engine.mapping.get(iid) or {}
    share = cfg.get("fill_share", 0.2)
    d = analyze_daily(db.history("h1", iid, now - 30 * DAY), engine.tax, iid, now=now, share=share, limit=m.get("limit"))
    w = analyze_weekly(db.history("d1", iid, now - 400 * DAY), engine.tax, iid, now=now, share=share, limit=m.get("limit"))
    return {"id": iid, "name": m.get("name"), "daily": d, "weekly": w}


def clear_cache():
    with _lock:
        _cache.clear()
