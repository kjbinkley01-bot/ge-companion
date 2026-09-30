"""Flip log, buy limits, portfolio, account tools (hiscores, goals, boss drops)."""
import csv
import io
import math
import re
import time

from . import recipes
from .skills import level_for_xp, xp_for_level

LIMIT_WINDOW = 4 * 3600


# Buy limits ------------------------------------------------------------------------

def limit_windows(buys):
    """Group (ts, qty) buys into GE limit windows.

    A window opens with the first buy and lasts 4 hours; every buy inside it counts
    toward the limit. The next buy after it closes opens a new window.
    Returns [[start, used], ...] in time order.
    """
    windows = []
    for ts, qty in sorted(buys):
        if windows and ts < windows[-1][0] + LIMIT_WINDOW:
            windows[-1][1] += qty
        else:
            windows.append([ts, qty])
    return windows


def buy_limits(db, mapping, now=None):
    """Active limit windows per item: used, left and reset time.

    GE limits are per account. Trades the RuneLite plugin recorded carry their account;
    trades logged by hand count for your main account (the one seen most recently). Each
    item shows the main account's window, with every account's in `byAcct`.
    """
    now = now or time.time()
    rows = db.q("SELECT item_id, qty, buy_ts, acct FROM flips WHERE buy_ts >= ? ORDER BY item_id, buy_ts",
                (int(now - 24 * 3600),))
    have_accts = db.one("SELECT name FROM sqlite_master WHERE name='accounts'")
    accts = db.q("SELECT acct, name FROM accounts ORDER BY last_seen DESC") if have_accts else []
    main = accts[0]["acct"] if accts else None
    names = {a["acct"]: a["name"] for a in accts}
    per = {}
    for r in rows:
        per.setdefault((r["item_id"], r.get("acct") or main), []).append((r["buy_ts"], r["qty"]))
    by_item = {}
    for (iid, acct), buys in per.items():
        start, used = limit_windows(buys)[-1]
        reset = start + LIMIT_WINDOW
        if reset <= now:
            continue
        limit = mapping.get(iid, {}).get("limit")
        by_item.setdefault(iid, []).append({"acct": acct, "name": names.get(acct), "used": used, "start": start,
                                            "resetAt": reset, "limit": limit,
                                            "left": max(0, limit - used) if limit else None})
    out = {}
    for iid, lst in by_item.items():
        pick = next((x for x in lst if x["acct"] == main), None) or max(lst, key=lambda x: x["used"])
        out[iid] = dict(pick, byAcct=lst)
    return out


def limit_resets(db, since, until):
    """Items whose limit window closed between `since` and `until` (for notifications)."""
    rows = db.q("SELECT item_id, qty, buy_ts FROM flips WHERE buy_ts >= ? ORDER BY item_id, buy_ts",
                (int(since - 2 * LIMIT_WINDOW),))
    per = {}
    for r in rows:
        per.setdefault(r["item_id"], []).append((r["buy_ts"], r["qty"]))
    out = []
    for iid, buys in per.items():
        for start, used in limit_windows(buys):
            reset = start + LIMIT_WINDOW
            if since < reset <= until:
                out.append((iid, used, reset))
    return out


# Flip log ------------------------------------------------------------------------

def flip_rows(db, engine):
    rows = db.q("SELECT * FROM flips ORDER BY COALESCE(sell_ts, buy_ts) DESC, fid DESC")
    tax = engine.tax
    names = {a["acct"]: a["name"] for a in db.q("SELECT acct, name FROM accounts")} if db.one(
        "SELECT name FROM sqlite_master WHERE name='accounts'") else {}
    out = []
    for f in rows:
        m = engine.mapping.get(f["item_id"], {})
        live = engine.by_id.get(f["item_id"], {})
        qty, buy = f["qty"], f["buy_price"]
        r = dict(f)
        r["name"] = m.get("name", f"Item {f['item_id']}")
        r["icon"] = m.get("icon")
        r["auto"] = f.get("source") == "auto"
        r["acctName"] = names.get(f.get("acct"))
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
        # Edge vs the market when the flip was logged: positive means you did better
        # than the instant price (bought under instant-sell, sold over instant-buy).
        rl, rh = f.get("ref_low"), f.get("ref_high")
        r["buyEdge"] = (rl - buy) / rl if rl else None
        r["sellEdge"] = ((f["sell_price"] - rh) / rh) if (rh and f["sell_price"] is not None) else None
        out.append(r)
    return out


HOLD_BUCKETS = [(1, "Under 1h"), (4, "1 to 4h"), (12, "4 to 12h"), (24, "12 to 24h"),
                (72, "1 to 3 days"), (float("inf"), "3 days+")]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def flip_summary(rows):
    closed = [r for r in rows if not r["open"]]
    total = sum(r["profit"] for r in closed)
    wins = sum(1 for r in closed if r["profit"] > 0)
    unreal = sum(r["unrealized"] or 0 for r in rows if r["open"])
    by_day, by_item = {}, {}
    by_hour = [{"hour": h, "profit": 0, "flips": 0} for h in range(24)]
    by_wday = [{"day": d, "profit": 0, "flips": 0} for d in WEEKDAYS]
    holds = [{"label": lbl, "flips": 0, "profit": 0, "roiSum": 0.0} for _, lbl in HOLD_BUCKETS]
    for r in closed:
        ts = r["sell_ts"] or r["buy_ts"] or 0
        lt = time.localtime(ts)
        day = time.strftime("%Y-%m-%d", lt)
        by_day[day] = by_day.get(day, 0) + r["profit"]
        by_hour[lt.tm_hour]["profit"] += r["profit"]
        by_hour[lt.tm_hour]["flips"] += 1
        by_wday[lt.tm_wday]["profit"] += r["profit"]
        by_wday[lt.tm_wday]["flips"] += 1
        it = by_item.setdefault(r["item_id"], {"id": r["item_id"], "name": r["name"],
                                               "profit": 0, "flips": 0})
        it["profit"] += r["profit"]
        it["flips"] += 1
        if r.get("hours") is not None:
            for k, (lim, _) in enumerate(HOLD_BUCKETS):
                if r["hours"] < lim:
                    holds[k]["flips"] += 1
                    holds[k]["profit"] += r["profit"]
                    holds[k]["roiSum"] += r["roi"] or 0
                    break
    for h in holds:
        h["avgRoi"] = (h.pop("roiSum") / h["flips"]) if h["flips"] else None
    # Equity curve and worst peak to trough drop, in close order.
    equity, cum, peak, dd, streak, best_streak = [], 0, 0, 0, 0, 0
    for r in sorted(closed, key=lambda r: (r["sell_ts"] or r["buy_ts"] or 0, r["fid"])):
        cum += r["profit"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        equity.append({"t": r["sell_ts"] or r["buy_ts"] or 0, "v": cum})
        streak = streak + 1 if r["profit"] > 0 else 0
        best_streak = max(best_streak, streak)
    days = sorted(by_day.items())[-30:]
    hours = sum(r.get("hours", 0) for r in closed)
    rois = [r["roi"] for r in closed if r["roi"] is not None]
    be = [r["buyEdge"] for r in rows if r.get("buyEdge") is not None]
    se = [r["sellEdge"] for r in closed if r.get("sellEdge") is not None]
    best = max(closed, key=lambda r: r["profit"]) if closed else None
    worst = min(closed, key=lambda r: r["profit"]) if closed else None
    pick = lambda r: {"fid": r["fid"], "name": r["name"], "profit": r["profit"], "item_id": r["item_id"]} if r else None
    return {
        "realized": total, "closed": len(closed), "open": len(rows) - len(closed),
        "winRate": (wins / len(closed)) if closed else None,
        "unrealized": unreal,
        "taxPaid": sum(r["taxEach"] * r["qty"] for r in closed),
        "avgRoi": (sum(rois) / len(rois)) if rois else None,
        "avgProfit": (total / len(closed)) if closed else None,
        "turnover": sum(r["cost"] for r in closed),
        "byDay": [{"day": d, "profit": p} for d, p in days],
        "byHour": by_hour, "byWeekday": by_wday, "holdTimes": holds,
        "equity": equity, "maxDrawdown": dd, "bestStreak": best_streak,
        "best": pick(best), "worst": pick(worst),
        "avgBuyEdge": (sum(be) / len(be)) if be else None,
        "avgSellEdge": (sum(se) / len(se)) if se else None,
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


# Portfolio -----------------------------------------------------------------------

def _sell_value(engine, iid, qty):
    """(value if sold at the instant-buy price after tax, quick sale value at instant-sell)."""
    live = engine.by_id.get(iid) or {}
    hi, lo = live.get("high"), live.get("low")
    tax = engine.tax
    val = qty * (hi - tax(hi, iid)) if hi else (qty * (lo - tax(lo, iid)) if lo else None)
    quick = qty * (lo - tax(lo, iid)) if lo else None
    return val, quick


def portfolio(db, engine, history_days=90):
    coins = int(db.kv_get("coins", 0) or 0)
    items = []
    tot_val = tot_quick = tot_cost = tot_chg = 0
    for h in db.q("SELECT * FROM holdings ORDER BY hid"):
        m = engine.mapping.get(h["item_id"], {})
        live = engine.by_id.get(h["item_id"]) or {}
        val, quick = _sell_value(engine, h["item_id"], h["qty"])
        cost = h["qty"] * h["cost_each"] if h["cost_each"] is not None else None
        c24 = live.get("chg24h")
        chg = (val * c24 / (1 + c24)) if (val is not None and c24 is not None and c24 > -1) else None
        items.append({"hid": h["hid"], "item_id": h["item_id"], "name": m.get("name", f"Item {h['item_id']}"),
                      "icon": m.get("icon"), "qty": h["qty"], "costEach": h["cost_each"], "note": h["note"],
                      "price": live.get("high"), "value": val, "quick": quick, "cost": cost,
                      "pnl": (val - cost) if (val is not None and cost is not None) else None,
                      "chg24h": c24, "chg24gp": chg, "added": h["added"]})
        tot_val += val or 0
        tot_quick += quick or 0
        tot_cost += cost or 0
        tot_chg += chg or 0
    flips = []
    flip_val = flip_cost = 0
    # Automatic flips are real trades whose items the plugin already counts in the Net worth tab.
    for f in db.q("SELECT * FROM flips WHERE sell_price IS NULL AND COALESCE(source, '') != 'auto'"):
        val, _ = _sell_value(engine, f["item_id"], f["qty"])
        m = engine.mapping.get(f["item_id"], {})
        cost = f["qty"] * f["buy_price"]
        flips.append({"fid": f["fid"], "item_id": f["item_id"], "name": m.get("name"), "icon": m.get("icon"),
                      "qty": f["qty"], "value": val, "cost": cost,
                      "pnl": (val - cost) if val is not None else None})
        flip_val += val or 0
        flip_cost += cost
    for it in items:
        it["share"] = (it["value"] / tot_val) if (tot_val and it["value"]) else None
    total = coins + tot_val + flip_val
    hist = db.q("SELECT * FROM networth WHERE ts >= ? ORDER BY ts", (int(time.time() - history_days * 86400),))
    return {"coins": coins, "holdings": items, "openFlips": flips,
            "totals": {"total": total, "holdings": tot_val, "quick": tot_quick + coins + flip_val,
                       "cost": tot_cost, "pnl": (tot_val - tot_cost) if tot_cost else None,
                       "flips": flip_val, "flipCost": flip_cost, "chg24": tot_chg},
            "history": hist}


def networth_snapshot(db, engine, now=None):
    """Record net worth once per hour (only when there is anything to track)."""
    p = portfolio(db, engine, history_days=0)
    t = p["totals"]
    if not p["holdings"] and not p["openFlips"] and not p["coins"]:
        return None
    ts = int((now or time.time()) // 3600 * 3600)
    db.run("INSERT OR REPLACE INTO networth (ts, total, holdings, flips, coins, cost) VALUES (?,?,?,?,?,?)",
           (ts, int(t["total"]), int(t["holdings"]), int(t["flips"]), int(p["coins"]),
            int(t["cost"] + t["flipCost"])))
    return ts


_QTY = r"(\d[\d,\.]*\s*[kmb]?)"
_LINE_PATTERNS = [
    re.compile(rf"^{_QTY}\s*x\s+(.+?)$", re.I),          # 2 x Abyssal whip
    re.compile(rf"^(.+?)\s*[x,:\t]\s*{_QTY}(?:\s*[,@\t]\s*{_QTY})?$", re.I),  # whip x 2 / whip, 2, 800k
    re.compile(rf"^{_QTY}\s+(.+?)$", re.I),              # 2 Abyssal whip
    re.compile(rf"^(.+?)\s+{_QTY}$", re.I),              # Abyssal whip 2
]


def parse_amount(s):
    s = str(s).strip().lower().replace(",", "").replace(" ", "")
    mult = 1
    if s and s[-1] in "kmb":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[s[-1]]
        s = s[:-1]
    return int(round(float(s) * mult))


def parse_holdings(text, by_name):
    """Parse pasted lines like '2 x Abyssal whip' or 'Shark, 500, 950' into holdings.

    `by_name` maps lower case item names to ids. Returns (parsed, problems).
    """
    parsed, problems = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        hit = None
        for k, pat in enumerate(_LINE_PATTERNS):
            m = pat.match(line)
            if not m:
                continue
            g = m.groups()
            if k in (0, 2):
                qty, name, cost = g[0], g[1], None
            else:
                name, qty = g[0], g[1]
                cost = g[2] if len(g) > 2 else None
            iid = by_name.get(name.strip().lower())
            if iid is None:
                continue
            try:
                hit = {"item_id": iid, "qty": parse_amount(qty),
                       "cost_each": parse_amount(cost) if cost else None}
            except ValueError:
                hit = None
            if hit:
                break
        if not hit and line.lower() in by_name:
            hit = {"item_id": by_name[line.lower()], "qty": 1, "cost_each": None}
        if hit and hit["qty"] > 0:
            parsed.append(hit)
        else:
            problems.append(line)
    return parsed, problems


# Account -------------------------------------------------------------------------

def _skills(data):
    return {s["name"]: s for s in data.get("skills", [])}


def _acts(data):
    return {a["name"]: a for a in data.get("activities", [])}


def account_view(db, player, mode, current):
    """Current stats plus XP and KC gained over 1 day / 7 days / all tracked time."""
    snaps = db.hiscore_snaps(player, mode)
    now = int(time.time())
    cur = _skills(current)

    def base_at(age):
        cands = [s for s in snaps if s["ts"] >= now - age]
        return cands[0]["data"] if cands else None

    bases = {"day": base_at(86400), "week": base_at(7 * 86400),
             "all": snaps[0]["data"] if snaps else None}
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
            bs = _skills(b) if b else None
            if bs and name in bs and bs[name].get("xp", -1) >= 0 and xp >= 0:
                row["gain_" + k] = xp - bs[name]["xp"]
        skills.append(row)
    acts = []
    for a in current.get("activities", []):
        score = a.get("score") or -1
        if score <= 0:
            continue
        row = dict(a)
        for k, b in bases.items():
            ba = _acts(b).get(a["name"]) if b else None
            if ba is not None:
                row["gain_" + k] = score - max(0, ba.get("score") or 0)
        acts.append(row)
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
    pricer = engine.pricer()
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
            # Cheapest ways to get there from the recipe table, at your current level.
            lvl = level_for_xp(xp, cap=99)
            methods = []
            if r["remaining"] > 0:
                for ev in recipes.xp_methods(pricer, g["skill"], level=lvl, db=db):
                    if ev["stale"] or ev["thin"]:
                        continue
                    methods.append({"name": ev["name"], "gpXp": ev["gpXp"], "level": ev["level"],
                                    "cost": -ev["gpXp"] * r["remaining"],
                                    "hours": (r["remaining"] / ev["xpHr"]) if ev.get("xpHr") else None})
                    if len(methods) >= 3:
                        break
            r["methods"] = methods
        out.append(r)
    return out


# Boss drops and dry streaks ------------------------------------------------------

def _current_kc(db, player, mode):
    snaps = db.hiscore_snaps(player, mode)
    if not snaps:
        return {}, {}
    first = {a["name"]: max(0, a.get("score") or 0) for a in snaps[0]["data"].get("activities", [])}
    last = {a["name"]: max(0, a.get("score") or 0) for a in snaps[-1]["data"].get("activities", [])}
    return last, first


def boss_view(db, engine, player, mode):
    """Drop log, loot value per boss and dry streak odds for the items you are chasing."""
    key = player.lower()
    kc_now, kc_first = _current_kc(db, player, mode)
    drops = db.q("SELECT * FROM drops WHERE player=? AND mode=? ORDER BY ts DESC, did DESC", (key, mode))
    out_drops = []
    bosses = {}
    for d in drops:
        live = engine.by_id.get(d["item_id"]) if d["item_id"] else None
        each = live.get("high") if live else None
        val = each * (d["qty"] or 1) if each else None
        m = engine.mapping.get(d["item_id"], {}) if d["item_id"] else {}
        out_drops.append(dict(d, value=val, icon=m.get("icon")))
        b = bosses.setdefault(d["boss"], {"boss": d["boss"], "drops": 0, "value": 0, "minKc": None})
        b["drops"] += 1
        b["value"] += val or 0
        if d["kc"]:
            b["minKc"] = d["kc"] if b["minKc"] is None else min(b["minKc"], d["kc"])
    for name, b in bosses.items():
        kc = kc_now.get(name)
        base = kc_first.get(name)
        if base is None and b["minKc"]:
            base = b["minKc"] - 1
        b["kc"] = kc
        tracked = (kc - base) if (kc is not None and base is not None) else None
        b["trackedKc"] = tracked
        b["gpPerKc"] = (b["value"] / tracked) if tracked else None
    chases = []
    for c in db.q("SELECT * FROM chase WHERE player=? AND mode=? ORDER BY cid", (key, mode)):
        got = [d for d in drops if d["boss"] == c["boss"] and d["item_name"].lower() == c["item_name"].lower()]
        last_kc = max((d["kc"] or 0) for d in got) if got else None
        kc = kc_now.get(c["boss"])
        if kc is None:
            kc = max([c["start_kc"] or 0] + [d["kc"] or 0 for d in drops if d["boss"] == c["boss"]])
        since = max(c["start_kc"] or 0, last_kc or 0)
        dry = max(0, kc - since)
        p = 1.0 / c["rate_n"] if c["rate_n"] else None
        chance_dry = (1 - p) ** dry if p else None
        chases.append(dict(c, kc=kc, dry=dry, got=len(got), expected=(dry * p) if p else None,
                           chanceDry=chance_dry,
                           luckier=(1 - chance_dry) if chance_dry is not None else None))
    boss_list = sorted(bosses.values(), key=lambda b: -b["value"])
    names = sorted(kc_now) if kc_now else []
    return {"drops": out_drops, "bosses": boss_list, "chases": chases, "activityNames": names,
            "kc": kc_now}
