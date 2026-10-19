"""Skill training: methods with typical XP rates, their cost priced live from the GE, temporary
boosts, and your own measured XP rates from the plugin's XP log.

The rates are rough figures in the spirit of the OSRS Wiki's training guides (rates vary a lot
with gear, diaries and attention), so each method shows "about". Once you have trained a skill
with the plugin running, your measured rate replaces the table's.
"""
import time

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


def plan_skill(skill, level, xp, target, price, mode="fastest", measured=None, members=True):
    """How to get `skill` from (level, xp) to `target`: the method, hours and gp."""
    need = xp_for_level(target) - (xp if xp is not None else xp_for_level(level))
    if need <= 0:
        return None
    options = []
    for m in METHODS.get(skill, []):
        if m[1] > level:
            continue
        mid = (level + target) / 2
        r = rate_at(m, mid)
        g = gp_per_xp(m, price)
        options.append({"method": m[0], "xph": r, "gpPerXp": g, "attention": m[4], "minLevel": m[1]})
    if not options:
        return {"skill": skill, "from": level, "to": target, "xp": need, "method": None, "hours": None, "gp": None,
                "note": "No training method in the table for this level yet"}
    fastest = max(options, key=lambda o: o["xph"])
    if mode == "cheapest":
        ok = [o for o in options if o["xph"] >= fastest["xph"] * 0.4 and o["gpPerXp"] is not None]
        pick = min(ok, key=lambda o: o["gpPerXp"]) if ok else fastest
    else:
        pick = fastest
    xph = pick["xph"]
    source = "table"
    if measured and measured.get("xph") and measured["xph"] > 1000:
        xph, source = measured["xph"], "yours"
    hours = need / xph
    gp = need * pick["gpPerXp"] if pick["gpPerXp"] is not None else None
    return {"skill": skill, "from": level, "to": target, "xp": need, "method": pick["method"], "xph": xph,
            "rateSource": source, "hours": hours, "gp": gp, "gpPerXp": pick["gpPerXp"], "attention": pick["attention"],
            "options": sorted(options, key=lambda o: -o["xph"])}


def best_boost(skill, gap):
    """The smallest boost that covers a gap of `gap` levels, or None."""
    fits = [b for b in BOOSTS.get(skill, []) if b[1] >= gap]
    return min(fits, key=lambda b: b[1]) if fits else None
