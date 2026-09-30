"""Analytics over saved price history: timing, market indices, correlation and backtests.

Everything here reads the local `h1` table (hourly windows the app saves while it runs,
plus the startup backfill). Nothing calls the Wiki, so these stay cheap on the API.
"""
import math
import threading
import time

from . import categories
from .db import wprice

_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (now, val)
        if len(_cache) > 200:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
                _cache.pop(k, None)
    return val


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _mid(r):
    if r["ah"] is not None and r["al"] is not None:
        return (r["ah"] + r["al"]) / 2.0
    return wprice(r)


# Best time to trade ----------------------------------------------------------------

def seasonality(db, item_id, days=30, now=None):
    """Average price by weekday and hour, relative to each day's own average.

    Comparing each hour to the mean of its own calendar day removes the trend, so what
    is left is the repeating daily and weekly pattern. Hours use this computer's clock.
    """
    now = now or time.time()
    rows = db.history("h1", item_id, now - days * 86400)
    by_day = {}
    for r in rows:
        mid = _mid(r)
        if mid is None:
            continue
        lt = time.localtime(r["ts"])
        by_day.setdefault((lt.tm_year, lt.tm_yday), []).append((lt.tm_wday, lt.tm_hour, mid, r))
    grid = [[{"dev": 0.0, "vol": 0.0, "margin": 0.0, "n": 0, "mn": 0} for _ in range(24)] for _ in range(7)]
    hours = [{"dev": 0.0, "vol": 0.0, "margin": 0.0, "n": 0, "mn": 0} for _ in range(24)]
    used_days = 0
    for pts in by_day.values():
        if len(pts) < 6:
            continue  # a thin day would bias its few hours
        used_days += 1
        mean = sum(p[2] for p in pts) / len(pts)
        for wd, hr, mid, r in pts:
            dev = mid / mean - 1.0
            vol = r["hv"] + r["lv"]
            for cell in (grid[wd][hr], hours[hr]):
                cell["dev"] += dev
                cell["vol"] += vol
                cell["n"] += 1
                if r["ah"] is not None and r["al"] is not None:
                    cell["margin"] += r["ah"] - r["al"]
                    cell["mn"] += 1

    def fin(c):
        n = c["n"]
        return {"dev": c["dev"] / n if n else None, "vol": c["vol"] / n if n else None,
                "margin": c["margin"] / c["mn"] if c["mn"] else None, "n": n}

    grid = [[fin(c) for c in row] for row in grid]
    hours = [fin(c) for c in hours]
    ok = [(h, c) for h, c in enumerate(hours) if c["n"] >= 2 and c["dev"] is not None]
    best_buy = min(ok, key=lambda x: x[1]["dev"])[0] if ok else None
    best_sell = max(ok, key=lambda x: x[1]["dev"])[0] if ok else None
    spread = (hours[best_sell]["dev"] - hours[best_buy]["dev"]) if ok else None
    wide = [(h, c) for h, c in enumerate(hours) if c["n"] >= 2 and c["margin"] is not None]
    widest = max(wide, key=lambda x: x[1]["margin"])[0] if wide else None
    return {"id": item_id, "days": days, "daysUsed": used_days, "points": len(rows),
            "grid": grid, "hours": hours, "bestBuyHour": best_buy, "bestSellHour": best_sell,
            "spread": spread, "widestMarginHour": widest}


# Market and category indices -------------------------------------------------------

def _bucket_for(days):
    return 3600 if days <= 7 else 21600 if days <= 30 else 86400


def index_universe(mapping, rows_by_id):
    """Category key to item ids, including the dynamic market and big ticket baskets."""
    cats = categories.build(mapping)
    liquid = [r for r in rows_by_id.values() if r.get("vol24") and r.get("high") and r.get("low")]
    by_gp = sorted(liquid, key=lambda r: -(r["vol24"] * (r["high"] + r["low"]) / 2))
    cats["market"] = [r["id"] for r in by_gp[:100]]
    cats["bigticket"] = [r["id"] for r in liquid if r["high"] >= 10_000_000]
    return cats


def cap_weights(ws, share):
    """Scale down the biggest weights so none is more than `share` of the final total.

    The capped items all end up at the same weight c, which satisfies
    c = share * (k * c + rest) for k capped items. When there are too few items for
    the cap to be possible at all, everything is weighted equally.
    """
    n = len(ws)
    if n == 0:
        return []
    if n * share <= 1:
        return [1.0] * n
    order = sorted(range(n), key=lambda i: -ws[i])
    rest = float(sum(ws))
    if ws[order[0]] <= share * rest:
        return list(ws)
    for k in range(1, n):
        rest -= ws[order[k - 1]]
        if share * k >= 1:
            break
        c = share * rest / (1 - share * k)
        if ws[order[k - 1]] >= c and ws[order[k]] <= c:
            out = list(ws)
            for i in order[:k]:
                out[i] = c
            return out
    return [1.0] * n


def compute_index(series_by_id, grid, cap_share=0.2):
    """Value weighted price index (start = 100) over a fixed time grid.

    Each item is tracked as its price relative to its first price in the window,
    weighted by gp traded, with no single item allowed more than `cap_share` of the
    weight so one huge market cannot drown out the rest. Gaps are forward filled.
    """
    items = []
    early = grid[max(0, len(grid) // 4)] if grid else 0
    for iid, pts in series_by_id.items():
        prices = {}
        gp = 0.0
        for r in pts:
            if r["den"]:
                prices[r["bt"]] = r["num"] / r["den"]
                gp += r["num"]
        if not prices:
            continue
        first_t = min(prices)
        if first_t > early:
            continue  # joined too late in the window to have a fair base
        items.append((iid, prices, prices[first_t], gp))
    if not items:
        return [], []
    weights = cap_weights([w for *_, w in items], cap_share)
    wsum = sum(weights) or 1.0
    out = []
    last = [1.0] * len(items)
    for t in grid:
        acc = 0.0
        for k, (iid, prices, base, _) in enumerate(items):
            p = prices.get(t)
            if p is not None:
                last[k] = p / base
            acc += weights[k] * last[k]
        out.append({"t": t, "v": round(100.0 * acc / wsum, 3)})
    members = []
    for k, (iid, prices, base, _) in enumerate(items):
        members.append({"id": iid, "weight": weights[k] / wsum, "change": last[k] - 1.0})
    members.sort(key=lambda m: -m["weight"])
    return out, members


def indices(db, mapping, rows_by_id, days=7, now=None):
    now = now or time.time()
    b = _bucket_for(days)
    start = int((now - days * 86400) // b * b)
    end = int(now // b * b)
    grid = list(range(start, end + 1, b))
    cats = index_universe(mapping, rows_by_id)
    ids = set()
    for v in cats.values():
        ids.update(v)
    data = db.bucketed(ids, start, b)
    # Start the grid where saved history starts, so a young database still gets indices.
    first = min((pts[0]["bt"] for pts in data.values() if pts), default=None)
    if first is not None and first > start:
        grid = [t for t in grid if t >= first]
    out = []
    for key in ["market"] + [k for k, _ in categories.CATEGORIES] + ["bigticket"]:
        members = cats.get(key) or []
        series, mem = compute_index({i: data[i] for i in members if i in data}, grid)
        if not series:
            continue
        first = series[0]["v"]
        chg = series[-1]["v"] / first - 1 if first else None
        back = max(0, len(series) - 1 - int(86400 // b))
        chg24 = (series[-1]["v"] / series[back]["v"] - 1) if series[back]["v"] else None
        top = []
        for m in mem[:6]:
            mm = mapping.get(m["id"], {})
            top.append({"id": m["id"], "name": mm.get("name"), "icon": mm.get("icon"),
                        "weight": m["weight"], "change": m["change"]})
        out.append({"key": key, "label": categories.LABELS.get(key, key), "items": len(mem),
                    "series": series, "change": chg, "chg24": chg24, "top": top})
    return {"days": days, "bucket": b, "categories": out}


# Correlation -----------------------------------------------------------------------

def _returns(pts, grid):
    """Hourly log returns, only where the item actually traded in both hours."""
    prices = {r["bt"]: r["num"] / r["den"] for r in pts if r["den"]}
    out = {}
    for a, b in zip(grid, grid[1:]):
        pa, pb = prices.get(a), prices.get(b)
        if pa and pb:
            out[b] = math.log(pb / pa)
    return out


def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def correlated(db, mapping, rows_by_id, item_id, days=7, now=None, universe=300):
    """Items whose hourly price moves line up with this one (Pearson on log returns)."""
    now = now or time.time()
    start = int((now - days * 86400) // 3600 * 3600)
    grid = list(range(start, int(now // 3600 * 3600) + 1, 3600))
    liquid = [r for r in rows_by_id.values() if r.get("vol24") and r.get("high") and r.get("low")]
    by_gp = sorted(liquid, key=lambda r: -(r["vol24"] * (r["high"] + r["low"]) / 2))
    ids = {r["id"] for r in by_gp[:universe]}
    cat = categories.classify(mapping.get(item_id, {}).get("name", ""))
    if cat:
        ids.update(i for i, m in mapping.items() if categories.classify(m["name"]) == cat)
    ids.add(item_id)
    data = db.bucketed(ids, start, 3600)
    if item_id not in data:
        return {"id": item_id, "days": days, "points": 0, "similar": [], "opposite": []}
    base = _returns(data[item_id], grid)
    res = []
    for iid, pts in data.items():
        if iid == item_id:
            continue
        other = _returns(pts, grid)
        common = [t for t in base if t in other]
        if len(common) < 24:
            continue
        c = _pearson([base[t] for t in common], [other[t] for t in common])
        if c is None:
            continue
        m = mapping.get(iid, {})
        res.append({"id": iid, "name": m.get("name"), "icon": m.get("icon"), "corr": round(c, 3),
                    "n": len(common)})
    res.sort(key=lambda r: -r["corr"])
    return {"id": item_id, "days": days, "points": len(base),
            "similar": [r for r in res if r["corr"] > 0][:8],
            "opposite": sorted([r for r in res if r["corr"] < 0], key=lambda r: r["corr"])[:4]}


# Backtester ------------------------------------------------------------------------

STRATEGIES = {
    "dip": "Buy the dip: price falls a set % below its trailing average",
    "dump": "Buy dumps: sharp 1h drop on a volume spike",
    "margin": "Margin flip: last hour's average margin after tax beat a set ROI",
    "momentum": "Breakout: price rises a set % above its trailing high on strong volume",
    "rsi": "RSI oversold: RSI over the lookback falls below the threshold (e.g. 30)",
    "ma_cross": "Moving average cross: a fast average (a quarter of the lookback) crosses above the lookback average",
    "bollinger": "Bollinger band: price closes below the lower band (lookback average minus 2 standard deviations)",
}


def _series_rsi(vals, n):
    """RSI per position from a list with gaps (None); None until there is enough data."""
    out = [None] * len(vals)
    gain = loss = 0.0
    prev = None
    seen = 0
    for i, v in enumerate(vals):
        if v is None:
            continue
        if prev is not None:
            ch = v - prev
            g, lo = max(ch, 0.0), max(-ch, 0.0)
            seen += 1
            if seen <= n:
                gain += g / n
                loss += lo / n
            else:
                gain = (gain * (n - 1) + g) / n
                loss = (loss * (n - 1) + lo) / n
            if seen >= n:
                out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
        prev = v
    return out


def _series_sma(vals, n):
    """Trailing mean and standard deviation over the last n known values."""
    out_m, out_s = [None] * len(vals), [None] * len(vals)
    win = []
    for i, v in enumerate(vals):
        if v is None:
            continue
        win.append(v)
        if len(win) > n:
            win.pop(0)
        if len(win) == n:
            m = sum(win) / n
            out_m[i] = m
            out_s[i] = math.sqrt(sum((x - m) ** 2 for x in win) / n)
    return out_m, out_s


def backtest(db, mapping, rows_by_id, tax, params, now=None):
    """Replay a simple rule over saved hourly history.

    Entries fill at the next hour's average instant-sell price (a patient buy), exits
    at the average instant-buy price `hold` hours later minus tax (a patient sell). One
    open trade per item at a time. Size is the smaller of the buy limit and your share
    of the trailing day's instant-sell volume over 4 hours.
    """
    now = now or time.time()
    strat = params.get("strategy", "dip")
    if strat not in STRATEGIES:
        raise ValueError("unknown strategy")
    days = max(1, min(365, int(params.get("days", 30))))
    th = float(params.get("threshold", 5)) / 100.0
    hold = max(1, min(168, int(params.get("hold", 6))))
    look = max(3, min(168, int(params.get("lookback", 24))))
    vol_mult = float(params.get("volMult", 3))
    share = max(0.01, min(1.0, float(params.get("share", 0.2))))
    min_price = float(params.get("minPrice") or 0)
    max_price = float(params.get("maxPrice") or 0) or float("inf")
    min_vol = float(params.get("minVol") or 0)
    members = params.get("members", "all")
    max_items = max(10, min(1500, int(params.get("maxItems", 300))))
    only = {int(i) for i in params.get("ids") or []}

    cand = []
    for r in rows_by_id.values():
        if only and r["id"] not in only:
            continue
        price = r.get("high") or r.get("low")
        if not price or price < min_price or price > max_price or (r.get("vol24") or 0) < min_vol:
            continue
        if members == "f2p" and r.get("members"):
            continue
        if members == "p2p" and not r.get("members"):
            continue
        cand.append(r)
    cand.sort(key=lambda r: -((r.get("vol24") or 0) * (r.get("high") or 0)))
    cand = cand[:max_items]
    start = int((now - days * 86400) // 3600 * 3600)
    hist = db.history_many([r["id"] for r in cand], start - look * 3600)
    end = int(now // 3600 * 3600)
    grid = list(range(start - look * 3600, end + 1, 3600))
    pos = {t: i for i, t in enumerate(grid)}
    n = len(grid)
    trades = []
    baseline = []
    for r in cand:
        iid = r["id"]
        pts = hist.get(iid)
        if not pts or len(pts) < look // 2 + hold + 2:
            continue
        mid = [None] * n
        ah = [None] * n
        al = [None] * n
        vol = [0] * n
        lv = [0] * n
        for p in pts:
            i = pos.get(p["ts"])
            if i is None:
                continue
            mid[i] = _mid(p)
            ah[i], al[i] = p["ah"], p["al"]
            vol[i], lv[i] = p["hv"] + p["lv"], p["lv"]
        # Prefix sums for O(1) trailing averages.
        pn = [0.0] * (n + 1)
        pd = [0.0] * (n + 1)
        pv = [0.0] * (n + 1)
        pl = [0.0] * (n + 1)
        pc = [0] * (n + 1)
        for i in range(n):
            m = mid[i]
            pn[i + 1] = pn[i] + (m * vol[i] if m is not None else 0)
            pd[i + 1] = pd[i] + (vol[i] if m is not None else 0)
            pv[i + 1] = pv[i] + vol[i]
            pl[i + 1] = pl[i] + lv[i]
            pc[i + 1] = pc[i] + (1 if m is not None else 0)
        highs = [m if m is not None else float("-inf") for m in mid]
        firsts = [m for m in mid[look:] if m is not None]
        if len(firsts) >= 2:
            baseline.append(firsts[-1] / firsts[0] - 1)
        if strat == "rsi":
            ind_rsi = _series_rsi(mid, max(3, look))
        elif strat in ("ma_cross", "bollinger"):
            slow_m, slow_s = _series_sma(mid, look)
            fast_m, _ = _series_sma(mid, max(2, look // 4))
        limit = mapping.get(iid, {}).get("limit")
        busy_until = -1
        first_i = pos.get(start, look)
        for i in range(max(first_i, look), n - 1):
            if i <= busy_until or mid[i] is None:
                continue
            a = i - look
            cnt = pc[i] - pc[a]
            if cnt < look // 2:
                continue
            signal = False
            if strat == "dip":
                den = pd[i] - pd[a]
                mean = (pn[i] - pn[a]) / den if den else None
                signal = bool(mean and mid[i] <= mean * (1 - th))
            elif strat == "dump":
                prev = mid[i - 1]
                avgv = (pv[i] - pv[max(0, i - 24)]) / min(24, i)
                signal = bool(prev and mid[i] / prev - 1 <= -th and avgv > 0 and vol[i] >= vol_mult * avgv)
            elif strat == "margin":
                if ah[i] is not None and al[i]:
                    signal = (ah[i] - tax(ah[i], iid) - al[i]) / al[i] >= th
            elif strat == "momentum":
                hi = max(highs[a:i])
                avgv = (pv[i] - pv[a]) / look
                signal = bool(hi > float("-inf") and mid[i] >= hi * (1 + th) and avgv > 0
                              and vol[i] >= vol_mult * avgv)
            elif strat == "rsi":
                level = th * 100 if th < 1 else th
                signal = ind_rsi[i] is not None and ind_rsi[i] < level
            elif strat == "ma_cross":
                j = max((k for k in range(a, i) if fast_m[k] is not None and slow_m[k] is not None), default=None)
                signal = bool(j is not None and fast_m[i] is not None and slow_m[i] is not None
                              and fast_m[j] <= slow_m[j] and fast_m[i] > slow_m[i])
            elif strat == "bollinger":
                signal = bool(slow_m[i] is not None and slow_s[i] and mid[i] < slow_m[i] - 2 * slow_s[i])
            if not signal:
                continue
            e = i + 1
            buy = al[e]
            if not buy:
                continue
            x = None
            for j in range(e + hold, min(n, e + hold + 7)):
                if ah[j]:
                    x = j
                    break
            if x is None:
                continue
            sell_net = ah[x] - tax(ah[x], iid)
            day_lv = (pl[e] - pl[max(0, e - 24)]) * (24.0 / min(24, e)) if e else 0
            qty = max(1, int(min(limit or 10 ** 9, share * day_lv / 6.0)))
            ret = sell_net / buy - 1
            trades.append({"id": iid, "name": r["name"], "icon": r.get("icon"),
                           "entry": grid[e], "exit": grid[x], "buy": buy, "sell": ah[x],
                           "qty": qty, "ret": ret, "profit": int((sell_net - buy) * qty),
                           "hours": (grid[x] - grid[e]) / 3600})
            busy_until = x
    trades.sort(key=lambda t: t["exit"])
    equity, peak, dd, cum = [], 0, 0, 0
    for t in trades:
        cum += t["profit"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        equity.append({"t": t["exit"], "v": cum})
    rets = sorted(t["ret"] for t in trades)
    wins = [t for t in trades if t["profit"] > 0]
    gross_win = sum(t["profit"] for t in wins)
    gross_loss = -sum(t["profit"] for t in trades if t["profit"] < 0)
    by_item = {}
    for t in trades:
        b = by_item.setdefault(t["id"], {"id": t["id"], "name": t["name"], "icon": t["icon"],
                                         "trades": 0, "profit": 0, "wins": 0, "retSum": 0.0})
        b["trades"] += 1
        b["profit"] += t["profit"]
        b["wins"] += 1 if t["profit"] > 0 else 0
        b["retSum"] += t["ret"]
    items = sorted(by_item.values(), key=lambda b: -b["profit"])
    for b in items:
        b["avgRet"] = b.pop("retSum") / b["trades"]
        b["winRate"] = b["wins"] / b["trades"]
    # Histogram of returns in 1% steps, clipped to +/-15%.
    edges = list(range(-15, 16))
    hist_counts = {e: 0 for e in edges}
    for rr in rets:
        k = max(-15, min(15, int(math.floor(rr * 100))))
        hist_counts[k] += 1
    coverage = db.snapshot_count("h1", start)
    return {
        "strategy": strat, "label": STRATEGIES[strat], "days": days, "itemsTested": len(cand),
        "hoursOfData": coverage,
        "summary": {
            "trades": len(trades), "winRate": (len(wins) / len(trades)) if trades else None,
            "avgRet": (sum(rets) / len(rets)) if rets else None,
            "medianRet": rets[len(rets) // 2] if rets else None,
            "profit": cum, "maxDrawdown": dd,
            "profitFactor": (gross_win / gross_loss) if gross_loss else None,
            "avgHold": (sum(t["hours"] for t in trades) / len(trades)) if trades else None,
            "baselineRet": (sum(baseline) / len(baseline)) if baseline else None,
        },
        "equity": equity, "items": items[:25], "worst": items[-5:][::-1] if len(items) > 5 else [],
        "histogram": [{"pct": e, "n": hist_counts[e]} for e in edges],
        "trades": trades[-200:][::-1],
    }
