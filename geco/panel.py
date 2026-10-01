"""Everything the RuneLite side panel shows, in one response.

The plugin reads its own GE slots from the game client; this adds what only the app knows:
net worth, market prices and coach notes for those slots, a looked up item in detail, flip
ideas you can afford, the watchlist, the account summary, average costs for the bank tooltip,
and new notifications. Display only: nothing here acts on the game.
"""
import time

from . import account, coach, features, guard, networth, news


def _row_price(E, iid):
    r = E.by_id.get(iid) or {}
    return {"high": r.get("high"), "low": r.get("low"), "profit": r.get("profit"), "roi": r.get("roi"),
            "chg24h": r.get("chg24h")}


def _spark(D, iid, hours=24):
    rows = D.history_many([iid], int(time.time() - hours * 3600)).get(iid, [])
    out = []
    for r in rows:
        v = [x for x in (r["ah"], r["al"]) if x]
        if v:
            out.append([r["ts"], round(sum(v) / len(v), 1)])
    return out


def _item(app, iid, view, limits, acct):
    E, D = app.engine, app.db
    r = E.by_id.get(iid) or {}
    m = E.mapping.get(iid) or {}
    pos = next((h for h in view["holdings"] if h["id"] == iid), None)
    lim = limits.get(iid)
    if lim and acct:
        lim = next((x for x in lim.get("byAcct", []) if x["acct"] == acct), lim)
    tax_each = E.tax(r["high"], iid) if r.get("high") else None
    breakeven = E.tax.breakeven(pos["costEach"], iid) if pos and pos.get("costEach") else None
    out = {
        "id": iid, "name": m.get("name") or f"Item {iid}", "members": m.get("members"),
        "high": r.get("high"), "low": r.get("low"), "profit": r.get("profit"), "roi": r.get("roi"),
        "tax": tax_each, "chg24h": r.get("chg24h"), "vol24": r.get("vol24"),
        "limit": m.get("limit"), "limitLeft": lim["left"] if lim else m.get("limit"),
        "limitResetAt": lim["resetAt"] if lim else None,
        "suggestBuy": r["low"] + 1 if r.get("low") else None,
        "suggestSell": r["high"] - 1 if r.get("high") else None,
        "fillHrs": r.get("fillHrs"), "stability": r.get("stability"), "trap": bool(r.get("trap")),
        "held": pos["qty"] if pos else 0, "costEach": pos.get("costEach") if pos else None,
        "value": pos["value"] if pos else None, "pnl": pos.get("pnl") if pos else None, "breakeven": breakeven,
        "watched": bool(D.one("SELECT 1 AS x FROM watchlist WHERE id=?", (iid,))),
        "spark": _spark(D, iid),
        "targets": [{"side": t["side"], "price": t["price"], "qty": t["qty"]}
                    for t in D.q("SELECT side, price, qty FROM targets WHERE item_id=? AND hit_at IS NULL ORDER BY price", (iid,))],
    }
    try:
        g = guard.scan(D, E, [iid]).get(iid)
        if g and g.get("score", 0) >= 50:
            out["guard"] = {"score": g["score"], "level": g.get("level"), "why": g.get("why", [])[:3]}
    except Exception:  # noqa: BLE001
        pass
    try:
        posts = news.feed(D, E.mapping, days=45, item=iid, limit=3)
        out["news"] = [{"t": p["t"], "title": p["title"], "kind": p.get("kindLabel"), "upcoming": p.get("upcoming")}
                       for p in posts[:3]]
    except Exception:  # noqa: BLE001
        out["news"] = []
    return out


def _ideas(E, cash, free_slots, limits, n=15):
    """Flips worth placing now that the cash on hand can fund (a slot's share of it)."""
    budget = cash / max(1, free_slots) if cash else None
    rows = []
    with E.lock:
        market = list(E.rows)
    for r in market:
        if not r.get("profit") or r["profit"] <= 0 or r.get("trap") or r.get("stale") or (r.get("vol24") or 0) < 1000:
            continue
        if budget is not None and r.get("low") and r["low"] > budget:
            continue
        lim = limits.get(r["id"])
        if lim and lim.get("left") == 0:
            continue
        rows.append(r)
    rows.sort(key=lambda r: -(r.get("adj4h") or 0))
    return [{"id": r["id"], "name": r["name"], "buy": r["low"], "sell": r["high"], "profit": r["profit"],
             "roi": r.get("roi"), "adj4h": r.get("adj4h"), "fillHrs": r.get("fillHrs"),
             "stability": r.get("stability")} for r in rows[:n]]


def build(app, acct=None, item=None, slot_items=(), since_nid=0, now=None):
    E, D, cfg = app.engine, app.db, app.cfg
    now = now or time.time()
    accts = account.accounts(D)
    known = {a["acct"] for a in accts}
    acct = acct if acct in known else None
    view = networth.account_view(D, E, acct, cfg) if acct else networth.combined_view(D, E, cfg)
    ch = networth.changes(D, acct, view["total"]).get("d1") or {}
    limits = features.buy_limits(D, E.mapping, now)
    out = {
        "now": int(now),
        "header": {"total": view["total"], "d1": ch.get("gp"), "d1pct": ch.get("pct"), "cash": view["cash"],
                   "items": view["items"], "ge": view["ge"], "pnl": view.get("pnl"),
                   "acctName": next((a["name"] for a in accts if a["acct"] == acct), None) if acct else "All accounts",
                   "live": bool(E.status.get("last_latest")) and now - E.status["last_latest"] < 600},
    }
    # Slots: market prices for the items in the player's slots, and the coach's notes by slot.
    out["prices"] = {str(i): _row_price(E, i) for i in set(slot_items) if i > 0}
    notes = {}
    attention = []
    for c in coach.check(D, E, cfg, now):
        if c.get("slot") is not None and (not acct or c.get("acct") == acct):
            notes[str(c["slot"])] = {"kind": c["kind"], "title": c["title"], "detail": c.get("detail"),
                                     "suggest": c.get("suggest"), "eta": c.get("eta")}
        if not acct or c.get("acct") in (None, acct):
            attention.append({"kind": c["kind"], "title": c["title"], "suggest": c.get("suggest"), "item": c.get("item")})
    out["slotNotes"] = notes
    if item:
        out["item"] = _item(app, int(item), view, limits, acct)
    # Ideas and the watchlist.
    used = D.one("SELECT COUNT(*) AS n FROM ge_offers WHERE state IN ('BUYING','SELLING','BOUGHT','SOLD')"
                 + (" AND acct=?" if acct else ""), (acct,) if acct else ())
    free = max(1, 8 - (used["n"] if used else 0))
    out["ideas"] = {"cash": view["cash"], "freeSlots": free, "rows": _ideas(E, view["cash"], free, limits)}
    out["watch"] = []
    for w in D.q("SELECT id FROM watchlist ORDER BY added DESC LIMIT 40"):
        r = E.by_id.get(w["id"]) or {}
        out["watch"].append({"id": w["id"], "name": (E.mapping.get(w["id"]) or {}).get("name") or f"Item {w['id']}",
                             "high": r.get("high"), "low": r.get("low"), "chg24h": r.get("chg24h"), "profit": r.get("profit")})
    # Account.
    day0 = int(now - 86400)
    flips = [f for f in features.flip_rows(D, E) if not f["open"] and (f.get("sell_ts") or 0) >= day0
             and (not acct or f.get("acct") == acct)]
    fills = D.q("SELECT * FROM ge_fills WHERE t>=?" + (" AND acct=?" if acct else "") + " ORDER BY t DESC LIMIT 8",
                (day0, acct) if acct else (day0,))
    soon = sorted([dict(v, id=k) for k, v in limits.items() if v["resetAt"] - now < 3600 * 4
                   and (not acct or v.get("acct") == acct)], key=lambda v: v["resetAt"])[:6]
    out["account"] = {
        "realized24": sum(f["profit"] or 0 for f in flips), "flips24": len(flips),
        "fills": [{"item": f["item"], "name": (E.mapping.get(f["item"]) or {}).get("name"), "side": f["side"],
                   "qty": f["qty"], "gp": f["gp"], "t": f["t"]} for f in fills],
        "limits": [{"id": v["id"], "name": (E.mapping.get(v["id"]) or {}).get("name"), "left": v.get("left"),
                    "limit": v.get("limit"), "resetAt": v["resetAt"]} for v in soon],
        "attention": attention[:8],
        "top": [{"id": h["id"], "name": h["name"], "qty": h["qty"], "value": h["value"], "pnl": h.get("pnl"),
                 "chg24gp": h.get("chg24gp")} for h in view["holdings"] if h.get("how") != "cash"][:8],
    }
    # Average cost per item (for the bank tooltip).
    # [quantity, average cost each, value each now] per item held.
    out["costs"] = {str(h["id"]): [h["qty"], round(h["costEach"], 1), round(h["value"] / h["qty"], 1) if h["qty"] else None]
                    for h in view["holdings"] if h.get("costEach") and h.get("how") != "cash"}
    # Notifications newer than the plugin has seen (first call only returns the latest id).
    last = D.one("SELECT MAX(nid) AS n FROM notifications")
    top = (last["n"] if last else 0) or 0
    out["lastNid"] = top
    out["notifications"] = [] if not since_nid else [
        {"nid": n["nid"], "message": n["message"], "item": n["item_id"]}
        for n in D.q("SELECT nid, message, item_id FROM notifications WHERE nid>? ORDER BY nid LIMIT 5", (since_nid,))]
    return out
