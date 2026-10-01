"""Offer coach: which of your GE offers and open positions need attention, and what to do.

Checks, for every account the RuneLite plugin has seen:

  stale buy     a buy offer that has not filled for a while and sits under the market:
                suggests the price that should fill and how long that would take
  stale sell    the same for a sell offer above the market
  overpaying    a buy priced well above the market (you would fill at your price, not the
                market's, so this only matters for large offers) or a sell far under it
  underwater    an open position (bought, not yet sold) now worth less than you paid after
                tax: the loss if you sell now, and how often this item has recovered that
                much within a week in its own history

Suggestions are advice only; nothing here touches the game.
"""
import json
import time

from . import networth

IDLE_MIN = 30 * 60


def _eta(qty, share, vol24):
    """Hours to fill qty at your share of a day's volume on that side."""
    if not qty or not vol24:
        return None
    rate = share * vol24 / 24.0
    return qty / rate if rate > 0 else None


def recovery_odds(db, engine, iid, needed, days=7, history=400):
    """Share of past `days` day windows in which the price rose at least `needed` (a fraction)."""
    series = networth._daily_prices(db, engine, [iid], history).get(iid) or []
    prices = [p for _, p in series]
    if len(prices) < days + 30:
        return None, 0
    hits = n = 0
    for i in range(len(prices) - days):
        if prices[i] <= 0:
            continue
        n += 1
        if max(prices[i + 1:i + days + 1]) / prices[i] - 1 >= needed:
            hits += 1
    return (hits / n if n else None), n


def _fmt_idle(seconds):
    m = int(seconds // 60)
    return f"{m} min" if m < 90 else f"{seconds / 3600:.1f} hours" if seconds < 48 * 3600 else f"{seconds / 86400:.0f} days"


def check(db, engine, cfg, now=None):
    """[{kind, acct, item, name, title, detail, severity, suggest, ...}] needing attention."""
    from . import account
    now = now or time.time()
    by_id = engine.by_id
    share_fn = None
    if getattr(engine, "fill_rates", None) and cfg.get("fill_share_measured", True):
        from . import fills
        share_fn = fills.share_for(engine.fill_rates, cfg.get("fill_share", 0.2))
    out = []
    names = {a["acct"]: a["name"] for a in account.accounts(db)}
    for o in db.q("SELECT * FROM ge_offers WHERE state IN ('BUYING', 'SELLING')"):
        live = by_id.get(o["item"]) or {}
        name = (engine.mapping.get(o["item"]) or {}).get("name") or f"Item {o['item']}"
        share = share_fn(o["item"])[0] if share_fn else cfg.get("fill_share", 0.2)
        idle = now - (o["last_fill"] or o["opened"] or o["t"])
        left = (o["total"] or 0) - (o["done"] or 0)
        base = {"acct": o["acct"], "acctName": names.get(o["acct"]), "slot": o["slot"], "item": o["item"],
                "name": name, "icon": (engine.mapping.get(o["item"]) or {}).get("icon"), "price": o["price"],
                "left": left, "idle": int(idle)}
        if o["state"] == "BUYING":
            mk = live.get("low")
            if not mk:
                continue
            gap = mk / o["price"] - 1
            eta_now = _eta(left, share, live.get("lv24"))
            if gap > 0.003 and idle > IDLE_MIN:
                new = mk + 1 if mk < 1000 else int(mk * 1.001) + 1
                out.append(dict(base, kind="stale_buy", severity="warn",
                                title=f"{name}: buy at {o['price']:,} has not filled for {_fmt_idle(idle)}",
                                detail=f"The market is buying at {mk:,} ({gap * 100:.1f}% above your offer). "
                                       f"At {new:,} the remaining {left:,} would likely fill in about "
                                       f"{_fmt_h(eta_now)} at your usual share.",
                                suggest=new, eta=eta_now))
            elif gap < -0.03 and left * o["price"] > 1_000_000:
                out.append(dict(base, kind="overpaying", severity="info",
                                title=f"{name}: your buy is {abs(gap) * 100:.1f}% over the market",
                                detail=f"Instant sells are going for {mk:,}. Offers fill at your price, so the "
                                       f"remaining {left:,} could cost about {int(left * (o['price'] - mk)):,} gp more "
                                       f"than a patient offer.",
                                suggest=mk + 1, eta=eta_now))
        else:
            mk = live.get("high")
            if not mk:
                continue
            gap = o["price"] / mk - 1
            eta_now = _eta(left, share, live.get("hv24"))
            if gap > 0.003 and idle > IDLE_MIN:
                new = mk - 1 if mk < 1000 else int(mk * 0.999) - 1
                out.append(dict(base, kind="stale_sell", severity="warn",
                                title=f"{name}: sell at {o['price']:,} has not filled for {_fmt_idle(idle)}",
                                detail=f"The market is selling at {mk:,} ({gap * 100:.1f}% under your offer). "
                                       f"At {new:,} the remaining {left:,} would likely sell in about {_fmt_h(eta_now)}.",
                                suggest=new, eta=eta_now))
            elif gap < -0.03 and left * mk > 1_000_000:
                out.append(dict(base, kind="underselling", severity="info",
                                title=f"{name}: your sell is {abs(gap) * 100:.1f}% under the market",
                                detail=f"Instant buys are paying {mk:,}. The remaining {left:,} could fetch about "
                                       f"{int(left * (mk - o['price'])):,} gp more listed near the market.",
                                suggest=mk - 1, eta=eta_now))
    # Open positions below break-even.
    for f in db.q("SELECT item_id, SUM(qty) AS qty, SUM(qty * buy_price) AS cost, acct FROM flips "
                  "WHERE sell_price IS NULL AND source='auto' GROUP BY acct, item_id"):
        live = by_id.get(f["item_id"]) or {}
        hi = live.get("high")
        if not hi or not f["qty"]:
            continue
        paid = f["cost"] / f["qty"]
        net = hi - engine.tax(hi, f["item_id"])
        loss = (net - paid) * f["qty"]
        if net >= paid or -loss < 50_000:
            continue
        needed = paid / net - 1
        odds, n = recovery_odds(db, engine, f["item_id"], needed)
        name = (engine.mapping.get(f["item_id"]) or {}).get("name") or f"Item {f['item_id']}"
        detail = (f"Bought at {paid:,.0f} on average; selling now nets {net:,} after tax, a loss of "
                  f"{-loss:,.0f} gp. It needs to rise {needed * 100:.1f}% to break even.")
        if odds is not None:
            detail += (f" In its own history it rose that much within a week {odds * 100:.0f}% of the time "
                       f"({n} weeks). That is history, not a promise.")
        out.append({"kind": "underwater", "severity": "warn" if needed > 0.03 else "info", "acct": f["acct"],
                    "acctName": names.get(f["acct"]), "item": f["item_id"], "name": name,
                    "icon": (engine.mapping.get(f["item_id"]) or {}).get("icon"),
                    "title": f"{name}: {f['qty']:,} held below break-even ({loss / 1e6:+.2f}m)",
                    "detail": detail, "loss": loss, "needed": needed, "odds": odds, "suggest": None})
    # Pump warnings on what you hold or watch.
    from . import guard
    held = {h["item_id"] for h in db.q("SELECT DISTINCT item_id FROM flips WHERE sell_price IS NULL")}
    held |= {r["id"] for r in db.q("SELECT id FROM watchlist")}
    for c in db.q("SELECT items FROM holdings_live"):
        held |= {i for i, _ in json.loads(c["items"])}
    for iid, g in guard.scan(db, engine, [i for i in held if i in by_id]).items():
        if g["score"] < 50:
            continue
        name = (engine.mapping.get(iid) or {}).get("name") or f"Item {iid}"
        out.append({"kind": "pump", "severity": "warn" if g["score"] >= 70 else "info", "item": iid, "name": name,
                    "icon": (engine.mapping.get(iid) or {}).get("icon"), "acct": None, "score": g["score"],
                    "title": f"{name} looks like it may be manipulated (score {g['score']})",
                    "detail": "Signs: " + "; ".join(g["why"]) + ". Be careful buying in, and consider selling into it if you hold.",
                    "suggest": None})
    order = {"warn": 0, "info": 1}
    out.sort(key=lambda r: (order.get(r["severity"], 2), -(r.get("idle") or 0)))
    return out


def _fmt_h(h):
    if h is None:
        return "an unknown time"
    if h < 1:
        return f"{max(1, int(h * 60))} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} days"
