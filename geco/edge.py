"""Your edge: what is actually working in your trading, and rules on paper before real gp.

Edge report (closed trades from the flip log, automatic and manual):
  by strategy tag, account, item, price band and hold time: trades, win rate, profit,
  average return after tax, and profit per hour of capital tied up
  execution: for trades the RuneLite plugin recorded, how your buy price compared with the
  average instant-sell price that hour (lower is better) and your sell price with the average
  instant-buy price (higher is better), so you can see whether you pay the spread or earn it

Paper trading: save a rule from the Backtest tab. Its results are the backtest restricted to
entries after the moment you saved it, so it is a genuine forward test on prices it never
saw, with no gp at risk.
"""
import json
import time

from . import analytics

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_rules (
    pid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, params TEXT NOT NULL, created INTEGER NOT NULL,
    active INTEGER DEFAULT 1);
"""
TAGS = ["Flip", "Merch", "Long hold", "Dip buy", "News play", "Set or decant", "Supplies", "Other"]
PRICE_BANDS = [(1000, "Under 1k"), (100_000, "1k to 100k"), (10_000_000, "100k to 10m"), (float("inf"), "10m and up")]
HOLDS = [(1, "Under 1h"), (6, "1 to 6h"), (24, "6 to 24h"), (24 * 7, "1 to 7 days"), (float("inf"), "Over a week")]


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


def _group(rows, key):
    out = {}
    for r in rows:
        k = key(r)
        g = out.setdefault(k, {"key": k, "trades": 0, "wins": 0, "profit": 0.0, "cost": 0.0, "capHours": 0.0,
                               "retSum": 0.0})
        g["trades"] += 1
        g["wins"] += 1 if r["profit"] > 0 else 0
        g["profit"] += r["profit"]
        g["cost"] += r["cost"]
        g["capHours"] += r["cost"] * max(0.25, r.get("hours") or 0.25)
        g["retSum"] += r["roi"] or 0
    res = []
    for g in out.values():
        g["winRate"] = g["wins"] / g["trades"]
        g["avgRet"] = g.pop("retSum") / g["trades"]
        # Profit per hour for every 1m gp tied up: compares a quick flip with a slow hold fairly.
        g["perMillionHour"] = g["profit"] / (g["capHours"] / 1e6) if g["capHours"] else None
        res.append(g)
    return sorted(res, key=lambda g: -g["profit"])


def _band(v, bands):
    for lim, label in bands:
        if v < lim:
            return label
    return bands[-1][1]


def report(db, engine, rows, days=90, now=None):
    """rows: features.flip_rows output. Returns grouped results and execution quality."""
    now = now or time.time()
    since = now - days * 86400
    closed = [r for r in rows if not r["open"] and (r.get("sell_ts") or r.get("buy_ts") or 0) >= since]
    if not closed:
        return {"ok": False, "reason": "No closed trades in this period yet."}
    out = {"ok": True, "days": days, "trades": len(closed),
           "byTag": _group(closed, lambda r: r.get("tag") or "Untagged"),
           "byAccount": _group(closed, lambda r: r.get("acctName") or ("Logged by hand" if not r.get("auto") else "Account")),
           "byItem": _group(closed, lambda r: r["name"])[:15],
           "byPrice": _group(closed, lambda r: _band(r["buy_price"], PRICE_BANDS)),
           "byHold": _group(closed, lambda r: _band(r.get("hours") or 0, HOLDS))}
    worst = sorted(_group(closed, lambda r: r["name"]), key=lambda g: g["profit"])[:5]
    out["worstItems"] = [w for w in worst if w["profit"] < 0]
    # Execution quality for recorded trades: price paid vs the hour's average instant prices.
    fills = db.q("SELECT * FROM ge_fills WHERE t>=? AND caught_up=0", (int(since),))
    buys, sells = [], []
    ids = sorted({f["item"] for f in fills})
    hist = db.history_many(ids, int(since) - 3600) if ids else {}
    for f in fills:
        pts = hist.get(f["item"]) or []
        hr = next((p for p in reversed(pts) if p["ts"] <= f["t"]), None)
        if not hr or not f["qty"]:
            continue
        each = f["gp"] / f["qty"]
        if f["side"] == "buy" and hr["al"]:
            buys.append((each / hr["al"] - 1, f["gp"]))
        elif f["side"] == "sell" and hr["ah"]:
            sells.append((each / hr["ah"] - 1, f["gp"]))

    def wavg(xs):
        w = sum(g for _, g in xs)
        return sum(x * g for x, g in xs) / w if w else None
    out["execution"] = {"buyVsMarket": wavg(buys), "sellVsMarket": wavg(sells), "buys": len(buys), "sells": len(sells)}
    return out


def set_tag(db, row, tag):
    """Tag a trade. Automatic rows are tagged by (account, item, buy time), which survives rebuilds."""
    tag = (tag or "").strip()[:40] or None
    if row.get("source") == "auto":
        if tag:
            db.run("INSERT OR REPLACE INTO trade_tags (acct, item, buy_ts, tag) VALUES (?,?,?,?)",
                   (row["acct"], row["item_id"], row["buy_ts"], tag))
        else:
            db.run("DELETE FROM trade_tags WHERE acct=? AND item=? AND buy_ts=?", (row["acct"], row["item_id"], row["buy_ts"]))
    db.run("UPDATE flips SET tag=? WHERE fid=?", (tag, row["fid"]))


# Paper trading -------------------------------------------------------------------------

def paper_results(db, engine, pid, now=None):
    now = now or time.time()
    r = db.one("SELECT * FROM paper_rules WHERE pid=?", (pid,))
    if not r:
        return None
    params = json.loads(r["params"])
    days = max(1, min(365, int((now - r["created"]) / 86400) + 2))
    res = analytics.backtest(db, engine.mapping, engine.by_id, engine.tax, dict(params, days=days), now=now)
    trades = [t for t in res["trades"] if t["entry"] >= r["created"]]
    profit = sum(t["profit"] for t in trades)
    wins = sum(1 for t in trades if t["profit"] > 0)
    return {"pid": pid, "name": r["name"], "created": r["created"], "params": params, "label": res["label"],
            "trades": len(trades), "profit": profit, "winRate": wins / len(trades) if trades else None,
            "avgRet": sum(t["ret"] for t in trades) / len(trades) if trades else None,
            "recent": trades[:15], "active": bool(r["active"])}
