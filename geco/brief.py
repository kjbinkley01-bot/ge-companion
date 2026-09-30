"""The bank statement: what happened to your wealth since you last looked, and what to do now.

Sections (each only when there is something to say):
  headline      net worth then and now, and where the change came from
  trades        GE fills since then, offers that finished, and anything the offer coach flags
  movers        the holdings whose value moved most over the period
  news          posts that mention items you hold, and announced (upcoming) content
  limits        buy limits that have reset since
  alerts        alerts that fired
  ideas         the top recommendations to add value right now
"""
import time

from . import account, advice, coach, features, networth, news

DAY = 86400


def _value_at(db, iid, t):
    """Average price of an item in the hour around time t (from saved history)."""
    r = db.one("SELECT ah, al FROM h1 WHERE id=? AND ts<=? ORDER BY ts DESC LIMIT 1", (iid, t))
    if not r:
        return None
    vals = [v for v in (r["ah"], r["al"]) if v]
    return sum(vals) / len(vals) if vals else None


def statement(db, engine, cfg, since=None, now=None):
    now = now or time.time()
    since = int(since or now - DAY)
    hours = (now - since) / 3600
    view = networth.combined_view(db, engine, cfg)
    out = {"since": since, "now": int(now), "hours": hours, "total": view["total"]}
    # Headline and attribution.
    perf = networth.performance(db, None, days=(now - since) / DAY, now=now)
    if perf.get("ok"):
        out["change"] = perf["change"]
        out["parts"] = perf["parts"]
        out["returnPct"] = perf["returnPct"]
        out["start"] = perf["start"]
    # Trades.
    fills = db.q("SELECT * FROM ge_fills WHERE t>? ORDER BY t", (since,))
    bought = sum(f["gp"] for f in fills if f["side"] == "buy")
    sold = sum(f["gp"] for f in fills if f["side"] == "sell")
    done = db.q("SELECT * FROM ge_offer_log WHERE closed>? AND state IN ('BOUGHT', 'SOLD') ORDER BY closed DESC",
                (since,))
    closed_flips = [f for f in features.flip_rows(db, engine) if f.get("auto") and not f["open"]
                    and (f.get("sell_ts") or 0) > since]
    out["trades"] = {"fills": len(fills), "bought": bought, "sold": sold,
                     "completed": [{"item": o["item"], "name": (engine.mapping.get(o["item"]) or {}).get("name"),
                                    "side": o["side"], "qty": o["done"], "price": o["price"], "t": o["closed"]}
                                   for o in done[:12]],
                     "profit": sum(f["profit"] or 0 for f in closed_flips), "flips": len(closed_flips)}
    out["coach"] = coach.check(db, engine, cfg, now)
    # Holdings that moved most.
    movers = []
    for h in view["holdings"][:60]:
        if h["how"] in ("cash", "untradeable"):
            continue
        then = _value_at(db, h["id"], since)
        live = engine.by_id.get(h["id"]) or {}
        now_p = live.get("high") and live.get("low") and (live["high"] + live["low"]) / 2
        if not then or not now_p:
            continue
        pct = now_p / then - 1
        movers.append({"id": h["id"], "name": h["name"], "icon": h.get("icon"), "qty": h["qty"], "pct": pct,
                       "gp": h["value"] * pct / (1 + pct)})
    movers.sort(key=lambda m: -abs(m["gp"]))
    out["movers"] = movers[:6]
    # News about what you hold.
    held = {h["id"] for h in view["holdings"]}
    posts = news.feed(db, engine.mapping, days=max(1.0, (now - since) / DAY + 1), limit=100, held=held)
    out["news"] = [p for p in posts if p["t"] >= since - DAY and (p.get("held") or p["kind"] == "game")][:8]
    shown = {p["nid"] for p in out["news"]}
    out["upcoming"] = [p for p in news.feed(db, engine.mapping, days=21, kind="upcoming", limit=50, held=held)
                       if p.get("held") and p["nid"] not in shown][:5]
    # Limits that reset since.
    resets = features.limit_resets(db, since, now)
    out["limits"] = [{"id": r["item_id"] if isinstance(r, dict) else r[0],
                      "name": (engine.mapping.get(r["item_id"] if isinstance(r, dict) else r[0]) or {}).get("name")}
                     for r in resets][:10]
    out["alerts"] = db.q("SELECT * FROM notifications WHERE ts>? ORDER BY ts DESC LIMIT 10", (since,))
    # Ideas to add value now.
    accts = [a["acct"] for a in account.accounts(db)]
    slots = {a: account.slots(db, a, engine.by_id, engine.mapping) for a in accts}
    ideas = [r for r in advice.recommend(view, engine, slots, features.buy_limits(db, engine.mapping))
             if r["group"] in ("trading", "value")]
    out["ideas"] = ideas[:5]
    return out


def text(st, mapping=None):
    """A short plain text version for phone notifications."""
    def gp(v):
        a = abs(v)
        s = "-" if v < 0 else "+"
        return s + (f"{a / 1e9:.2f}b" if a >= 1e9 else f"{a / 1e6:.1f}m" if a >= 1e6 else f"{a / 1e3:.0f}k")
    lines = [f"Bank statement: net worth {st['total'] / 1e6:,.1f}m"]
    if "change" in st:
        p = st["parts"]
        lines.append(f"{gp(st['change'])} since {time.strftime('%a %H:%M', time.localtime(st['since']))} "
                     f"(market {gp(p['mkt'])}, trading {gp(p['trade'])}, loot {gp(p['loot'])})")
    t = st["trades"]
    if t["fills"]:
        lines.append(f"{t['fills']} GE fills, {t['flips']} flips closed ({gp(t['profit'])})")
    for c in st["coach"][:3]:
        lines.append("! " + c["title"])
    for m in st["movers"][:3]:
        lines.append(f"{m['name']} {m['pct'] * 100:+.1f}% ({gp(m['gp'])})")
    for n in st["news"][:2]:
        lines.append("News: " + n["title"])
    return "\n".join(lines)


def seen(db, when=None):
    db.kv_set("statement_seen", int(when or time.time()))


def last_seen(db):
    return db.kv_get("statement_seen")
