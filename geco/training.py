"""Skill training: which method to use at each level, how long it takes and what it costs.

Methods come from the OSRS Wiki's training guides (see skillguides): level ranges, XP rates by
level where the guide has a table, cost per XP from the guide's own price formulas, and the
quests or levels a method needs. A plan switches method as levels unlock better ones and skips
methods the account cannot use yet (listing them with what they need). The small table below
fills gaps the guides leave (no rate stated, or no method at a level) and prices a few methods
live. Once you have trained a skill with the plugin running, your measured rate replaces both.
"""
import re
import time

from . import skillguides
from .skills import xp_for_level

# skill: [(method, min level, XP/h at that level, XP/h near 99, attention, cost spec or None)]
# cost spec: {"in": [(item, qty)], "out": [(item, qty)], "xp": XP per action}
METHODS = {
    "Attack": [("Sand crabs (AFK)", 1, 20_000, 60_000, "afk", None),
               ("Nightmare Zone", 60, 60_000, 110_000, "afk", None)],
    "Strength": [("Sand crabs (AFK)", 1, 20_000, 60_000, "afk", None),
                 ("Nightmare Zone", 60, 60_000, 110_000, "afk", None)],
    "Defence": [("Sand crabs (AFK)", 1, 20_000, 60_000, "afk", None),
                ("Nightmare Zone", 60, 60_000, 110_000, "afk", None)],
    "Hitpoints": [("Combat training (a third of combat XP)", 10, 10_000, 35_000, "afk", None)],
    "Ranged": [("Sand crabs (AFK)", 1, 20_000, 60_000, "afk", None),
               ("Nightmare Zone", 60, 60_000, 120_000, "afk", None)],
    "Prayer": [("Dragon bones at a gilded altar", 1, 350_000, 450_000, "high",
                {"in": [("Dragon bones", 1)], "out": [], "xp": 252})],
    "Magic": [("Splashing", 1, 12_000, 13_000, "afk", None),
              ("High Level Alchemy", 55, 75_000, 78_000, "medium",
               {"in": [("Nature rune", 1)], "out": [], "xp": 65})],
    "Runecraft": [("Guardians of the Rift", 27, 35_000, 60_000, "medium", None)],
    "Construction": [("Oak larders", 33, 200_000, 300_000, "high",
                      {"in": [("Oak plank", 8)], "out": [], "xp": 480}),
                     ("Mahogany tables", 52, 350_000, 500_000, "high",
                      {"in": [("Mahogany plank", 6)], "out": [], "xp": 840})],
    "Agility": [("Canifis rooftop course", 40, 15_000, 19_000, "medium", None),
                ("Seers' Village rooftop course", 60, 40_000, 47_000, "medium", None),
                ("Pollnivneach rooftop course", 70, 44_000, 50_000, "medium", None),
                ("Rellekka rooftop course", 80, 48_000, 55_000, "medium", None),
                ("Ardougne rooftop course", 90, 55_000, 62_000, "medium", None)],
    "Herblore": [("Prayer potions", 38, 200_000, 250_000, "high",
                  {"in": [("Ranarr potion (unf)", 1), ("Snape grass", 1)], "out": [("Prayer potion(3)", 1)], "xp": 87.5}),
                 ("Super restores", 63, 280_000, 340_000, "high",
                  {"in": [("Snapdragon potion (unf)", 1), ("Red spiders' eggs", 1)], "out": [("Super restore(3)", 1)], "xp": 142.5}),
                 ("Saradomin brews", 81, 380_000, 430_000, "high",
                  {"in": [("Toadflax potion (unf)", 1), ("Crushed nest", 1)], "out": [("Saradomin brew(3)", 1)], "xp": 180})],
    "Thieving": [("Fruit stalls", 25, 30_000, 45_000, "medium", None),
                 ("Ardougne knights", 55, 140_000, 220_000, "high", None),
                 ("Pyramid Plunder", 71, 180_000, 260_000, "high", None)],
    "Crafting": [("Unpowered orbs", 46, 80_000, 95_000, "medium",
                  {"in": [("Molten glass", 1)], "out": [("Unpowered orb", 1)], "xp": 52.5}),
                 ("Green d'hide bodies", 63, 300_000, 370_000, "medium",
                  {"in": [("Green dragon leather", 3)], "out": [("Green d'hide body", 1)], "xp": 186}),
                 ("Air battlestaves", 66, 320_000, 380_000, "medium",
                  {"in": [("Battlestaff", 1), ("Air orb", 1)], "out": [("Air battlestaff", 1)], "xp": 137.5})],
    "Fletching": [("Stringing maple longbows", 55, 150_000, 180_000, "medium",
                   {"in": [("Maple longbow (u)", 1), ("Bow string", 1)], "out": [("Maple longbow", 1)], "xp": 58.3}),
                  ("Stringing yew longbows", 70, 210_000, 240_000, "medium",
                   {"in": [("Yew longbow (u)", 1), ("Bow string", 1)], "out": [("Yew longbow", 1)], "xp": 75})],
    "Slayer": [("Slayer tasks", 1, 15_000, 60_000, "high", None)],
    "Hunter": [("Hunter rumours", 46, 45_000, 70_000, "medium", None),
               ("Grey chinchompas", 53, 55_000, 80_000, "medium", None),
               ("Red chinchompas", 63, 80_000, 110_000, "medium", None)],
    "Mining": [("Iron ore (power mining)", 15, 40_000, 60_000, "high", None),
               ("Motherlode Mine", 30, 25_000, 45_000, "afk", None),
               ("Granite", 45, 55_000, 100_000, "high", None)],
    "Smithing": [("Gold bars at Blast Furnace", 40, 280_000, 380_000, "high",
                  {"in": [("Gold ore", 1)], "out": [("Gold bar", 1)], "xp": 56.2})],
    "Fishing": [("Tempoross", 35, 40_000, 80_000, "medium", None),
                ("Barbarian fishing", 48, 40_000, 70_000, "high", None)],
    "Cooking": [("Jugs of wine", 35, 400_000, 490_000, "medium",
                 {"in": [("Jug of water", 1), ("Grapes", 1)], "out": [("Jug of wine", 1)], "xp": 200}),
                ("Sharks", 80, 200_000, 250_000, "afk",
                 {"in": [("Raw shark", 1)], "out": [("Shark", 1)], "xp": 210})],
    "Firemaking": [("Wintertodt", 50, 150_000, 300_000, "medium", None)],
    "Woodcutting": [("Willow trees", 30, 30_000, 50_000, "afk", None),
                    ("Teak trees", 35, 60_000, 110_000, "high", None)],
    "Farming": [("Tithe Farm", 34, 45_000, 110_000, "high", None)],
    "Sailing": [],
}

# skill: [(boost, levels, item to buy or None)]. Conservative figures; check the Wiki's
# temporary boosts page before relying on one for a quest.
BOOSTS = {
    "Agility": [("Summer pie", 5, "Summer pie"), ("Agility potion", 3, "Agility potion(4)")],
    "Herblore": [("Botanical pie", 4, "Botanical pie")],
    "Hunter": [("Hunter potion", 3, "Hunter potion(4)")],
    "Farming": [("Garden pie", 3, "Garden pie")],
    "Fishing": [("Admiral pie", 5, "Admiral pie"), ("Fishing potion", 3, "Fishing potion(4)")],
    "Crafting": [("Mushroom pie", 4, "Mushroom pie")],
    "Mining": [("Dwarven stout", 1, "Dwarven stout")],
    "Smithing": [("Dwarven stout", 1, "Dwarven stout")],
    "Slayer": [("Wild pie", 5, "Wild pie")],
    "Magic": [("Magic potion", 4, "Magic potion(4)")],
    "Attack": [("Super attack", 5, "Super attack(4)")],
    "Strength": [("Super strength", 5, "Super strength(4)")],
    "Defence": [("Super defence", 5, "Super defence(4)")],
    "Ranged": [("Ranging potion", 4, "Ranging potion(4)")],
}


def rate_at(m, level):
    """XP/h of a method at a level: straight line from its minimum level to 99."""
    lo_lv, lo, hi = m[1], m[2], m[3]
    f = 0 if level <= lo_lv else min(1.0, (level - lo_lv) / max(1, 99 - lo_lv))
    return lo + (hi - lo) * f


def gp_per_xp(m, price):
    """Cost per XP (negative is profit) from live prices: buy inputs at the instant-buy price,
    sell outputs at the instant-sell price after tax. None when an item has no price."""
    spec = m[5]
    if not spec:
        return 0.0
    cost = 0.0
    for name, q in spec["in"]:
        p = price(name, "buy")
        if p is None:
            return None
        cost += p * q
    for name, q in spec["out"]:
        p = price(name, "sell")
        if p is None:
            return None
        cost -= p * q
    return cost / spec["xp"]


def measured_rates(db, acct, days=30, now=None):
    """Your XP/h per skill while training it, from the XP log (gaps over 5 minutes ignored).
    Only skills with at least 30 minutes of training count."""
    now = now or time.time()
    rows = db.q("SELECT skill, t, xp FROM xp_log WHERE acct=? AND t>=? ORDER BY skill, t", (acct, int(now - days * 86400)))
    out, cur, prev = {}, None, None
    acc = {}
    for r in rows:
        if r["skill"] != cur:
            cur, prev = r["skill"], None
        if prev and r["xp"] and prev["xp"] and r["xp"] > prev["xp"] and r["t"] - prev["t"] <= 300:
            a = acc.setdefault(cur, [0, 0])
            a[0] += r["xp"] - prev["xp"]
            a[1] += r["t"] - prev["t"]
        prev = r
    for sk, (xp, secs) in acc.items():
        if secs >= 1800:
            out[sk] = {"xph": xp / secs * 3600, "hours": secs / 3600}
    return out


GATHER = {"Agility", "Thieving", "Hunter", "Mining", "Fishing", "Woodcutting", "Slayer", "Attack", "Strength",
          "Defence", "Hitpoints", "Sailing"}


GENERIC = {"course", "courses", "rooftop", "agility", "training", "trees", "tree", "runes", "rune", "with", "from",
           "making", "crafting", "smithing", "cooking", "fishing", "mining", "level", "levels", "high"}


def _words(name):
    return {w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) > 3 and w not in GENERIC}


def candidates(skill, guides=None):
    """Every method for a skill: the Wiki's, then the table's for anything the Wiki lacks."""
    out = []
    for m in (guides or {}).get("methods", {}).get(skill, []):
        # A flat rate quoted for a "1-99" method is the rate near the top (the best garden, the
        # best tree), so it is scaled down at low levels.
        wide = not m.get("curve") and m["lo"] <= 20 and m["hi"] - m["lo"] >= 50 and skill != "Prayer"
        out.append({"name": m["name"], "lo": m["lo"], "hi": m["hi"], "curve": m.get("curve") or [], "wide": wide,
                    "typical": m.get("typical"), "gpPerXp": m.get("gpPerXp"), "req": m.get("req") or {},
                    "items": m.get("items") or [], "summary": m.get("summary") or "", "url": m.get("url"),
                    "source": "wiki", "fastest": m.get("fastest")})
    for t in METHODS.get(skill, []):
        twin = next((c for c in out if c["source"] == "wiki" and _words(c["name"]) & _words(t[0])), None)
        if twin:
            twin["spec"] = t  # live cost for a Wiki method the table also knows
            continue
        out.append({"name": t[0], "lo": t[1], "hi": 99, "curve": [], "typical": None, "gpPerXp": None, "req": {},
                    "items": [], "summary": "", "url": None, "source": "table", "spec": t, "attention": t[4]})
    return out


def method_rate(c, level):
    """XP/h of a candidate at a level: the best row of its table you can do, else its stated rate."""
    if c["curve"]:
        ok = [r for lv, r in c["curve"] if lv <= level]
        return max(ok) if ok else c["curve"][0][1]
    if c["typical"]:
        return c["typical"] * (max(0.3, min(1.0, level / 80)) if c.get("wide") else 1.0)
    if c.get("spec"):
        return rate_at(c["spec"], level)
    return None


def method_cost(c, skill, price):
    """gp per XP: live from the table's recipe when known, else the Wiki's figure (GE prices when
    the guide was read), else 0 for gathering skills, else None."""
    if c.get("spec") and c["spec"][5]:
        g = gp_per_xp(c["spec"], price)
        if g is not None:
            return g
    if c.get("gpPerXp") is not None:
        return c["gpPerXp"]
    return 0.0 if skill in GATHER else None


def locked(c, levels, quest_state):
    """What a candidate still needs on this account: [text], empty when usable."""
    need = []
    for q in (c.get("req") or {}).get("quests", []):
        st = quest_state(q["quest"]) if quest_state else None
        if st is None:
            continue  # unknown account: do not hide methods
        if st < (1 if q.get("partial") else 2):
            need.append(("Start " if q.get("partial") else "") + q["quest"])
    for sk, lv in ((c.get("req") or {}).get("skills") or {}).items():
        have = (levels.get(sk) or (None,))[0]
        if have is not None and have < lv:
            need.append(f"{lv} {sk}")
    return need


def plan_skill(skill, level, xp, target, price, mode="fastest", measured=None, members=True,
               guides=None, levels=None, quest_state=None):
    """How to get `skill` from (level, xp) to `target`: one or more stretches, each with the best
    method you can use there, plus hours and gp."""
    start_xp = xp if xp is not None else xp_for_level(level)
    need = xp_for_level(target) - start_xp
    if need <= 0:
        return None
    levels = levels or {}
    cands = candidates(skill, guides)
    for c in cands:
        c["needs"] = locked(c, levels, quest_state)
        c["cost"] = method_cost(c, skill, price)

    def pick(lv):
        ok = [c for c in cands if c["lo"] <= lv and not c["needs"] and method_rate(c, lv)]
        if not ok:
            return None
        # A guide's level range is where it recommends the method; past it, prefer others.
        score = {id(c): method_rate(c, lv) * (1.0 if lv <= c["hi"] else 0.85) for c in ok}
        best = max(ok, key=lambda c: score[id(c)])
        if mode == "cheapest":
            cheap = [c for c in ok if score[id(c)] >= score[id(best)] * 0.4 and c["cost"] is not None]
            if cheap:
                best = min(cheap, key=lambda c: (round(c["cost"], 1), -score[id(c)]))
        return best

    segs = []
    for lv in range(level, target):
        lo_xp = max(start_xp, xp_for_level(lv))
        part = xp_for_level(lv + 1) - lo_xp
        if part <= 0:
            continue
        c = pick(lv)
        rate = method_rate(c, lv) if c else None
        if segs and segs[-1]["c"] is c:
            sg = segs[-1]
            sg["to"] = lv + 1
            sg["xp"] += part
            sg["hours"] = sg["hours"] + part / rate if rate and sg["hours"] is not None else None
        else:
            segs.append({"c": c, "from": lv, "to": lv + 1, "xp": part, "hours": part / rate if rate else None})
    if not any(sg["c"] for sg in segs):
        return {"skill": skill, "from": level, "to": target, "xp": need, "method": None, "hours": None, "gp": None,
                "segments": [], "options": [], "note": "No training method found for this level yet"}
    out_segs = []
    for sg in segs:
        c = sg["c"]
        cost = c["cost"] if c else None
        out_segs.append({"method": c["name"] if c else None, "from": sg["from"], "to": sg["to"], "xp": sg["xp"],
                         "hours": sg["hours"], "xph": sg["xp"] / sg["hours"] if sg["hours"] else None,
                         "gp": sg["xp"] * cost if cost is not None else None, "gpPerXp": cost,
                         "url": c.get("url") if c else None, "items": (c.get("items") or [])[:12] if c else [],
                         "summary": c.get("summary") if c else "", "source": c["source"] if c else None,
                         "notes": ((c.get("req") or {}).get("notes") or [])[:3] if c else []})
    hours = sum(sg["hours"] for sg in out_segs if sg["hours"]) if all(sg["hours"] for sg in out_segs) else None
    source = "wiki" if all(sg["source"] == "wiki" for sg in out_segs) else "table"
    if measured and measured.get("xph") and measured["xph"] > 1000:
        scale = (need / measured["xph"]) / hours if hours else None
        hours = need / measured["xph"]
        for sg in out_segs:
            if scale and sg["hours"]:
                sg["hours"] *= scale
                sg["xph"] = sg["xp"] / sg["hours"]
        source = "yours"
    costs = [sg["gp"] for sg in out_segs]
    gp = sum(costs) if all(g is not None for g in costs) else None
    first = out_segs[0]
    opts = []
    for c in cands:
        r = method_rate(c, max(level, c["lo"]))
        if not r:
            continue
        opts.append({"method": c["name"], "xph": r, "gpPerXp": c["cost"], "minLevel": c["lo"], "maxLevel": c["hi"],
                     "needs": c["needs"], "url": c.get("url"), "source": c["source"],
                     "usable": c["lo"] <= level and not c["needs"]})
    used = {sg["method"] for sg in out_segs}
    opts = [o for o in opts if o["method"] not in used and o["minLevel"] < target]
    opts.sort(key=lambda o: (not o["usable"], -o["xph"]))
    return {"skill": skill, "from": level, "to": target, "xp": need, "method": first["method"],
            "xph": need / hours if hours else None, "rateSource": source, "hours": hours, "gp": gp,
            "gpPerXp": gp / need if gp is not None else None, "attention": None,
            "segments": out_segs, "options": opts[:6], "url": first["url"]}


def best_boost(skill, gap, level=None, price=None, guides=None):
    """(boost, levels, item to buy) for the cheapest visible boost that covers `gap` levels, from
    the Wiki's boost tables (items sold on the GE, nothing that needs an unlock), else the table."""
    if guides and price:
        fits = []
        for b in (guides.get("boosts") or {}).get(skill, []):
            amount = skillguides.boost_at(b, level or 1)
            if amount < gap or re.search(r"(?i)\brequires?\b|cannot be|untradeable", b.get("note") or ""):
                continue
            p = price(b["item"], "buy")
            if p:
                fits.append((amount, p, b["item"]))
        if fits:
            amount, p, item = min(fits, key=lambda f: (f[1], f[0]))
            return (item, amount, item)
    fits = [b for b in BOOSTS.get(skill, []) if b[1] >= gap]
    return min(fits, key=lambda b: b[1]) if fits else None
