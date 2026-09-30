"""Holding outlook: do the trends support holding an item, and what is it likely worth later?

The model learns from the whole market's history rather than extrapolating one chart. For
every liquid item and every week in the imported daily history it records trend signals and
what the price did next, then fits a small ridge regression per horizon (7, 30 and 90 days):

  momentum        7, 30 and 90 day log price change
  vs averages     price against its 30 and 90 day averages
  range           where the price sits between its 180 day low and high
  volatility      30 day standard deviation of daily moves
  volume trend    last 7 days of volume against the last 60
  market          the typical item's 30 day change (the whole market's direction)

It is scored walk-forward: fitted on older weeks, tested on newer weeks it never saw, against
the plain "no change" guess. The likely range comes from the model's own errors on past data,
scaled by each item's volatility, so a quiet item gets a tight range and a wild one a wide one.
Everything reads saved history (`d1` and `h1`), never the Wiki.
"""
import json
import math
import os
import time

from . import forecast

DAY = 86400
HORIZONS = (7, 30, 90)
FEATURES = ["r7", "r30", "r90", "ma30", "ma90", "range180", "vol30", "volume", "market30"]
LOOKBACK = 180          # days of history a feature row needs
MODEL_FILE = "hold_model.json"
BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), MODEL_FILE)
CLIP = 0.5              # future log returns are clipped to +/-50% when fitting (one-off spikes)


# Features --------------------------------------------------------------------------

def _feature_row(prices, vols, i, market30=0.0):
    """Trend signals at day i from prices[..i] (daily prices, no gaps assumed)."""
    if i < LOOKBACK - 1:
        return None
    p = prices[i]
    if not p or any(prices[i - k] <= 0 for k in (7, 30, 90)):
        return None
    lp = math.log(p)
    w30, w90, w180 = prices[i - 29:i + 1], prices[i - 89:i + 1], prices[i - 179:i + 1]
    lo, hi = min(w180), max(w180)
    rets = [math.log(prices[k] / prices[k - 1]) for k in range(i - 29, i + 1) if prices[k - 1] > 0 and prices[k] > 0]
    mu = sum(rets) / len(rets) if rets else 0.0
    vol30 = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets)) if len(rets) > 1 else 0.0
    v7 = sum(vols[i - 6:i + 1]) / 7.0
    v60 = sum(vols[i - 59:i + 1]) / 60.0
    return {
        "r7": lp - math.log(prices[i - 7]),
        "r30": lp - math.log(prices[i - 30]),
        "r90": lp - math.log(prices[i - 90]),
        "ma30": lp - math.log(sum(w30) / 30.0),
        "ma90": lp - math.log(sum(w90) / 90.0),
        "range180": ((p - lo) / (hi - lo)) if hi > lo else 0.5,
        "vol30": vol30,
        "volume": math.log((v7 + 1) / (v60 + 1)),
        "market30": market30,
    }


def daily_grid(days):
    """Days on a continuous calendar (gaps forward filled), as (t0, prices, volumes)."""
    if not days:
        return None, [], []
    by_t = {d["t"] // DAY * DAY: d for d in days}
    t0, t1 = min(by_t), max(by_t)
    prices, vols = [], []
    last = None
    for t in range(t0, t1 + DAY, DAY):
        d = by_t.get(t)
        if d:
            last = d["price"]
            prices.append(d["price"])
            vols.append(d["hv"] + d["lv"])
        else:
            prices.append(last or 0.0)
            vols.append(0.0)
    return t0, prices, vols


def market_series(grids):
    """Median 30 day log change across items per day (the market's direction)."""
    by_t = {}
    for t0, prices, _ in grids.values():
        for i in range(30, len(prices)):
            if prices[i] > 0 and prices[i - 30] > 0:
                by_t.setdefault(t0 + i * DAY, []).append(math.log(prices[i] / prices[i - 30]))
    out = {}
    for t, v in by_t.items():
        v.sort()
        out[t] = v[len(v) // 2]
    return out


def dataset(grids, market, step=7):
    """Rows of (item, day, features, future log change per horizon) for training and testing."""
    rows = []
    for iid, (t0, prices, vols) in grids.items():
        n = len(prices)
        for i in range(LOOKBACK - 1, n, step):
            t = t0 + i * DAY
            f = _feature_row(prices, vols, i, market.get(t, 0.0))
            if not f:
                continue
            y = {}
            for h in HORIZONS:
                if i + h < n and prices[i + h] > 0:
                    y[h] = max(-CLIP, min(CLIP, math.log(prices[i + h] / prices[i])))
            rows.append({"id": iid, "t": t, "x": f, "y": y})
    return rows


# Ridge regression (standard library) --------------------------------------------------

def _solve(a, b):
    """Solve a x = b by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit(rows, h, lam=50.0, intercept=True, shrink=1.0, use=None):
    """Ridge fit for one horizon on standardized features. Returns a model dict.

    intercept=False drops the average drift of the training period (a market-wide rise or
    fall that need not repeat); shrink scales predictions toward "no change".
    """
    data = [(r["x"], r["y"][h]) for r in rows if h in r["y"]]
    if len(data) < 50:
        return None
    k = len(FEATURES)
    means = [sum(x[f] for x, _ in data) / len(data) for f in FEATURES]
    sds = [math.sqrt(sum((x[f] - means[j]) ** 2 for x, _ in data) / len(data)) or 1.0
           for j, f in enumerate(FEATURES)]
    ybar = sum(y for _, y in data) / len(data)
    center = ybar
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for x, y in data:
        z = [(x[f] - means[j]) / sds[j] for j, f in enumerate(FEATURES)]
        for a in range(k):
            xty[a] += z[a] * (y - ybar)
            for b in range(k):
                xtx[a][b] += z[a] * z[b]
    for a in range(k):
        xtx[a][a] += lam
    coef = _solve(xtx, xty)
    use = set(use if use is not None else FEATURES)
    coef = [c if f in use else 0.0 for f, c in zip(FEATURES, coef)]
    if len(use) < len(FEATURES):  # refit on the kept signals only
        idx = [j for j, f in enumerate(FEATURES) if f in use]
        if idx:
            sub = _solve([[xtx[a][b] for b in idx] for a in idx], [xty[a] for a in idx])
            coef = [0.0] * k
            for j, c in zip(idx, sub):
                coef[j] = c
    model = {"h": h, "means": means, "sds": sds, "coef": [c * shrink for c in coef], "use": sorted(use),
             "intercept": (center if intercept else 0.0) * shrink, "n": len(data),
             "lam": lam, "useIntercept": intercept, "shrink": shrink}
    # Errors in units of each row's own volatility, for the likely range and chance of a rise.
    z = sorted((y - predict(model, x)) / _scale(x, h) for x, y in data)
    model["q"] = {str(q): z[min(len(z) - 1, int(q * (len(z) - 1)))] for q in (0.1, 0.25, 0.5, 0.75, 0.9)}
    model["resid"] = [z[int(j * (len(z) - 1) / 199)] for j in range(200)]  # compact error distribution
    return model


def predict(model, x):
    return model["intercept"] + sum(c * (x[f] - model["means"][j]) / model["sds"][j]
                                    for j, (f, c) in enumerate(zip(FEATURES, model["coef"])))


def _scale(x, h):
    """Typical size of an h day move for this item: daily volatility times the square root of time."""
    return max(0.005, x["vol30"]) * math.sqrt(h)


# Evaluation -------------------------------------------------------------------------

def _corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa = sum((x - ma) ** 2 for x in a)
    sbb = sum((y - mb) ** 2 for y in b)
    return sab / math.sqrt(saa * sbb) if saa and sbb else None


def verdict(exp_ret, p_up):
    if exp_ret >= 0.02 and p_up >= 0.6:
        return "hold"
    if exp_ret <= -0.02 and p_up <= 0.4:
        return "sell"
    return "neutral"


def evaluate(models, rows):
    """How the models did on rows they were not fitted on."""
    out = {}
    for h, m in models.items():
        data = [(r["x"], r["y"][h]) for r in rows if h in r["y"]]
        if not m or len(data) < 30:
            continue
        pred = [predict(m, x) for x, _ in data]
        act = [y for _, y in data]
        sse = sum((p - y) ** 2 for p, y in zip(pred, act))
        sse0 = sum(y * y for y in act)
        hits = [(p > 0) == (y > 0) for p, y in zip(pred, act) if abs(p) > 0.005 and y != 0]
        order = sorted(range(len(pred)), key=lambda i: pred[i])
        q = max(1, len(order) // 5)
        bottom = sum(act[i] for i in order[:q]) / q
        top = sum(act[i] for i in order[-q:]) / q
        inside = sum(1 for (x, y), p in zip(data, pred)
                     if p + m["q"]["0.1"] * _scale(x, h) <= y <= p + m["q"]["0.9"] * _scale(x, h))
        groups = {"hold": [], "sell": [], "neutral": []}
        for (x, y), p in zip(data, pred):
            groups[verdict(math.exp(p) - 1, prob_up(m, x, p))].append(y)
        out[str(h)] = {
            "cases": len(data), "corr": _corr(pred, act),
            "skill": 1 - sse / sse0 if sse0 else None,       # above 0 beats "no change"
            "direction": (sum(hits) / len(hits)) if hits else None,
            "topFifth": math.exp(top) - 1, "bottomFifth": math.exp(bottom) - 1,
            "coverage": inside / len(data),
            "verdicts": {k: {"n": len(v), "avg": (math.exp(sum(v) / len(v)) - 1) if v else None,
                             "rose": (sum(1 for y in v if y > 0) / len(v)) if v else None}
                         for k, v in groups.items()},
        }
    return out


def prob_up(model, x, pred):
    """Share of past errors that would still leave the price above today's."""
    s = _scale(x, model["h"])
    need = -pred / s
    res = model["resid"]
    return sum(1 for z in res if z > need) / len(res)


# Loading history ------------------------------------------------------------------------

def load_grids(db, tax, now, ids=None, top=600, days=800):
    """Daily price grids for the most traded items (or the given ids)."""
    since = int(now - days * DAY)
    if ids is None:
        rows = db.q("SELECT id, SUM(COALESCE(ah * hv, 0) + COALESCE(al * lv, 0)) AS gp FROM d1 "
                    "WHERE ts >= ? GROUP BY id ORDER BY gp DESC LIMIT ?", (int(now - 30 * DAY), top))
        ids = [r["id"] for r in rows]
    hist_d = db.history_many(ids, since, table="d1")
    hist_h = db.history_many(ids, since)
    grids = {}
    for iid in ids:
        hourly = forecast.daily_series(hist_h.get(iid, []), tax, iid)
        first = hourly[0]["t"] if hourly else now
        days_ = [d for d in forecast.daily_from_d1(hist_d.get(iid, []), tax, iid) if d["t"] < first] + hourly
        if len(days_) >= LOOKBACK:
            grids[iid] = daily_grid(days_)
    return grids


# Training ----------------------------------------------------------------------------

def train(db, tax, now=None, progress=None):
    """Walk-forward test on the older/newer split, then refit on everything for use."""
    now = now or time.time()
    say = progress or (lambda m: None)
    say("Loading history")
    grids = load_grids(db, tax, now)
    if len(grids) < 30:
        raise ValueError("Needs at least 180 days of imported daily history. Import a year in Settings first.")
    market = market_series(grids)
    rows = dataset(grids, market)
    dates = sorted({r["t"] for r in rows})
    split = dates[int(len(dates) * 0.6)]
    report = {"items": len(grids), "rows": len(rows), "trainedAt": int(time.time()),
              "from": dates[0], "to": dates[-1], "split": split, "horizons": {}}
    models = {}
    test_models = {}
    choices = {}
    for h in HORIZONS:
        say(f"Choosing settings for the {h} day model")
        # No leakage: a training row's future must end before the test period starts.
        tr = [r for r in rows if r["t"] + h * DAY < split]
        best = choose(tr, h)
        choices[str(h)] = best
        say(f"Testing the {h} day model")
        test_models[h] = fit(tr, h, **best)
        say(f"Fitting the {h} day model")
        models[h] = fit(rows, h, **best)
    report["test"] = evaluate(test_models, [r for r in rows if r["t"] >= split])
    # Use a trend signal only where it beat "no change" on the months it never saw and ranked
    # items the right way round. Otherwise predict no change: the likely range and the chance
    # of a rise then come straight from how real prices moved.
    used = {}
    for h in HORIZONS:
        t = report["test"].get(str(h))
        ok = bool(t and (t["skill"] or 0) > 0 and t["topFifth"] > t["bottomFifth"])
        used[str(h)] = ok
        if not ok:
            say(f"No reliable {h} day signal; using no change")
            models[h] = fit(rows, h, shrink=0.0)
    report["signalUsed"] = used
    report["settings"] = choices
    report["baseRates"] = {"all": base_rates(rows), "recent": base_rates([r for r in rows if r["t"] >= split])}
    report["weights"] = {str(h): dict(zip(FEATURES, m["coef"])) for h, m in models.items() if m}
    return {"models": {str(h): m for h, m in models.items() if m}, "report": report}


def choose(rows, h):
    """Pick ridge strength, intercept and shrink on a validation slice of the training rows.

    The newest quarter of the training dates is held back and scored with the "better than
    no change" skill. A shrink of 0 means the honest answer was "no usable signal".
    """
    dates = sorted({r["t"] for r in rows})
    if len(dates) < 8:
        return {"lam": 50.0, "intercept": False, "shrink": 0.0, "use": []}
    use = stable_signals(rows, h, dates)
    if not use:
        return {"lam": 50.0, "intercept": False, "shrink": 0.0, "use": []}
    cut = dates[int(len(dates) * 0.75)]
    fit_rows = [r for r in rows if r["t"] + h * DAY < cut]
    val = [(r["x"], r["y"][h]) for r in rows if r["t"] >= cut and h in r["y"]]
    if len(val) < 30:
        return {"lam": 50.0, "intercept": False, "shrink": 0.0, "use": use}
    sse0 = sum(y * y for _, y in val) or 1.0
    best, best_skill = {"lam": 50.0, "intercept": False, "shrink": 0.0, "use": use}, 0.0
    for lam in (10.0, 100.0, 1000.0, 10000.0):
        for icpt in (False, True):
            m = fit(fit_rows, h, lam, icpt, use=use)
            if not m:
                continue
            preds = [predict(m, x) for x, _ in val]
            for k in (0.25, 0.5, 0.75, 1.0):
                sk = 1 - sum((k * p - y) ** 2 for p, (_, y) in zip(preds, val)) / sse0
                if sk > best_skill + 1e-6:
                    best, best_skill = {"lam": lam, "intercept": icpt, "shrink": k, "use": use}, sk
    return best


def stable_signals(rows, h, dates=None, min_ic=0.02):
    """Signals that pointed the same way in both halves of the training history.

    A signal that predicted rises in one year and falls in the next is a regime, not a
    rule, so it is left out. Only training rows are used here, never the test months.
    """
    dates = dates or sorted({r["t"] for r in rows})
    mid = dates[len(dates) // 2]
    halves = ([r for r in rows if r["t"] + h * DAY < mid and h in r["y"]],
              [r for r in rows if r["t"] >= mid and h in r["y"]])
    if min(len(x) for x in halves) < 50:
        return []
    keep = []
    for f in FEATURES:
        ics = [_corr([r["x"][f] for r in half], [r["y"][h] for r in half]) for half in halves]
        if all(ic is not None and abs(ic) >= min_ic for ic in ics) and (ics[0] > 0) == (ics[1] > 0):
            keep.append(f)
    return keep


def base_rates(rows):
    """How the typical item did: share higher after each horizon and the median change."""
    out = {}
    for h in HORIZONS:
        ys = sorted(r["y"][h] for r in rows if h in r["y"])
        if ys:
            out[str(h)] = {"up": sum(1 for y in ys if y > 0) / len(ys), "median": math.exp(ys[len(ys) // 2]) - 1,
                           "n": len(ys)}
    return out


def own_history(prices, h, step=7):
    """This item's own record: share of past h day periods it rose, and its median change."""
    ch = [math.log(prices[i + h] / prices[i]) for i in range(0, len(prices) - h, step)
          if prices[i] > 0 and prices[i + h] > 0]
    if len(ch) < 6:
        return None
    ch.sort()
    return {"up": sum(1 for c in ch if c > 0) / len(ch), "median": math.exp(ch[len(ch) // 2]) - 1, "n": len(ch)}


def save(bundle, data_dir):
    path = os.path.join(data_dir, MODEL_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    os.replace(path + ".tmp", path)


def load(data_dir=None):
    """Your own trained model if you trained one, else the one that ships with the app."""
    for path in ([os.path.join(data_dir, MODEL_FILE)] if data_dir else []) + [BUNDLED]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                b = json.load(f)
            b["models"] = {int(k): v for k, v in b["models"].items()}
            b["source"] = "yours" if path != BUNDLED else "bundled"
            return b
        except (OSError, ValueError, KeyError):
            continue
    return None


# Outlook for one item ------------------------------------------------------------------

def outlook(bundle, grid, market30, price_now, qty=1, tax=None, iid=None):
    """Verdict, expected value at each horizon with a likely range, and plain trend facts."""
    t0, prices, vols = grid
    i = len(prices) - 1
    x = _feature_row(prices, vols, i, market30)
    if not x:
        return None
    base = price_now or prices[i]
    points = []
    for h in HORIZONS:
        m = bundle["models"].get(h)
        if not m:
            continue
        p = predict(m, x)
        s = _scale(x, h)
        lo, hi = p + m["q"]["0.1"] * s, p + m["q"]["0.9"] * s
        up = prob_up(m, x, p)
        p = p + m["q"]["0.5"] * s  # middle of the real outcomes, so it agrees with the odds
        mid = base * math.exp(p)
        sell = (lambda v: v - (tax(int(v), iid) if tax else 0))
        points.append({
            "h": h, "ret": math.exp(p) - 1, "lo": math.exp(lo) - 1, "hi": math.exp(hi) - 1,
            "price": mid, "priceLo": base * math.exp(lo), "priceHi": base * math.exp(hi), "pUp": up,
            "value": sell(mid) * qty, "valueLo": sell(base * math.exp(lo)) * qty,
            "valueHi": sell(base * math.exp(hi)) * qty, "verdict": verdict(math.exp(p) - 1, up),
        })
    main = next((pt for pt in points if pt["h"] == 30), points[0] if points else None)
    hi180, lo180 = max(prices[-180:]), min(p for p in prices[-180:] if p > 0)
    facts = {
        "chg7": math.exp(x["r7"]) - 1, "chg30": math.exp(x["r30"]) - 1, "chg90": math.exp(x["r90"]) - 1,
        "chg180": (prices[i] / prices[i - 179] - 1) if prices[i - 179] else None,
        "vsMa30": math.exp(x["ma30"]) - 1, "vsMa90": math.exp(x["ma90"]) - 1,
        "range180": x["range180"], "high180": hi180, "low180": lo180,
        "vol30": x["vol30"], "monthlyMove": math.exp(x["vol30"] * math.sqrt(30)) - 1,
        "volumeTrend": math.exp(x["volume"]) - 1, "market30": math.exp(market30) - 1,
    }
    reasons = explain(bundle, x)
    for h in (30, 90):
        facts[f"own{h}"] = own_history(prices, h)
    signal = {str(h): bool(bundle["models"].get(h) and any(bundle["models"][h]["coef"])) for h in HORIZONS}
    return {"verdict": main["verdict"] if main else "neutral", "points": points, "facts": facts, "signal": signal,
            "reasons": reasons, "priceNow": base, "qty": qty,
            "sellNow": (base - (tax(int(base), iid) if tax else 0)) * qty,
            "history": [{"t": t0 + k * DAY, "v": prices[k]} for k in range(max(0, i - 364), i + 1) if prices[k] > 0]}


LABELS = {
    "r7": ("up {v} over 7 days", "down {v} over 7 days"),
    "r30": ("up {v} over 30 days", "down {v} over 30 days"),
    "r90": ("up {v} over 90 days", "down {v} over 90 days"),
    "ma30": ("{v} above its 30 day average", "{v} below its 30 day average"),
    "ma90": ("{v} above its 90 day average", "{v} below its 90 day average"),
    "range180": ("near its 180 day high", "near its 180 day low"),
    "vol30": ("more volatile than usual", "calmer than usual"),
    "volume": ("trading volume rising", "trading volume falling"),
    "market30": ("the market as a whole rising", "the market as a whole falling"),
}


def explain(bundle, x, h=30):
    """The signals pushing the 30 day outlook the most, in plain words."""
    m = bundle["models"].get(h)
    if not m:
        return []
    parts = []
    for j, f in enumerate(FEATURES):
        z = (x[f] - m["means"][j]) / m["sds"][j]
        effect = m["coef"][j] * z
        if abs(effect) < 0.002:
            continue
        pos_label, neg_label = LABELS[f]
        above = x[f] > m["means"][j] if f in ("range180", "vol30") else x[f] > 0
        if f == "range180":
            above = x[f] >= 0.5
        v = f"{abs(math.exp(x[f]) - 1) * 100:.1f}%" if f in ("r7", "r30", "r90", "ma30", "ma90") else ""
        text = (pos_label if above else neg_label).format(v=v)
        parts.append({"signal": text, "effect": math.exp(effect) - 1})
    parts.sort(key=lambda p: -abs(p["effect"]))
    return parts[:4]
