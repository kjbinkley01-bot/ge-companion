"""Manipulation guard: does this item's recent action look like a pump (or a dump)?

Pump groups buy a thin item, push the price up and sell into the buyers they attract. The
signs, each scored and explained:

  sharp rise       price up a lot over 24 hours while its market class was flat
  volume burst     the last hour traded many times its usual hourly volume
  thin market      little gp actually traded, so a few players can move it
  wide spread      instant buy and instant sell far apart (one side is not really there)
  lopsided flow    nearly all volume on one side (instant buys during a pump)
  off its history  far above its own 90 day range

A score of 50 or more is a warning, 70 or more a strong warning. It is a heads up, not proof:
real updates can move thin items too, so it lists what it saw.
"""


def assess(row, cat_move=None, range90=None):
    """(score 0 to 100, [reasons]) for one market row."""
    if not row or not row.get("high") or not row.get("low"):
        return 0, []
    score, why = 0, []
    chg = row.get("chg24h")
    rel = chg - (cat_move or 0) if chg is not None else None
    if rel is not None and rel > 0.15:
        pts = min(35, int(rel * 100))
        score += pts
        why.append(f"up {chg * 100:.0f}% in 24h" + (f" while its class moved {cat_move * 100:+.1f}%" if cat_move is not None else ""))
    elif rel is not None and rel < -0.2:
        score += min(25, int(-rel * 80))
        why.append(f"down {abs(chg) * 100:.0f}% in 24h (a dump after a pump often looks like this)")
    last, avg = row.get("vol1h") or 0, (row.get("vol24") or 0) / 24.0
    if avg >= 2 and last >= 5 * avg:
        score += 20
        why.append(f"last hour traded {last / avg:.0f}x its usual volume")
    gp24 = (row.get("vol24") or 0) * (row["high"] + row["low"]) / 2
    if gp24 < 20_000_000 and (row.get("vol24") or 0) < 2000:
        score += 15
        why.append(f"thin market: about {gp24 / 1e6:.1f}m gp traded in 24h")
    spread = row["high"] / row["low"] - 1
    if spread > 0.08:
        score += 10
        why.append(f"instant buy and sell {spread * 100:.0f}% apart")
    bp = row.get("buyPressure")
    if bp is not None and bp > 0.85 and (chg or 0) > 0.05:
        score += 10
        why.append(f"{bp * 100:.0f}% of volume is instant buys")
    if range90 and range90[1] > range90[0] > 0:
        lo, hi = range90
        if row["high"] > hi * 1.25:
            score += 15
            why.append(f"{(row['high'] / hi - 1) * 100:.0f}% above its 90 day high")
    return min(100, score), why


def level(score):
    return "strong" if score >= 70 else "warning" if score >= 50 else "watch" if score >= 30 else None


def scan(db, engine, ids):
    """{id: {"score", "level", "why"}} for the given items (held and watched)."""
    from . import networth
    moves = {}
    for r in engine.rows:
        if r.get("chg24h") is not None and (r.get("vol24") or 0) > 500:
            moves.setdefault(networth.category(r["name"]), []).append(r["chg24h"])
    med = {k: sorted(v)[len(v) // 2] for k, v in moves.items() if v}
    series = networth._daily_prices(db, engine, list(ids), 95)
    out = {}
    for iid in ids:
        row = engine.by_id.get(iid)
        if not row:
            continue
        pts = [p for _, p in (series.get(iid) or [])][:-1]
        rng = (min(pts), max(pts)) if len(pts) >= 30 else None
        score, why = assess(row, med.get(networth.category(row["name"])), rng)
        if score:
            out[iid] = {"score": score, "level": level(score), "why": why}
    return out
