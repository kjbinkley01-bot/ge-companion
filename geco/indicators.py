"""Technical indicators, and an honest test of whether each one predicts anything.

Indicators (on daily prices): moving averages, RSI and Bollinger bands. The report
replays each signal over the imported daily history of the most traded items and
measures what happened next, after the 2% tax, against the market index:

  signal    the item's move over the next 7 and 30 days after the signal, minus the
            market's move (so a rising market is not credited to the signal)
  control   the same measure on every day for the same items (what you would get
            buying at random), so a signal only counts if it beats this
  halves    the history is split in two; a signal has to work in both the older and
            the newer half, because patterns that only appear in one half are usually luck

A signal fires at most once a week per item, so one long dip is not counted seven times.
"""
import math
import time

from . import analytics, hold, risk

DAY = 86400
SIGNALS = {
    "rsi_low": ("RSI(14) below 30 (oversold)", "buy"),
    "rsi_high": ("RSI(14) above 70 (overbought)", "avoid"),
    "golden": ("20 day average crosses above the 50 day (golden cross)", "buy"),
    "death": ("20 day average crosses below the 50 day (death cross)", "avoid"),
    "bb_low": ("Close below the lower Bollinger band (20 days, 2 sd)", "buy"),
    "bb_high": ("Close above the upper Bollinger band (20 days, 2 sd)", "buy"),
    "high90": ("New 90 day high (breakout)", "buy"),
    "low90": ("New 90 day low", "avoid"),
}
HORIZONS = (7, 30)


def sma(vals, n):
    out, run = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= n:
            run -= vals[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema(vals, n):
    out, k, e = [None] * len(vals), 2.0 / (n + 1), None
    for i, v in enumerate(vals):
        e = v if e is None else v * k + e * (1 - k)
        if i >= n - 1:
            out[i] = e
    return out


def rsi(vals, n=14):
    return analytics._series_rsi(vals, n)


def bollinger(vals, n=20, k=2.0):
    m, s = analytics._series_sma(vals, n)
    up = [None if a is None else a + k * b for a, b in zip(m, s)]
    lo = [None if a is None else a - k * b for a, b in zip(m, s)]
    return m, up, lo


def _signals(p):
    """{signal: [day index, ...]} for one item's daily prices (no gaps)."""
    n = len(p)
    r = rsi(p)
    s20, s50 = sma(p, 20), sma(p, 50)
    mid, up, lo = bollinger(p)
    out = {k: [] for k in SIGNALS}
    for i in range(90, n):
        c = p[i]
        if r[i] is not None:
            if r[i] < 30:
                out["rsi_low"].append(i)
            if r[i] > 70:
                out["rsi_high"].append(i)
        if s20[i - 1] is not None and s50[i - 1] is not None:
            if s20[i - 1] <= s50[i - 1] and s20[i] > s50[i]:
                out["golden"].append(i)
            if s20[i - 1] >= s50[i - 1] and s20[i] < s50[i]:
                out["death"].append(i)
        if lo[i] is not None and c < lo[i]:
            out["bb_low"].append(i)
        if up[i] is not None and c > up[i]:
            out["bb_high"].append(i)
        window = p[i - 90:i]
        if c > max(window):
            out["high90"].append(i)
        if c < min(window):
            out["low90"].append(i)
    # At most one signal a week per item.
    for k, idx in out.items():
        kept, last = [], -99
        for i in idx:
            if i - last >= 7:
                kept.append(i)
                last = i
        out[k] = kept
    return out


def _mean_t(xs):
    n = len(xs)
    if n < 3:
        return None, None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd else None)


def report(db, tax, engine, now=None, min_days=200):
    """Run every signal over the daily history. Slow (a few seconds): cache the result."""
    now = now or time.time()
    grids = hold.load_grids(db, tax, now)
    mkt = risk.market_index(db, engine, 800, now=now)["series"]
    m_by_t = {p["t"]: p["v"] for p in mkt}
    res = {k: {h: [] for h in HORIZONS} for k in SIGNALS}
    ctrl = {h: [] for h in HORIZONS}
    all_days = set()
    for iid, (t0, prices, _) in grids.items():
        if len(prices) < min_days or min(prices) <= 0:
            continue
        mk = [m_by_t.get(t0 + i * DAY) for i in range(len(prices))]
        sig = _signals(prices)

        def fwd(i, h):
            j = i + h
            if j >= len(prices) or not mk[i] or not mk[j]:
                return None
            sell = prices[j] - tax(prices[j], iid)
            return (sell / prices[i] - 1) - (mk[j] / mk[i] - 1)
        for k, idx in sig.items():
            for i in idx:
                for h in HORIZONS:
                    r = fwd(i, h)
                    if r is not None and abs(r) < 1.5:
                        res[k][h].append((t0 + i * DAY, r))
                        all_days.add(t0 + i * DAY)
        for i in range(90, len(prices), 7):
            for h in HORIZONS:
                r = fwd(i, h)
                if r is not None and abs(r) < 1.5:
                    ctrl[h].append((t0 + i * DAY, r))
    if not all_days:
        return {"ok": False, "reason": "Needs about 200 days of imported daily history. Import history in Settings."}
    days = sorted(all_days)
    split = days[len(days) // 2]
    out = {"ok": True, "items": len(grids), "from": days[0], "to": days[-1], "split": split, "signals": []}
    for h in HORIZONS:
        c_all, _ = _mean_t([r for _, r in ctrl[h]])
        out.setdefault("control", {})[h] = {"mean": c_all, "n": len(ctrl[h])}
    for k, (label, side) in SIGNALS.items():
        row = {"key": k, "label": label, "side": side}
        for h in HORIZONS:
            xs = res[k][h]
            c = out["control"][h]["mean"] or 0.0
            ex = [r - c for _, r in xs]
            early = [r - c for t, r in xs if t < split]
            late = [r - c for t, r in xs if t >= split]
            m, t = _mean_t(ex)
            me, _ = _mean_t(early)
            ml, _ = _mean_t(late)
            good = (lambda v: v is not None and v > 0) if side == "buy" else (lambda v: v is not None and v < 0)
            if len(xs) < 30 or t is None:
                verdict = "too few"
            elif good(me) and good(ml) and abs(t) >= 2 and good(m):
                verdict = "worked in both halves"
            elif good(m) and abs(t) >= 2:
                verdict = "only in one half"
            elif not good(me) and not good(ml) and me is not None and ml is not None and abs(t) >= 2 \
                    and me != 0 and ml != 0:
                verdict = "worked in reverse"
            else:
                verdict = "no edge"
            row[str(h)] = {"n": len(xs), "excess": m, "t": t, "early": me, "late": ml,
                           "up": sum(1 for x in ex if x > 0) / len(ex) if ex else None, "verdict": verdict}
        out["signals"].append(row)
    return out
