"""Backtest and tune the forecast model on imported daily market history.

Walk forward: for many past dates ("cutoffs") and many liquid items, build the forecast
using only the days before the cutoff, then compare it with what actually happened over
the following days. Nothing after a cutoff leaks into its forecast.

Realized profit follows the same trading rule the forecast assumes: on each day the model
expected a positive margin, you flip your share of that day's real volume (capped by the
buy limit) at that day's real after-tax margin. Bad days count as losses.

Tuning is a coordinate search over forecast.DEFAULT_PARAMS on the older cutoffs only; the
newer cutoffs are held out to check the tuned settings on data they never saw. Finally the
width of the simulated range is calibrated so about 8 in 10 real outcomes land inside it.
"""
import json
import math
import os
import random
import time

from . import forecast

DAY = forecast.DAY

GRID = {
    "roi_alpha": [0.05, 0.1, 0.2, 0.3, 0.5],
    "vol_alpha": [0.1, 0.2, 0.3, 0.5],
    "window": [14, 30, 60, 90],
    "half_life": [0.25, 0.5, 1.0, 2.0, 4.0],
    "shrink": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1],
    "min_edge": [0.0, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03],
}
PRICE_GRID = {"trend": ["auto", "off", "on"], "phi": [0.7, 0.8, 0.9, 0.95]}


def universe(db, mapping, tax, now, top=150, min_days=150, lookback_days=400):
    """Most traded items (by gp over the last 30 imported days) with enough daily history."""
    since = int(now - lookback_days * DAY)
    rows = db.q("SELECT id, SUM(COALESCE(ah * hv, 0) + COALESCE(al * lv, 0)) AS gp, COUNT(*) AS n "
                "FROM d1 WHERE ts >= ? GROUP BY id", (int(now - 30 * DAY),))
    counts = {r["id"]: r["n"] for r in db.q("SELECT id, COUNT(*) AS n FROM d1 WHERE ts >= ? GROUP BY id", (since,))}
    ids = [r["id"] for r in sorted(rows, key=lambda r: -(r["gp"] or 0))
           if counts.get(r["id"], 0) >= min_days and r["id"] in mapping][:top]
    hist = db.history_many(ids, since, table="d1")
    return {i: forecast.daily_from_d1(hist.get(i, []), tax, i) for i in ids}


def _row_at(day):
    """What the app would have seen as the live row on a past day."""
    if day.get("ah") and day.get("al"):
        return {"high": day["ah"], "low": day["al"], "roi": day["roi"]}
    return None


def cases(series, mapping, horizon, step=7, warmup=60, share=0.2, windows=2.0, params=None):
    """(item, cutoff) forecast cases with the price half of the model precomputed."""
    out = []
    for iid, days in series.items():
        limit = mapping.get(iid, {}).get("limit")
        for c in range(warmup, len(days) - horizon + 1, step):
            fut = days[c:c + horizon]
            # Skip windows with missing days, so "30 days" really means 30 days.
            if (fut[-1]["t"] - days[c - 1]["t"]) // DAY > horizon + 2:
                continue
            m = forecast.build_model(days[:c], _row_at(days[c - 1]), limit, share, windows, params)
            if m:
                out.append({"id": iid, "cut": days[c - 1]["t"], "hist": days[:c], "fut": fut, "m": m})
    return out


def realized_profit(m, fut):
    """What following the model's advice would really have made over `fut`."""
    total = 0.0
    for d, day in enumerate(fut, 1):
        if not forecast.trades(m, d) or day["roi"] is None:
            continue
        qty = min(m["cap"], m["share"] * min(day["hv"], day["lv"]))
        total += day["roi"] * day["price"] * qty
    return total


def _ranks(v):
    """Ranks with ties sharing their average rank."""
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def _spearman(xs, ys):
    """Rank correlation. A forecast that ranks everything equal scores 0, not 1."""
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0


def oracle_profit(m, fut):
    """The most one could have made: flipping only on the days that really were profitable."""
    total = 0.0
    for day in fut:
        if day["roi"] is not None and day["roi"] > 0:
            total += day["roi"] * day["price"] * min(m["cap"], m["share"] * min(day["hv"], day["lv"]))
    return total


def score(cs, params, horizon):
    """Profit and ranking accuracy for one parameter set over a list of cases.

    wape is the plain gp error (dominated by the most expensive items); nwape scales each
    forecast by that item's daily trade size so every item counts about equally.
    """
    F, R, N = [], [], []
    by_cut = {}
    for c in cs:
        m = dict(c["m"], **forecast.margin_part(c["hist"], params))
        f = forecast.expected(m, horizon)[-1]["cum"]
        r = realized_profit(m, c["fut"])
        if "oracle" not in c:
            c["oracle"] = oracle_profit(m, c["fut"])  # does not depend on the tuned settings
        scale = m["basePrice"] * max(1.0, min(m["cap"], m["share"] * min(m["hv"], m["lv"])))
        F.append(f)
        R.append(r)
        N.append(scale)
        by_cut.setdefault(c["cut"], []).append((f, r, c["oracle"]))
    abs_r = sum(abs(r) for r in R) or 1.0
    wape = sum(abs(f - r) for f, r in zip(F, R)) / abs_r
    nabs = sum(abs(r) / n for r, n in zip(R, N)) or 1.0
    nwape = sum(abs(f - r) / n for f, r, n in zip(F, R, N)) / nabs
    traded = [(f, r) for f, r in zip(F, R) if f > 0]
    sp = [v for v in (_spearman([a for a, _, _ in g], [b for _, b, _ in g]) for g in by_cut.values())
          if v is not None]
    # Usefulness: what the model's top 10 picks (only those it would trade) really made,
    # against the best 10 anyone could have made with hindsight. Never trading scores 0.
    got = best = top_f_sum = top_r_sum = 0.0
    for g in by_cut.values():
        if len(g) < 20:
            continue
        picks = [x for x in sorted(g, key=lambda x: -x[0])[:10] if x[0] > 0]
        got += sum(r for _, r, _ in picks)
        top_f_sum += sum(f for f, _, _ in picks)
        top_r_sum += sum(abs(r) for _, r, _ in picks)
        best += sum(o for _, _, o in sorted(g, key=lambda x: -x[2])[:10])
    return {
        "wape": wape, "nwape": nwape, "bias": (sum(F) / sum(R) - 1) if sum(R) else None,
        "tradedCases": len(traded), "profitableShare": (sum(1 for _, r in traded if r > 0) / len(traded)) if traded else None,
        "spearman": (sum(sp) / len(sp)) if sp else None,
        "top10": (got / best) if best else None,
        "top10Error": (abs(top_f_sum - got) / top_r_sum) if top_r_sum else None,
        "forecast": sum(F), "realized": sum(R), "cases": len(cs),
    }


def objective(s):
    """Lower is better.

    Mostly: how much of the achievable profit the top 10 picks really captured. Then how
    honest the forecast numbers for those picks were, and how well items were ranked.
    Never trading captures nothing, so it cannot win by being silent.
    """
    capture = max(-1.0, min(1.0, s["top10"] or 0.0))
    honesty = min(2.0, s["top10Error"]) if s["top10Error"] is not None else 1.0
    return -capture + 0.3 * honesty + 0.25 * (1 - (s["spearman"] or 0))


def price_score(cs, horizon):
    model = naive = 0.0
    for c in cs:
        m = c["m"]
        pred = m["basePrice"] * math.exp(forecast.trend_path(m["trend"], m["phi"], horizon))
        actual = c["fut"][-1]["price"]
        model += abs(pred / actual - 1)
        naive += abs(m["basePrice"] / actual - 1)
    n = len(cs) or 1
    return {"model": model / n, "naive": naive / n}


def coverage(cs, horizon, band, price_band, sample=300, paths=150, seed=3, level=0.0):
    """Share of real outcomes inside the simulated 10 to 90% range.

    Profit is only judged on forecasts that would trade (a zero forecast with a zero
    outcome says nothing about the range); price is judged on every case.
    """
    rng = random.Random(seed)
    pick = cs if len(cs) <= sample else rng.sample(cs, sample)
    traded = [c for c in cs if forecast.trades(c["m"], 1)]
    tpick = traded if len(traded) <= sample else rng.sample(traded, sample)
    inside = p_inside = 0
    for c in tpick:
        m = dict(c["m"], band=band, priceBand=price_band, level=level)
        b = forecast.simulate(m, horizon, paths=paths)[-1]
        r = realized_profit(m, c["fut"])
        inside += 1 if b["c10"] <= r <= b["c90"] else 0
    for c in pick:
        m = dict(c["m"], band=band, priceBand=price_band)
        b = forecast.simulate(m, horizon, paths=paths)[-1]
        p = c["fut"][-1]["price"]
        p_inside += 1 if b["p10"] <= p <= b["p90"] else 0
    return {"profit": (inside / len(tpick)) if tpick else None, "price": p_inside / (len(pick) or 1),
            "cases": len(pick), "tradedCases": len(tpick)}


def run(db, mapping, tax, horizon=30, share=0.2, windows=2.0, now=None, progress=None, top=300, start=None):
    """Full backtest and tuning. Returns a report with before/after metrics and tuned params.

    `start` is the settings in use now (the shipped defaults unless you tuned before). New
    settings are only adopted if they also do better on the held out dates; tuning that only
    fits the older dates better is noise, and the current settings are kept.
    """
    t0 = time.time()
    now = now or time.time()
    say = progress or (lambda msg: None)
    say("Loading imported history")
    series = universe(db, mapping, tax, now, top=top)
    if len(series) < 10:
        raise ValueError("Not enough imported daily history. Import at least 180 days first.")
    current = dict(forecast.DEFAULT_PARAMS, **(start or {}))
    base = dict(current)
    # Price settings first (they change the cached price half of the model).
    say("Testing price trend settings")
    price_results = []
    best_price = None
    for trend in PRICE_GRID["trend"]:
        for phi in PRICE_GRID["phi"] if trend != "off" else [0.9]:
            p = dict(base, trend=trend, phi=phi)
            cs = cases(series, mapping, horizon, step=14, share=share, windows=windows, params=p)
            split = _split(cs)
            ps = price_score(split[0], horizon)
            price_results.append({"trend": trend, "phi": phi, "train": ps})
            if best_price is None or ps["model"] < best_price[0]:
                best_price = (ps["model"], trend, phi)
    base.update(trend=best_price[1], phi=best_price[2])
    say("Building forecast cases")
    cs = cases(series, mapping, horizon, step=7, share=share, windows=windows, params=base)
    train, test = _split(cs)
    before = {"train": score(train, current, horizon), "test": score(test, current, horizon)}
    # A naive reference: today's margin simply continues (never fades).
    naive_p = dict(forecast.DEFAULT_PARAMS, half_life=1e9)
    naive = {"test": score(test, naive_p, horizon)}
    best = dict(base)
    best_obj = objective(score(train, best, horizon))
    for rnd in range(2):
        for key, values in GRID.items():
            say(f"Tuning {key} (round {rnd + 1})")
            for v in values:
                trial = dict(best, **{key: v})
                o = objective(score(train, trial, horizon))
                if o < best_obj - 1e-9:
                    best_obj, best = o, trial
    after = {"train": score(train, best, horizon), "test": score(test, best, horizon)}
    adopted = objective(after["test"]) < objective(before["test"])
    if not adopted:
        best, after = dict(current), before
    # Calibrate the simulated range on the training cases with the tuned margin settings.
    say("Calibrating the likely range")
    tuned_train = [dict(c, m=dict(c["m"], **forecast.margin_part(c["hist"], best))) for c in train]
    tuned_test = [dict(c, m=dict(c["m"], **forecast.margin_part(c["hist"], best))) for c in test]
    cal = []
    # Price range: one width setting. Margin range: day to day width and the level shift.
    for pb in (0.75, 0.9, 1.0, 1.1, 1.25, 1.5):
        cov = coverage(tuned_train, horizon, 1.0, pb, sample=200, paths=100)
        cal.append({"price_band": pb, "coverage": cov["price"]})
    best["price_band"] = min(cal, key=lambda x: abs(x["coverage"] - 0.8))["price_band"]
    mcal = []
    for band in (1.0, 1.5, 2.0):
        for level in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
            cov = coverage(tuned_train, horizon, band, best["price_band"], sample=200, paths=100, level=level)
            if cov["profit"] is not None:
                mcal.append({"band": band, "level": level, "coverage": cov["profit"]})
    if mcal:
        pick = min(mcal, key=lambda x: (abs(x["coverage"] - 0.8), x["band"] + x["level"]))
        best["band"], best["level"] = pick["band"], pick["level"]
    cal = {"price": cal, "margin": mcal}
    if not adopted:  # keep the current ranges too
        best.update({k: current[k] for k in ("band", "price_band", "level")})
    D = current
    cov_before = coverage([dict(c, m=dict(c["m"], **forecast.margin_part(c["hist"], D))) for c in test],
                          horizon, D["band"], D["price_band"], level=D["level"])
    tuned_test = [dict(c, m=dict(c["m"], **forecast.margin_part(c["hist"], best))) for c in test]
    cov_after = coverage(tuned_test, horizon, best["band"], best["price_band"], level=best["level"])
    # The other horizons, checked with the tuned settings on held out cutoffs.
    other = {}
    for h in (7, 90):
        hc = cases(series, mapping, h, step=7, share=share, windows=windows, params=best)
        _, htest = _split(hc)
        if htest:
            other[h] = {"before": score(htest, current, h), "after": score(htest, best, h),
                        "price": price_score(htest, h)}
    days_span = max(len(d) for d in series.values())
    return {
        "horizon": horizon, "items": len(series), "days": days_span, "cases": len(cs),
        "trainCases": len(train), "testCases": len(test),
        "trainUntil": train[-1]["cut"] if train else None,
        "params": best, "defaults": forecast.DEFAULT_PARAMS, "start": current, "adopted": adopted,
        "before": before, "after": after, "naive": naive,
        "price": {"train": price_score(train, horizon), "test": price_score(test, horizon),
                  "grid": price_results},
        "coverage": {"before": cov_before, "after": cov_after, "calibration": cal},
        "otherHorizons": other, "seconds": round(time.time() - t0, 1), "ranAt": int(time.time()),
        "share": share, "windows": windows,
    }


def _split(cs, frac=2 / 3):
    """Older cutoffs for tuning, newer ones held out for checking."""
    cuts = sorted({c["cut"] for c in cs})
    if not cuts:
        return [], []
    edge = cuts[min(len(cuts) - 1, int(len(cuts) * frac))]
    return [c for c in cs if c["cut"] < edge], [c for c in cs if c["cut"] >= edge]


def save(report, data_dir):
    path = os.path.join(data_dir, forecast.PARAMS_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    os.replace(path + ".tmp", path)
    return path


def load_report(data_dir):
    """Your own tuning report if you ran one, else the one the shipped defaults came from."""
    for path in (os.path.join(data_dir, forecast.PARAMS_FILE),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "forecast_baseline.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None
