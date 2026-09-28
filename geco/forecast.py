"""Forecasting: expected long term flip profit from demand, margins and current prices.

The model is deliberately simple and checks itself:

* Price. Daily volume weighted prices are smoothed with a damped trend (Holt's method),
  then anchored to today's live price. Before trusting the trend, the model is refit on
  older days and scored against the days it did not see; if a flat "no change" forecast
  would have done better, the trend is dropped.
* Demand. Daily instant-buy and instant-sell volumes are smoothed separately. What you can
  flip in a day is your share of the slower side, capped by the buy limit times how many
  limit windows you use a day.
* Margin. Today's live after-tax margin (as ROI) fades toward the item's recent average,
  halving the gap each day, since unusual margins rarely last. Live margins flagged as a trap
  or stale are ignored.
* Uncertainty. A Monte Carlo simulation replays real past days: each simulated day draws a
  margin and a volume level from the item's own history and moves the price by a random
  step sized from past forecast errors. The spread of outcomes gives low / expected / high.

Everything reads the saved hourly history, never the Wiki. Forecasts are estimates, and the
further out they go, the more they are a statement about risk rather than a prediction.
"""
import json
import math
import os
import random
import time

DAY = 86400

# Tunable settings. geco/forecast_eval.py backtests the model on imported market history and
# searches these; the tuned values are saved to data/forecast_params.json and used from then on.
DEFAULT_PARAMS = {
    "roi_alpha": 0.1,     # smoothing of the daily margin (higher = recent days count more)
    "vol_alpha": 0.1,     # smoothing of daily volume
    "window": 90,         # days of margin and volume history the model looks at
    "half_life": 0.25,    # days for today's live margin to fade halfway to the usual margin
    "shrink": 0.8,        # scale on the expected margin (below 1 because forecasts ran hot)
    "min_edge": 0.0025,   # only trade on days the expected margin (ROI) is above this
    "trend": "off",       # "auto" / "on" / "off"; trends lost to "no change" in the backtest
    "phi": 0.9,           # trend damping per day
    "band": 2.0,          # width of the simulated margin range (calibrated for 8 in 10 coverage)
    "price_band": 1.0,    # width of the simulated price range (calibrated the same way)
    "level": 0.75,        # uncertainty in the item's average margin itself (regimes last weeks)
}
# The values above come from backtesting on a year of real market history (the 300 most
# traded items, Sep 2025 to Sep 2026, 30 day forecasts, tuned on older dates and checked on
# newer ones). The full report ships as forecast_baseline.json and shows in the Forecast tab.
PARAMS_FILE = "forecast_params.json"


def load_params(data_dir=None):
    p = dict(DEFAULT_PARAMS)
    if data_dir:
        try:
            with open(os.path.join(data_dir, PARAMS_FILE), "r", encoding="utf-8") as f:
                saved = json.load(f).get("params", {})
            p.update({k: v for k, v in saved.items() if k in DEFAULT_PARAMS})
        except (OSError, ValueError, AttributeError):
            pass
    return p


def daily_series(rows, tax, iid):
    """Aggregate hourly rows into days: price, per side volume, after-tax margin ROI.

    Days with fewer than 6 hours of trades are dropped; volume is scaled to a full day by
    the hours that were saved, so a partly recorded day does not look like low demand.
    """
    days = {}
    for r in rows:
        d = days.setdefault(r["ts"] // DAY * DAY, {"num": 0.0, "den": 0, "hv": 0, "lv": 0, "hours": 0,
                                                    "roi": [], "held": 0, "both": 0})
        d["hours"] += 1
        d["hv"] += r["hv"]
        d["lv"] += r["lv"]
        if r["ah"] is not None and r["hv"]:
            d["num"] += r["ah"] * r["hv"]
            d["den"] += r["hv"]
        if r["al"] is not None and r["lv"]:
            d["num"] += r["al"] * r["lv"]
            d["den"] += r["lv"]
        if r["ah"] is not None and r["al"]:
            m = (r["ah"] - tax(r["ah"], iid) - r["al"]) / r["al"]
            d["roi"].append(m)
            d["both"] += 1
            d["held"] += 1 if m > 0 else 0
    out = []
    for t in sorted(days):
        d = days[t]
        if d["hours"] < 6 or not d["den"]:
            continue
        scale = 24.0 / d["hours"]
        out.append({"t": t, "price": d["num"] / d["den"], "hv": d["hv"] * scale, "lv": d["lv"] * scale,
                    "roi": (sum(d["roi"]) / len(d["roi"])) if d["roi"] else None,
                    "held": (d["held"] / d["both"]) if d["both"] else None, "hours": d["hours"]})
    return out


def holt(ys, alpha, beta, phi):
    """Damped trend smoothing. Returns (level, trend, one step errors)."""
    level, trend = ys[0], (ys[1] - ys[0]) if len(ys) > 1 else 0.0
    errs = []
    for y in ys[1:]:
        pred = level + phi * trend
        errs.append(y - pred)
        new_level = alpha * y + (1 - alpha) * pred
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return level, trend, errs


def fit_holt(ys, phi=0.9):
    """Pick smoothing settings by one step error. Works on log prices."""
    best = None
    for a in (0.2, 0.35, 0.5, 0.7, 0.9):
        for b in (0.05, 0.15, 0.3):
            lvl, tr, errs = holt(ys, a, b, phi)
            sse = sum(e * e for e in errs)
            if best is None or sse < best[0]:
                best = (sse, a, b, lvl, tr, errs)
    _, a, b, lvl, tr, errs = best
    return {"alpha": a, "beta": b, "phi": phi, "level": lvl, "trend": tr, "errs": errs}


def trend_path(trend, phi, h):
    """Cumulative damped trend after h steps."""
    return trend * sum(phi ** i for i in range(1, h + 1))


def holdout(logp, k, phi=0.9):
    """Mean absolute % error of the trend model and of 'no change' on the last k days."""
    train, test = logp[:-k], logp[-k:]
    if len(train) < 4:
        return None
    f = fit_holt(train, phi)
    model = naive = 0.0
    for h, y in enumerate(test, 1):
        model += abs(math.exp(y - (f["level"] + trend_path(f["trend"], f["phi"], h))) - 1)
        naive += abs(math.exp(y - train[-1]) - 1)
    return {"model": model / k, "naive": naive / k, "days": k}


def ewma(vals, alpha=0.3):
    lvl = None
    for v in vals:
        lvl = v if lvl is None else alpha * v + (1 - alpha) * lvl
    return lvl


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def build_model(days, row, limit, share=0.2, windows=2.0, params=None):
    """Everything the forecast needs from an item's daily history and its live row."""
    P = dict(DEFAULT_PARAMS, **(params or {}))
    if len(days) < 3:
        return None
    logp = [math.log(d["price"]) for d in days[-120:]]
    fit = fit_holt(logp, P["phi"])
    check = holdout(logp, min(7, len(logp) // 3), P["phi"]) if len(logp) >= 9 else None
    if P["trend"] == "on":
        use_trend = True
    elif P["trend"] == "off":
        use_trend = False
    else:
        use_trend = bool(check and check["model"] < check["naive"])
    trend = fit["trend"] if use_trend else 0.0
    # Daily step size: one step errors of the chosen model, or plain day to day changes.
    steps = fit["errs"] if use_trend else [b - a for a, b in zip(logp, logp[1:])]
    sigma = math.sqrt(sum(e * e for e in steps) / len(steps)) if steps else 0.02
    sigma = max(sigma, 0.002)
    mp = margin_part(days, P)
    live_price = None
    live_roi = None
    if row and row.get("high") and row.get("low"):
        live_price = (row["high"] + row["low"]) / 2.0
        # A margin the flip finder flags as a trap or stale is not a signal worth following.
        if not row.get("trap") and not row.get("stale"):
            live_roi = row.get("roi")
    base_price = live_price or days[-1]["price"]
    cap = (limit * windows) if limit else float("inf")
    return dict(mp, **{
        "days": len(days), "fit": fit, "useTrend": use_trend, "trend": trend, "phi": fit["phi"],
        "sigma": sigma, "check": check, "liveRoi": live_roi,
        "basePrice": base_price, "cap": cap, "share": share,
        "limit": limit, "windows": windows,
        "spread": ((row["high"] - row["low"]) / base_price) if (row and row.get("high") and row.get("low")) else 0.0,
    })


def margin_part(days, P):
    """Demand and margin estimates (the cheap half of the model, re-run by the tuner)."""
    recent = days[-int(P["window"]):]
    full = [d for d in recent if d["hours"] >= 20] or recent
    hv = ewma([d["hv"] for d in full], P["vol_alpha"])
    lv = ewma([d["lv"] for d in full], P["vol_alpha"])
    rois = [d["roi"] for d in recent if d["roi"] is not None]
    held = [d["held"] for d in recent if d["held"] is not None]
    base = min(hv, lv) or 1
    return {"hv": hv, "lv": lv, "rois": rois, "histRoi": ewma(rois, P["roi_alpha"]) if rois else None,
            "held": (sum(held) / len(held)) if held else None,
            "volRatios": [min(d["hv"], d["lv"]) / base for d in full] or [1.0],
            "halfLife": P["half_life"], "shrink": P["shrink"], "band": P["band"],
            "minEdge": P["min_edge"], "priceBand": P["price_band"], "level": P["level"], "params": P}


def _roi_on_day(m, d):
    """Live margin fading toward the historical average (halving every `half_life` days)."""
    hist, live = m["histRoi"], m["liveRoi"]
    k = m.get("shrink", 1.0)
    if hist is None and live is None:
        return 0.0
    if hist is None:
        return live * k
    if live is None:
        return hist * k
    hl = max(0.05, m.get("halfLife", 1.0))
    return (hist + (live - hist) * 0.5 ** (d / hl)) * k


def trades(m, d):
    """Whether the model would flip on day d: expected margin above the minimum edge."""
    return _roi_on_day(m, d) > max(0.0, m.get("minEdge", 0.0))


def expected(m, horizon):
    """Deterministic expected path: price, daily quantity and cumulative profit."""
    qty = min(m["cap"], m["share"] * min(m["hv"], m["lv"]))
    out, cum = [], 0.0
    for d in range(1, horizon + 1):
        price = m["basePrice"] * math.exp(trend_path(m["trend"], m["phi"], d))
        roi = _roi_on_day(m, d)
        profit = roi * price * qty if trades(m, d) else 0.0  # no flipping without an expected edge
        cum += profit
        out.append({"d": d, "price": price, "qty": qty, "roi": roi, "profit": profit, "cum": cum})
    return out


def simulate(m, horizon, paths=400, seed=7):
    """Monte Carlo paths of price and cumulative flip profit, returning percentile bands.

    You flip on a day only when the model expects a profit that day, but the margin you
    actually get is drawn from real past days, so bad days still cost money.
    """
    rng = random.Random(seed)
    base_q = min(m["cap"], m["share"] * min(m["hv"], m["lv"]))
    rois = m["rois"][-30:] or [m["histRoi"] or 0.0]
    hist = m["histRoi"] if m["histRoi"] is not None else (sum(rois) / len(rois))
    price_at = [[] for _ in range(horizon)]
    cum_at = [[] for _ in range(horizon)]
    mu = sum(rois) / len(rois)
    sd = math.sqrt(sum((x - mu) ** 2 for x in rois) / len(rois)) if len(rois) > 1 else 0.0
    for _ in range(paths):
        logp = math.log(m["basePrice"])
        cum = 0.0
        # One persistent shift per path: the usual margin itself may be wrong for weeks.
        shift = rng.gauss(0, m.get("level", 0.0) * sd)
        for d in range(1, horizon + 1):
            logp += m["trend"] * m["phi"] ** d + rng.gauss(0, m["sigma"] * m.get("priceBand", 1.0))
            price = math.exp(logp)
            # A drawn day keeps its spread around the average, plus the fading live signal.
            roi = (rng.choice(rois) - hist) * m.get("band", 1.0) + _roi_on_day(m, d) + shift
            qty = min(m["cap"], base_q * rng.choice(m["volRatios"]))
            if trades(m, d):
                cum += roi * price * qty
            price_at[d - 1].append(price)
            cum_at[d - 1].append(cum)
    bands = []
    for d in range(horizon):
        ps, cs = sorted(price_at[d]), sorted(cum_at[d])
        bands.append({"d": d + 1, "p10": _pct(ps, 0.1), "p50": _pct(ps, 0.5), "p90": _pct(ps, 0.9),
                      "c10": _pct(cs, 0.1), "c50": _pct(cs, 0.5), "c90": _pct(cs, 0.9),
                      "lossChance": sum(1 for c in cs if c < 0) / len(cs)})
    return bands


def hold_outcome(m, bands, tax, iid, row):
    """Buy one limit now at the instant-sell price and sell after the horizon (investing)."""
    if not row or not row.get("low") or not m["limit"]:
        return None
    buy = row["low"]
    half = m["spread"] / 2
    last = bands[-1]
    res = {}
    for k in ("p10", "p50", "p90"):
        sell = last[k] * (1 + half)
        net = sell - tax(int(sell), iid)
        res[k] = (net - buy) * m["limit"]
        res[k + "Ret"] = net / buy - 1
    res["qty"] = m["limit"]
    res["cost"] = buy * m["limit"]
    return res


def confidence(m):
    if m["days"] < 7:
        return "low"
    err = m["check"]["model" if m["useTrend"] else "naive"] if m["check"] else None
    if m["days"] >= 21 and err is not None and err < 0.05:
        return "high"
    return "medium"


def daily_from_d1(rows, tax, iid):
    """Days from imported /24h windows (one row per day, volumes already full day)."""
    out = []
    for r in rows:
        num = den = 0
        if r["ah"] is not None and r["hv"]:
            num += r["ah"] * r["hv"]
            den += r["hv"]
        if r["al"] is not None and r["lv"]:
            num += r["al"] * r["lv"]
            den += r["lv"]
        if not den:
            continue
        roi = ((r["ah"] - tax(r["ah"], iid) - r["al"]) / r["al"]) if (r["ah"] is not None and r["al"]) else None
        out.append({"t": r["ts"], "price": num / den, "hv": r["hv"], "lv": r["lv"], "roi": roi,
                    "held": None, "hours": 24, "ah": r["ah"], "al": r["al"]})
    return out


def load_days(db, iid, tax, since, now):
    """Imported daily history, then the app's own hourly history where it exists."""
    hourly = daily_series(db.history("h1", iid, since), tax, iid)
    first = hourly[0]["t"] if hourly else now
    daily = [d for d in daily_from_d1(db.history("d1", iid, since), tax, iid) if d["t"] < first]
    return daily + hourly


def item_forecast(db, iid, row, mapping, tax, horizon=30, share=0.2, windows=2.0, history_days=180, now=None,
                  params=None):
    now = now or time.time()
    days = load_days(db, iid, tax, now - history_days * DAY, now)
    limit = mapping.get(iid, {}).get("limit")
    m = build_model(days, row, limit, share, windows, params)
    if not m:
        return {"id": iid, "ok": False, "daysOfHistory": len(days),
                "reason": "Needs at least 3 days of saved hourly history for this item."}
    exp = expected(m, horizon)
    bands = simulate(m, horizon)
    start = int(now // DAY * DAY)
    for b in bands:
        b["t"] = start + b["d"] * DAY
    for e in exp:
        e["t"] = start + e["d"] * DAY
    last = bands[-1]
    return {
        "id": iid, "ok": True, "horizon": horizon, "daysOfHistory": m["days"],
        "confidence": confidence(m),
        "history": [{"t": d["t"], "price": d["price"], "vol": d["hv"] + d["lv"], "roi": d["roi"]} for d in days],
        "expected": exp, "bands": bands,
        "summary": {
            "profit": exp[-1]["cum"], "p10": last["c10"], "p50": last["c50"], "p90": last["c90"],
            "lossChance": last["lossChance"], "perDay": exp[-1]["cum"] / horizon,
            "dailyQty": exp[0]["qty"], "roiNow": m["liveRoi"], "roiHist": m["histRoi"],
            "priceNow": m["basePrice"], "priceEnd": exp[-1]["price"],
            "priceChange": exp[-1]["price"] / m["basePrice"] - 1,
            "priceP10": last["p10"], "priceP90": last["p90"],
            "demandPerDay": min(m["hv"], m["lv"]), "limitCapped": m["cap"] < m["share"] * min(m["hv"], m["lv"]),
            "held": m["held"], "useTrend": m["useTrend"], "check": m["check"], "sigma": m["sigma"],
        },
        "hold": hold_outcome(m, bands, tax, iid, row),
        "assumptions": {"share": share, "windows": windows, "limit": limit, "paths": 400},
    }


def rank(db, mapping, rows_by_id, tax, horizon=30, share=0.2, windows=2.0, min_vol=1000, max_items=400,
         history_days=120, now=None, params=None):
    """Expected flip profit over the horizon for the liquid market, best first (no simulation)."""
    now = now or time.time()
    cand = [r for r in rows_by_id.values()
            if (r.get("vol24") or 0) >= min_vol and r.get("high") and r.get("low")]
    cand.sort(key=lambda r: -(r["vol24"] * (r["high"] + r["low"]) / 2))
    cand = cand[:max_items]
    since = now - history_days * DAY
    hist = db.history_many([r["id"] for r in cand], since)
    hist_d = db.history_many([r["id"] for r in cand], since, table="d1")
    out = []
    for r in cand:
        hourly = daily_series(hist.get(r["id"], []), tax, r["id"])
        first = hourly[0]["t"] if hourly else now
        days = [d for d in daily_from_d1(hist_d.get(r["id"], []), tax, r["id"]) if d["t"] < first] + hourly
        m = build_model(days, r, mapping.get(r["id"], {}).get("limit"), share, windows, params)
        if not m:
            continue
        exp = expected(m, horizon)
        # Analytic spread: daily margin swings and price risk scale with the square root of time.
        sd_roi = 0.0
        if len(m["rois"]) >= 2:
            mu = sum(m["rois"]) / len(m["rois"])
            sd_roi = math.sqrt(sum((x - mu) ** 2 for x in m["rois"]) / (len(m["rois"]) - 1))
        daily_gp = m["basePrice"] * exp[0]["qty"]
        spread = sd_roi * daily_gp * math.sqrt(horizon)
        out.append({
            "id": r["id"], "name": r["name"], "icon": r.get("icon"), "members": r.get("members"),
            "profit": exp[-1]["cum"], "low": exp[-1]["cum"] - 1.28 * spread, "high": exp[-1]["cum"] + 1.28 * spread,
            "perDay": exp[-1]["cum"] / horizon, "dailyQty": exp[0]["qty"],
            "roiNow": m["liveRoi"], "roiHist": m["histRoi"], "held": m["held"],
            "priceNow": m["basePrice"], "priceChange": exp[-1]["price"] / m["basePrice"] - 1,
            "trend": m["useTrend"], "days": m["days"], "confidence": confidence(m),
            "capital": r["low"] * exp[0]["qty"],
        })
    out.sort(key=lambda x: -x["profit"])
    return {"horizon": horizon, "share": share, "windows": windows, "items": out,
            "maxDays": max((x["days"] for x in out), default=0)}
