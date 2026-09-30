"""Recommendations to improve an account's value, from what it actually holds.

Every suggestion names the items involved, an estimated gp effect, and how solid that
estimate is:
  exact     arithmetic on live prices (alching, decanting, set packing). Prices can move
            before you act, and tax is included.
  estimate  depends on offers filling (flips) or on an assumption stated in the text.
  history   describes what has happened before; it is not a prediction.
  data      the net worth itself is incomplete or estimated until you do this.
"""
from . import market, networth, recipes

NATURE = market.NATURE_RUNE


def _gp(x):
    return int(round(x or 0))


def _fmt(n):
    n = float(n or 0)
    a = abs(n)
    s = "-" if n < 0 else ""
    if a >= 1e9:
        return f"{s}{a / 1e9:.2f}b"
    if a >= 1e6:
        return f"{s}{a / 1e6:.1f}m"
    if a >= 1e4:
        return f"{s}{a / 1e3:.0f}k"
    return f"{s}{a:,.0f}"


def plan_flips(rows, cash, slots, limits=None):
    """Greedy slot plan: the flips that add the most expected profit for the cash available."""
    limits = limits or {}
    pool = [r for r in rows if r.get("profit") and r["profit"] > 0 and not r.get("trap") and not r.get("stale")
            and (r.get("stability") or 0) >= 0.5 and (r.get("vol24") or 0) >= 1000 and r.get("low")
            and r.get("estQty")]
    picks, left = [], cash
    while len(picks) < slots and left > 0:
        best = None
        for r in pool:
            if any(p["id"] == r["id"] for p in picks):
                continue
            lim = limits.get(r["id"], {}).get("left")
            qty = min(r["estQty"], lim if lim is not None else r["estQty"], int(left // r["low"]))
            if qty <= 0:
                continue
            value = r["profit"] * qty * r["stability"]
            if not best or value > best["value"]:
                best = {"id": r["id"], "name": r["name"], "qty": qty, "cost": qty * r["low"],
                        "profit": r["profit"] * qty, "value": value, "buy": r["low"], "sell": r["high"]}
        if not best:
            break
        picks.append(best)
        left -= best["cost"]
    return picks


def recommend(view, engine, slots_by_acct=None, limits=None, own_record=None, min_gp=10000):
    """Build the recommendation list for a net worth view (one account or all)."""
    out = []
    by_id = engine.by_id
    held = {h["id"]: h for h in view["holdings"]}
    total = view["total"] or 1
    tax = engine.tax

    # Data quality first: the numbers below are only as good as what the plugin has seen.
    for c in view.get("coverage", []):
        if not c["ok"] and c["key"] == "bank":
            out.append({"kind": "coverage", "group": "data", "title": "Open your bank once so it is counted",
                        "detail": "The plugin only sees your bank while it is open. Until then net worth leaves it out.",
                        "gp": None, "confidence": "data"})
        elif c["ok"] and c["key"] == "bank" and c["age"] and c["age"] > 7 * 86400:
            out.append({"kind": "coverage", "group": "data", "title": "Your bank snapshot is over a week old",
                        "detail": "Open your bank to refresh it; anything you changed since is not reflected.",
                        "gp": None, "confidence": "data"})
    if view.get("geEstimated"):
        out.append({"kind": "coverage", "group": "data", "title": "Some GE collection boxes are estimated",
                    "detail": "Open the Grand Exchange to refresh what is waiting to be collected.",
                    "gp": None, "confidence": "data"})

    # Offers priced away from the market.
    for acct, slots in (slots_by_acct or {}).items():
        for s in slots:
            if s.get("note"):
                out.append({"kind": "offer", "group": "trading",
                            "title": f"{s['name']}: your {s['side']} offer at {s['price']:,} gp",
                            "detail": s["note"] + f" (market: buy {s.get('market_low') or '-'}, sell {s.get('market_high') or '-'}).",
                            "gp": None, "confidence": "exact", "item": s["item"]})

    # Idle cash.
    free = 8
    if slots_by_acct:
        used = sum(1 for sl in slots_by_acct.values() for s in sl if s["state"] != "EMPTY")
        free = max(0, 8 * max(1, len(slots_by_acct)) - used)
    cash = view["cash"]
    if cash >= max(1_000_000, 0.05 * total) and free > 0:
        picks = plan_flips(engine.rows, cash, min(free, 8), limits)
        picks = [p for p in picks if p["value"] >= min_gp]
        if picks:
            prof = sum(p["value"] for p in picks)
            out.append({"kind": "cash", "group": "trading",
                        "title": f"Put idle cash to work: about {_fmt(prof)} profit per 4 hours",
                        "detail": f"{_fmt(cash)} is sitting in coins with {free} free GE slot(s). The steadiest flips it could fund: "
                                  + ", ".join(f"{p['qty']:,} {p['name']}" for p in picks[:5])
                                  + ". Expected profit already scaled by how often each margin held.",
                        "gp": prof, "confidence": "estimate", "picks": picks})

    # Worth more alched than sold.
    nat_row = by_id.get(NATURE) or {}
    nat = nat_row.get("high") or nat_row.get("low") or 0
    for h in view["holdings"]:
        ha = h.get("highalch") or 0
        if not ha or h["how"] not in ("sell", "market") or not nat:
            continue
        gain = (ha - nat - h["each"]) * h["qty"]
        if gain >= min_gp:
            out.append({"kind": "alch", "group": "value",
                        "title": f"Alch your {h['qty']:,} {h['name']}: +{_fmt(gain)}",
                        "detail": f"High alch gives {ha:,} each; after a nature rune ({nat:,}) that beats selling at {_gp(h['each']):,} after tax.",
                        "gp": gain, "confidence": "exact", "item": h["id"]})

    # Sets: combine held parts, or split held sets.
    pricer = engine.pricer()
    for set_name, parts in recipes.SETS:
        sid = pricer.iid(set_name)
        pids = [pricer.iid(p) for p in parts]
        if sid is None or None in pids:
            continue
        sv = networth.Valuer(engine).each(sid)[0]
        pv = sum(networth.Valuer(engine).each(p)[0] for p in pids)
        if not sv or not pv:
            continue
        n_parts = min(held.get(p, {}).get("qty", 0) for p in pids)
        if n_parts and sv > pv:
            gain = (sv - pv) * n_parts
            if gain >= min_gp:
                out.append({"kind": "set", "group": "value", "title": f"Combine into {n_parts} x {set_name}: +{_fmt(gain)}",
                            "detail": "You hold every piece. The set sells for more than the pieces (free at the GE clerk), after tax.",
                            "gp": gain, "confidence": "exact", "item": sid})
        n_sets = held.get(sid, {}).get("qty", 0)
        if n_sets and pv > sv:
            gain = (pv - sv) * n_sets
            if gain >= min_gp:
                out.append({"kind": "set", "group": "value", "title": f"Split your {set_name}: +{_fmt(gain)}",
                            "detail": "The pieces sell for more than the set, after tax on each piece.",
                            "gp": gain, "confidence": "exact", "item": sid})

    # Decanting held potions into 4-dose.
    doses = {}
    for h in view["holdings"]:
        mt = market.DOSE_RE.match(h["name"])
        if mt and int(mt.group(2)) in (1, 2, 3, 4):
            doses.setdefault(mt.group(1).strip(), {})[int(mt.group(2))] = h
    for base, byd in doses.items():
        four = pricer.iid(f"{base}(4)")
        if four is None:
            continue
        v4 = networth.Valuer(engine).each(four)[0]
        if not v4:
            continue
        gain = 0.0
        for d, h in byd.items():
            if d == 4:
                continue
            gain += h["qty"] * d * (v4 / 4.0) - h["value"]
        if gain >= min_gp:
            out.append({"kind": "decant", "group": "value", "title": f"Decant your {base} into 4-dose: +{_fmt(gain)}",
                        "detail": "Bob Barter at the GE decants for free, and 4-dose potions sell for more per dose.",
                        "gp": gain, "confidence": "exact", "item": four})

    # Held ingredients of profitable processing.
    seen = set()
    for m in recipes.money_making(pricer):
        if m["stale"] or m["thin"] or m["profit"] <= 0 or not m["inputs"]:
            continue
        main = max(m["inputs"], key=lambda i: i["price"] * i["qty"])
        h = held.get(main["id"])
        if not h or main["id"] in seen:
            continue
        actions = int(h["qty"] // main["qty"])
        gain = actions * m["profit"]
        if gain >= max(min_gp, 50000):
            seen.add(main["id"])
            out.append({"kind": "process", "group": "value",
                        "title": f"{m['name']} with your {h['name']}: +{_fmt(gain)}",
                        "detail": f"{actions:,} actions at {_fmt(m['profit'])} each"
                                  + (f", needs level {m['level']} {m['skill']}" if m.get("level", 1) > 1 else "")
                                  + (". Buys the other ingredients at today's prices." if len(m["inputs"]) > 1 else "."),
                        "gp": gain, "confidence": "estimate", "item": main["id"]})

    # Concentration.
    items_only = [h for h in view["holdings"] if h["how"] != "cash"]
    if items_only and total > 0:
        top = items_only[0]
        if top["share"] >= 0.25 and top["value"] >= 1_000_000:
            c30 = (by_id.get(top["id"]) or {}).get("chg7d")
            out.append({"kind": "concentration", "group": "risk",
                        "title": f"{top['share'] * 100:.0f}% of your net worth is {top['name']}",
                        "detail": "One item this large means its price swings move your whole account. "
                                  "Worth deciding on purpose whether to keep that much in one item"
                                  + (f" (it moved {c30 * 100:+.1f}% over the last week)." if c30 is not None else "."),
                        "gp": None, "confidence": "history", "item": top["id"]})

    # Holdings that have mostly lost value over past 90 day periods.
    if own_record:
        for h in items_only[:30]:
            if h["share"] < 0.02:
                continue
            rec = own_record(h["id"])
            if rec and rec["up"] < 0.35 and rec["median"] < -0.05:
                out.append({"kind": "drift", "group": "risk",
                            "title": f"{h['name']} has lost value in most past 90 day periods",
                            "detail": f"It ended higher in only {rec['up'] * 100:.0f}% of them (median {rec['median'] * 100:+.1f}%). "
                                      "That is its history, not a forecast; the Holding outlook tab has the tested view.",
                            "gp": None, "confidence": "history", "item": h["id"]})

    order = {"data": 0, "trading": 1, "value": 2, "risk": 3}
    out.sort(key=lambda r: (order.get(r["group"], 9), -(r["gp"] or 0)))
    return out
