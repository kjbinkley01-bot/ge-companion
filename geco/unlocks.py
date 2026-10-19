"""Best next unlocks: which quest, diary tier or item would help your account most, measured on
what the app already plans for you.

Each candidate is tried on a copy of your account: the farming routine is planned again (patches
it opens, teleports it gives, yield or XP bonuses, your own minutes per stop) and the dailies are
valued again (bigger diary amounts, tasks a quest opens). The difference, per week, is what the
unlock is worth: gp, Farming XP and minutes of runs. Quests also get the planner's estimate of
how long they take with everything still needed before them. Display only.
"""
import time

WIKI = "https://oldschool.runescape.wiki/w/"
DIARIES = ["Ardougne", "Desert", "Falador", "Fremennik", "Kandarin", "Karamja", "Kourend & Kebos",
           "Lumbridge & Draynor", "Morytania", "Varrock", "Western Provinces", "Wilderness"]
TIERS = ["easy", "medium", "hard", "elite"]
# The reward item each diary tier gives (worn or carried, many of them teleports).
DIARY_ITEMS = {"Ardougne": "Ardougne cloak", "Desert": "Desert amulet", "Falador": "Falador shield",
               "Fremennik": "Fremennik sea boots", "Kandarin": "Kandarin headgear", "Karamja": "Karamja gloves",
               "Kourend & Kebos": "Rada's blessing", "Lumbridge & Draynor": "Explorer's ring",
               "Morytania": "Morytania legs", "Varrock": "Varrock armour", "Western Provinces": "Western banner",
               "Wilderness": "Wilderness sword"}
DIARY_URL = {"Kourend & Kebos": "Kourend_%26_Kebos_Diary", "Lumbridge & Draynor": "Lumbridge_%26_Draynor_Diary",
             "Western Provinces": "Western_Provinces_Diary"}
# Quests that matter to runs or dailies, and items they leave you with.
QUESTS = {"Fairytale II - Cure a Queen": [], "Fairytale I - Growing Pains": ["Magic secateurs"],
          "Tree Gnome Village": [], "Lunar Diplomacy": [], "Ghosts Ahoy": ["Ectophial"], "Children of the Sun": [],
          "Making Friends with My Arm": [], "My Arm's Big Adventure": [], "Plague City": [], "Watchtower": [],
          "Eadgar's Ruse": [], "Song of the Elves": [], "Mourning's End Part I": [], "Bone Voyage": [],
          "Priest in Peril": [], "The Fremennik Trials": [], "The Great Brain Robbery": [], "Lost City": ["Dramen staff"],
          "The Hand in the Sand": [], "Throne of Miscellania": [], "Tears of Guthix": []}
_cache = {}


def _week(routine, daily):
    """gp counts only runs that make money (a run done for XP costs what it costs either way)."""
    d = routine["perDay"]
    earn = sum(max(0.0, g["perDay"]["profit"]) for g in routine["groups"])
    return {"gp": 7 * (earn + daily), "xp": 7 * d["xp"], "minutes": 7 * d["minutes"]}


def _farm(ctx, mode):
    from . import farming
    plans = {r["key"]: r for r in [farming.plan_run(k, ctx, mode) for k in farming.RUNS] if r["patches"]}
    birds = farming.plan_birdhouses(ctx, mode)
    if birds.get("usable"):
        plans["birdhouse"] = birds
    return farming.routine(plans, mode)


def _daily_value(db, engine, acct, diaries, quests_done=()):
    from . import dailies
    p = dailies.plan(db, engine, acct, diaries=diaries, quests_done=quests_done)
    return sum(r["value"] or 0 for r in p["tasks"] if r["available"] and r["period"] == "daily")


def candidates(ctx, diaries):
    """[(kind, label, apply(ctx, diaries) -> (diaries, quests_done))] for things not done yet."""
    from . import farming
    out = []
    known = bool(diaries)
    for region in DIARIES:
        tiers = diaries.get(region) or [0, 0, 0, 0]
        if not known:
            break
        nxt = next((i for i in range(4) if not tiers[i]), None)
        if nxt is None:
            continue

        def apply(c, d, region=region, nxt=nxt):
            d = dict(d)
            d[region] = [1 if i <= nxt else (d.get(region) or [0] * 4)[i] for i in range(4)]
            for key, (reg, tier) in farming.DIARY_UNLOCKS.items():
                if reg == region and tier <= nxt:
                    c.unlocks.add(key)
            for k in range(1, nxt + 2):
                c.virtual.add(f"{DIARY_ITEMS[region]} {k}".lower())
            return d, ()
        out.append(("diary", f"{TIERS[nxt].title()} {region} diary", apply,
                    WIKI + DIARY_URL.get(region, region + "_Diary"), {"region": region, "tier": TIERS[nxt]}))
    for q, items in QUESTS.items():
        if ctx.quest(q) is not False:
            continue

        def apply(c, d, q=q, items=items):
            c.states[farming.norm(q)] = 2
            for i in items:
                c.virtual.add(i.lower())
            return d, (q,)
        out.append(("quest", q, apply, WIKI + q.replace(" ", "_"), {"quest": q}))
    from_quests = {i for q, items in QUESTS.items() if ctx.quest(q) is False for i in items}
    for item, why in (("Magic secateurs", "10% more herbs, allotments and hops"),
                      ("Bottomless compost bucket", "one slot for all your compost")):
        if not ctx.has(item) and item not in from_quests:
            out.append(("item", item, lambda c, d, item=item: (c.virtual.add(item.lower()), (d, ()))[1],
                        WIKI + item.replace(" ", "_"), {"why": why}))
    return out


def rank(db, engine, acct=None, mode="value", ttl=1800, top_quests=8):
    """Every candidate with its weekly gp, XP and minutes compared with now, best first by
    `mode`: "value" (gp), "xp" (Farming XP) or "time" (minutes of runs saved)."""
    from . import account, farming, planner
    acct = acct or planner.main_account(db)
    key = (acct, mode, int(time.time() // ttl))
    if key in _cache:
        return _cache[key]
    ctx = farming.Context(db, engine, acct)
    diaries = account.diaries(db, acct)
    fmode = "xp" if mode == "xp" else "value"
    base_r = _farm(ctx, fmode)
    base = _week(base_r, _daily_value(db, engine, acct, diaries))
    rows = []
    for kind, label, apply, url, extra in candidates(ctx, diaries):
        c = ctx.clone()
        d2, qs = apply(c, diaries)
        r = _farm(c, fmode)
        w = _week(r, _daily_value(db, engine, acct, d2, qs))
        delta = {k: w[k] - base[k] for k in w}
        opened = []
        for g in r["groups"]:
            bg = next((x for x in base_r["groups"] if x["group"] == g["group"]), None)
            if not bg or bg["runs"] != g["runs"]:
                opened.append(g["group"])
        if abs(delta["gp"]) < 1 and abs(delta["xp"]) < 1 and abs(delta["minutes"]) < 0.5:
            continue
        rows.append(dict({"kind": kind, "label": label, "url": url, "gp": delta["gp"], "xp": delta["xp"],
                          "minutes": delta["minutes"], "changes": opened}, **extra))
    # Best first by what you asked for: gp, Farming XP, or minutes of runs saved.
    if mode == "xp":
        rows.sort(key=lambda r: (-r["xp"], -r["gp"]))
    elif mode == "time":
        rows.sort(key=lambda r: (r["minutes"], -r["gp"]))
        rows = [r for r in rows if r["minutes"] < -0.5] + [r for r in rows if r["minutes"] >= -0.5]
    else:
        rows.sort(key=lambda r: (-r["gp"], r["minutes"]))
    # Quest length, with what has to be done first, for the best few.
    n = 0
    for r in rows:
        if r["kind"] == "quest" and n < top_quests:
            n += 1
            try:
                p = planner.build(db, engine, {}, {"type": "quest", "name": r["quest"]}, acct=acct)
                r["hours"] = p["hours"]["total"]
                r["questsFirst"] = max(0, p["counts"]["quests"] - 1)
                r["skills"] = p["counts"]["skills"]
            except Exception:  # noqa: BLE001 - a quest the planner can't place still shows
                pass
    out = {"acct": acct, "mode": mode, "diariesKnown": bool(diaries), "base": base, "rows": rows,
           "note": "Per week: farming routine plus dailies. XP is Farming XP from runs."}
    _cache.clear()
    _cache[key] = out
    return out
