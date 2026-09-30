"""Risk and benchmark measures for an account, the way a brokerage reports them.

Everything reads saved daily history (`d1` imported from the Wiki, then `h1` days), never
the Wiki itself.

  market index   gp traded weighted daily index of the most traded items (no item more
                 than 5% of the weight), the benchmark for "how did the market do"
  volatility     standard deviation of daily moves
  value at risk  the loss on a bad day or week, from how today's holdings moved on past days
                 (historical simulation: no bell curve assumed)
  drawdown       the deepest fall from a peak for today's holdings over the past year
  beta           how much a holding tends to move when the market index moves 1%
  liquidity      days to sell a holding at your share of its daily instant-buy volume
"""
import math
import time

from . import analytics, networth

DAY = 86400


def _grid(series, days, now=None):
    """Align {id: [(t, price)]} on a continuous daily grid (forward filled)."""
    now = now or time.time()
    end = int(now) // DAY * DAY
    start = end - days * DAY
    grid = list(range(start, end + 1, DAY))
    out = {}
    for iid, pts in series.items():
        by_t = dict(pts)
        last = None
        # Seed with the last price before the window.
        for t, p in pts:
            if t < start:
                last = p
        vals = []
        for t in grid:
            last = by_t.get(t, last)
            vals.append(last)
        if sum(1 for v in vals if v) >= 10:
            out[iid] = vals
    return grid, out


def _rets(vals):
    """Daily simple returns, None where either day has no price."""
    return [None if not (a and b) else b / a - 1 for a, b in zip(vals, vals[1:])]


def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _cov(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 10:
        return None, None, None
    mx = sum(p[0] for p in pairs) / len(pairs)
    my = sum(p[1] for p in pairs) / len(pairs)
    cov = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) - 1)
    vx = sum((x - mx) ** 2 for x, _ in pairs) / (len(pairs) - 1)
    vy = sum((y - my) ** 2 for _, y in pairs) / (len(pairs) - 1)
    return cov, vx, vy


def _pctl(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(math.floor(q * (len(xs) - 1)))))
    return xs[k]


def _drawdown(levels):
    peak, worst, at = None, 0.0, None
    for i, v in enumerate(levels):
        if v is None:
            continue
        peak = v if peak is None else max(peak, v)
        dd = v / peak - 1
        if dd < worst:
            worst, at = dd, i
    return worst, at


# Market index ---------------------------------------------------------------------------

def market_index(db, engine, days=365, top=150, cap=0.05, now=None):
    """[{t, v}] daily market index (start = 100) and {t: daily return}."""
    def build():
        t_now = now or time.time()
        rows = db.q("SELECT id, SUM(COALESCE(ah * hv, 0) + COALESCE(al * lv, 0)) AS gp FROM d1 WHERE ts >= ? "
                    "GROUP BY id ORDER BY gp DESC LIMIT ?", (int(t_now - 30 * DAY), top))
        ids = [r["id"] for r in rows]
        gp = {r["id"]: r["gp"] or 0 for r in rows}
        if not ids:  # no daily import yet: use hourly history
            liquid = sorted(engine.rows, key=lambda r: -((r.get("vol24") or 0) * (r.get("high") or 0)))[:top]
            ids = [r["id"] for r in liquid]
            gp = {r["id"]: (r.get("vol24") or 0) * (r.get("high") or 0) for r in liquid}
        series = networth._daily_prices(db, engine, ids, days + 5)
        grid, vals = _grid(series, days, t_now)
        if not vals:
            return {"series": [], "rets": {}}
        keys = list(vals)
        weights = analytics.cap_weights([gp.get(i, 0) or 1 for i in keys], cap)
        rets = {k: _rets(vals[k]) for k in keys}
        level, out, daily = 100.0, [{"t": grid[0], "v": 100.0}], {}
        for d in range(len(grid) - 1):
            num = den = 0.0
            for w, k in zip(weights, keys):
                r = rets[k][d]
                if r is not None and abs(r) < 0.5:  # ignore broken prints
                    num += w * r
                    den += w
            r = num / den if den else 0.0
            level *= 1 + r
            daily[grid[d + 1]] = r
            out.append({"t": grid[d + 1], "v": round(level, 3)})
        return {"series": out, "rets": daily}
    return analytics.cached(("mkt-index", days, top, int((now or time.time()) // 3600)), 3600, build)


def benchmark(db, engine, since, now=None):
    """Market index return series from `since` (for comparing with a portfolio)."""
    now = now or time.time()
    days = max(2, int((now - since) / DAY) + 2)
    if days <= 30:
        # Short windows: the hourly market index is smoother than two or three daily points.
        idx = analytics.cached(("idx", days), 600,
                               lambda: analytics.indices(db, engine.mapping, engine.by_id, days))
        mk = next((c for c in idx["categories"] if c["key"] == "market"), None)
        pts = [p for p in (mk["series"] if mk else []) if p["t"] >= since]
    else:
        pts = [p for p in market_index(db, engine, min(days, 800), now=now)["series"] if p["t"] >= since - DAY]
    if len(pts) < 2:
        return []
    base = pts[0]["v"]
    return [{"t": p["t"], "v": p["v"] / base - 1} for p in pts]


# Account risk ---------------------------------------------------------------------------

def account_risk(db, engine, view, cfg, days=365, max_items=80):
    """Risk report for a net worth view (one account or all)."""
    total = view["total"] or 0
    held = [h for h in view["holdings"] if h["how"] not in ("cash", "untradeable") and h["value"] > 0][:max_items]
    if not total or not held:
        return {"ok": False, "reason": "Nothing held to measure yet."}
    series = networth._daily_prices(db, engine, [h["id"] for h in held], days + 5)
    grid, vals = _grid(series, days)
    mkt = market_index(db, engine, days)["rets"]
    m_rets = [mkt.get(t) for t in grid[1:]]
    n = len(grid) - 1
    port = [0.0] * n
    covered = 0.0
    item_rets = {}
    for h in held:
        v = vals.get(h["id"])
        if not v:
            continue
        r = _rets(v)
        item_rets[h["id"]] = r
        w = h["value"] / total
        covered += h["value"]
        for d in range(n):
            if r[d] is not None and abs(r[d]) < 0.5:
                port[d] += w * r[d]
    recent = port[-90:]
    vol_d = _std(recent)
    week = [sum(port[i:i + 7]) for i in range(0, max(0, n - 6))]
    var1 = _pctl(port, 0.05)
    var7 = _pctl(week, 0.05)
    worst5 = sorted(port)[:max(1, n // 20)]
    levels = [1.0]
    for r in port:
        levels.append(levels[-1] * (1 + r))
    dd, dd_at = _drawdown(levels)
    cov, vp, vm = _cov(port[-180:], m_rets[-180:])
    beta = cov / vm if cov is not None and vm else None
    corr = cov / math.sqrt(vp * vm) if cov is not None and vp and vm else None
    share = cfg.get("fill_share", 0.2)
    items = []
    for h in held:
        r = item_rets.get(h["id"])
        live = engine.by_id.get(h["id"]) or {}
        hv = live.get("hv24") or 0
        days_to_sell = h["qty"] / (share * hv) if hv else None
        row = {"id": h["id"], "name": h["name"], "icon": h.get("icon"), "value": h["value"],
               "weight": h["value"] / total, "daysToSell": days_to_sell, "category": h.get("category")}
        if r:
            icv, iv, im = _cov(r[-180:], m_rets[-180:])
            row["vol"] = _std(r[-90:])
            row["beta"] = icv / im if icv is not None and im else None
            row["corr"] = icv / math.sqrt(iv * im) if icv is not None and iv and im else None
            pc, _, pv = _cov(r[-180:], port[-180:])
            row["riskShare"] = (row["weight"] * pc / pv) if pc is not None and pv else None
            vv = [x for x in vals[h["id"]] if x]
            lvl = vals[h["id"]]
            row["drawdown"] = _drawdown(lvl)[0]
            row["low"], row["high"] = min(vv), max(vv)
            row["rangePos"] = ((vv[-1] - row["low"]) / (row["high"] - row["low"])) if row["high"] > row["low"] else None
        items.append(row)
    items.sort(key=lambda r: -(r.get("riskShare") or 0))
    weights = [h["value"] / total for h in view["holdings"] if h["value"] > 0]
    by_id = {r["id"]: r for r in items}

    def sellable(days_):
        return sum(h["value"] * min(1.0, days_ / by_id[h["id"]]["daysToSell"])
                   for h in held if by_id[h["id"]]["daysToSell"])
    liquid1, liquid7 = sellable(1.0), sellable(7.0)
    cash = view["cash"]
    best_i = max(range(n), key=lambda i: port[i]) if n else None
    worst_i = min(range(n), key=lambda i: port[i]) if n else None
    return {
        "ok": True, "days": days, "coverage": covered / (total - cash) if total > cash else 1.0,
        "volDaily": vol_d, "volMonthly": vol_d * math.sqrt(30) if vol_d is not None else None,
        "var1": -var1 * total if var1 is not None else None, "var1Pct": -var1 if var1 is not None else None,
        "var7": -var7 * total if var7 is not None else None, "var7Pct": -var7 if var7 is not None else None,
        "es1": -sum(worst5) / len(worst5) * total if worst5 else None,
        "maxDrawdown": dd, "drawdownAt": grid[dd_at] if dd_at is not None else None,
        "beta": beta, "corr": corr,
        "bestDay": {"t": grid[best_i + 1], "pct": port[best_i]} if best_i is not None else None,
        "worstDay": {"t": grid[worst_i + 1], "pct": port[worst_i]} if worst_i is not None else None,
        "effectiveHoldings": 1.0 / sum(w * w for w in weights) if weights else None,
        "liquid1": (liquid1 + cash) / total, "liquid7": (liquid7 + cash) / total,
        "items": items,
        "levels": [{"t": t, "v": v} for t, v in zip(grid, levels)],
    }
