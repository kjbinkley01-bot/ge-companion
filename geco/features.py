"""Flip log (profit tracker) and account tools (hiscores, goals)."""
import csv
import io
import math
import time

from .skills import level_for_xp, xp_for_level


# Flip log ------------------------------------------------------------------------

def flip_rows(db, engine):
    rows = db.q("SELECT * FROM flips ORDER BY COALESCE(sell_ts, buy_ts) DESC, fid DESC")
    tax = engine.tax
    out = []
    for f in rows:
        m = engine.mapping.get(f["item_id"], {})
        live = engine.by_id.get(f["item_id"], {})
        qty, buy = f["qty"], f["buy_price"]
        r = dict(f)
        r["name"] = m.get("name", f"Item {f['item_id']}")
        r["icon"] = m.get("icon")
        r["cost"] = qty * buy
        if f["sell_price"] is not None:
            t = tax(f["sell_price"], f["item_id"])
            r["taxEach"] = t
            r["revenue"] = qty * (f["sell_price"] - t)
            r["profit"] = r["revenue"] - r["cost"]
            r["open"] = False
        else:
            cur = live.get("high")
            t = tax(cur, f["item_id"]) if cur else 0
            r["taxEach"] = t
            r["livePrice"] = cur
            r["unrealized"] = qty * (cur - t - buy) if cur else None
            r["profit"] = None
            r["open"] = True
        r["roi"] = (r["profit"] / r["cost"]) if (r["profit"] is not None and r["cost"]) else None
        if f["sell_ts"] and f["buy_ts"]:
            r["hours"] = max(0.0, (f["sell_ts"] - f["buy_ts"]) / 3600)
        out.append(r)
    return out


def flip_summary(rows):
    closed = [r for r in rows if not r["open"]]
    total = sum(r["profit"] for r in closed)
    wins = sum(1 for r in closed if r["profit"] > 0)
    unreal = sum(r["unrealized"] or 0 for r in rows if r["open"])
    by_day = {}
    by_item = {}
    for r in closed:
        day = time.strftime("%Y-%m-%d", time.localtime(r["sell_ts"] or r["buy_ts"] or 0))
        by_day[day] = by_day.get(day, 0) + r["profit"]
        it = by_item.setdefault(r["item_id"], {"id": r["item_id"], "name": r["name"],
                                               "profit": 0, "flips": 0})
        it["profit"] += r["profit"]
        it["flips"] += 1
    days = sorted(by_day.items())[-30:]
    hours = sum(r.get("hours", 0) for r in closed)
    return {
        "realized": total, "closed": len(closed), "open": len(rows) - len(closed),
        "winRate": (wins / len(closed)) if closed else None,
        "unrealized": unreal,
        "taxPaid": sum(r["taxEach"] * r["qty"] for r in closed),
        "avgRoi": (sum(r["roi"] for r in closed if r["roi"] is not None) / len(closed)) if closed else None,
        "byDay": [{"day": d, "profit": p} for d, p in days],
        "topItems": sorted(by_item.values(), key=lambda x: x["profit"], reverse=True)[:8],
        "gpPerHourHeld": (total / hours) if hours > 0 else None,
    }


def flips_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "item", "qty", "buy_price", "sell_price", "buy_time", "sell_time",
                "tax_each", "profit", "roi_pct", "note"])
    fmt = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
    for r in rows:
        w.writerow([r["fid"], r["name"], r["qty"], r["buy_price"], r["sell_price"] or "",
                    fmt(r["buy_ts"]), fmt(r["sell_ts"]), r["taxEach"],
                    r["profit"] if r["profit"] is not None else "",
                    f"{r['roi'] * 100:.2f}" if r["roi"] is not None else "", r["note"] or ""])
    return buf.getvalue()


# Account -------------------------------------------------------------------------

def _skills(data):
    return {s["name"]: s for s in data.get("skills", [])}


def account_view(db, player, mode, current):
    """Current stats plus XP gained over 1 day / 7 days / all tracked time."""
    snaps = db.hiscore_snaps(player, mode)
    now = int(time.time())
    cur = _skills(current)

    def base_at(age):
        cands = [s for s in snaps if s["ts"] >= now - age]
        return _skills(cands[0]["data"]) if cands else None

    bases = {"day": base_at(86400), "week": base_at(7 * 86400),
             "all": _skills(snaps[0]["data"]) if snaps else None}
    skills = []
    for name, s in cur.items():
        row = {"name": name, "level": s.get("level"), "xp": s.get("xp"), "rank": s.get("rank")}
        xp = s.get("xp") or 0
        if name != "Overall" and xp >= 0:
            lvl = level_for_xp(xp, cap=126)
            nxt = min(lvl + 1, 126)
            row["virtual"] = lvl
            row["toNext"] = max(0, xp_for_level(nxt) - xp) if lvl < 126 else 0
            span = xp_for_level(nxt) - xp_for_level(lvl)
            row["pctToNext"] = ((xp - xp_for_level(lvl)) / span) if span > 0 else 1
        for k, b in bases.items():
            if b and name in b and b[name].get("xp", -1) >= 0 and xp >= 0:
                row["gain_" + k] = xp - b[name]["xp"]
        skills.append(row)
    acts = [a for a in current.get("activities", []) if (a.get("score") or -1) > 0]
    first = snaps[0]["ts"] if snaps else now
    history = []
    for s in snaps[-90:]:
        ov = _skills(s["data"]).get("Overall", {})
        history.append({"ts": s["ts"], "xp": ov.get("xp")})
    return {"player": current.get("name", player), "mode": mode, "skills": skills,
            "activities": acts, "trackedSince": first, "snapshots": len(snaps),
            "history": history}


def goal_rows(db, engine, player, mode):
    goals = db.q("SELECT * FROM goals WHERE player=? AND mode=? ORDER BY gid", (player.lower(), mode))
    snaps = db.hiscore_snaps(player, mode, since=int(time.time()) - 14 * 86400)
    latest = _skills(snaps[-1]["data"]) if snaps else {}
    out = []
    for g in goals:
        s = latest.get(g["skill"], {})
        xp = s.get("xp")
        target = g["target_xp"] or xp_for_level(g["target_level"] or 99)
        r = dict(g)
        r["targetXp"] = target
        r["currentXp"] = xp
        if xp is not None and xp >= 0:
            r["remaining"] = max(0, target - xp)
            r["progress"] = min(1.0, xp / target) if target else 1.0
            pace = None
            if len(snaps) >= 2:
                a, b = snaps[0], snaps[-1]
                ax = _skills(a["data"]).get(g["skill"], {}).get("xp")
                days = (b["ts"] - a["ts"]) / 86400
                if ax is not None and ax >= 0 and days >= 0.25:
                    pace = (xp - ax) / days
            r["xpPerDay"] = pace
            r["etaDays"] = (r["remaining"] / pace) if pace and pace > 0 else None
            if g["method_item_id"] and g["xp_each"]:
                live = engine.by_id.get(g["method_item_id"], {})
                price = live.get("high")
                actions = math.ceil(r["remaining"] / g["xp_each"]) if g["xp_each"] > 0 else None
                r["methodName"] = engine.mapping.get(g["method_item_id"], {}).get("name")
                r["actions"] = actions
                r["methodPrice"] = price
                r["methodCost"] = actions * price if (actions is not None and price) else None
        out.append(r)
    return out
