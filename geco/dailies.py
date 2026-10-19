"""Daily and weekly tasks worth doing: what each is worth today, how long it takes, and which
your diaries and quests allow. Quantities come from the OSRS Wiki's "Repeatable events" page
(Oct 2026); values are live GE prices. Daily tasks reset at 00:00 UTC; Tears of Guthix opens 7 days
after your last visit. You tick what you've done (the game doesn't tell the plugin).
Display only.
"""
import re
import time

WIKI = "https://oldschool.runescape.wiki/w/"
DIARY_TIERS = ["easy", "medium", "hard", "elite"]
HERB_BOX = 6575  # the Wiki's average herb box value on Oct 19 2026, until the weekly refresh runs

# qty: a fixed amount, or (diary region, [none, easy, medium, hard, elite]) amounts by the highest
# tier done. value(q, price) is gp for the day; None when it varies.
TASKS = [
    {"key": "battlestaves", "title": "Buy Zaff's battlestaves", "where": "Zaff's Superior Staffs, Varrock",
     "minutes": 2.5, "qty": ("Varrock", [5, 15, 30, 60, 120]), "item": "Battlestaff",
     "value": lambda q, p: q * (p("Battlestaff", "sell") - 7000), "note": "7,000 each, noted",
     "url": WIKI + "Zaff"},
    {"key": "sand", "title": "Bert's buckets of sand", "where": "Bert, Yanille", "minutes": 2.0, "qty": 84,
     "quest": "The Hand in the Sand", "item": "Bucket of sand",
     "value": lambda q, p: q * p("Bucket of sand", "sell"), "auto": ("Ardougne", 3),
     "note": "delivered to your bank on login after the elite Ardougne diary", "url": WIKI + "Bert"},
    {"key": "essence", "title": "Free pure essence from Wizard Cromperty", "where": "East Ardougne", "minutes": 1.5,
     "qty": ("Ardougne", [0, 0, 100, 150, 250]), "item": "Pure essence",
     "value": lambda q, p: q * p("Pure essence", "sell"), "url": WIKI + "Wizard_Cromperty"},
    {"key": "flax", "title": "Flax to bow strings at the flax keeper", "where": "Seers' Village", "minutes": 1.5,
     "qty": ("Kandarin", [0, 30, 60, 120, 250]), "item": "Bow string",
     "value": lambda q, p: q * (p("Bow string", "sell") - p("Flax", "buy")),
     "note": "bring the flax noted", "url": WIKI + "Flax_keeper"},
    {"key": "dynamite", "title": "Free dynamite from Thirus", "where": "Lovakengj", "minutes": 2.0,
     "qty": ("Kourend & Kebos", [0, 0, 20, 40, 80]), "item": "Dynamite",
     "value": lambda q, p: q * p("Dynamite", "sell"), "url": WIKI + "Thirus"},
    {"key": "ogre_arrows", "title": "Free ogre arrows from Rantz", "where": "Feldip Hills", "minutes": 3.0,
     "qty": ("Western Provinces", [0, 25, 50, 100, 150]), "item": "Ogre arrow",
     "value": lambda q, p: q * p("Ogre arrow", "sell"), "url": WIKI + "Rantz"},
    {"key": "herb_boxes", "title": "Herb boxes from Nightmare Zone", "where": "Nightmare Zone reward chest",
     "minutes": 2.0, "qty": 15, "value": lambda q, p: q * p("@herb box", "sell"),
     "note": "9,500 NMZ points each", "url": WIKI + "Herb_box"},
    {"key": "alchs", "title": "Free alchemy casts on the Explorer's ring", "where": "anywhere", "minutes": 1.0,
     "qty": ("Lumbridge & Draynor", [0, 30, 30, 30, 30]), "levels": {"Magic": 21}, "item": "Nature rune",
     "value": lambda q, p: q * p("Nature rune", "buy"), "note": "saves 30 nature runes; high alchemy with the elite ring",
     "url": WIKI + "Explorer%27s_ring"},
    {"key": "runes", "title": "Free runes from Lundail", "where": "Mage Arena bank", "minutes": 3.0,
     "qty": ("Wilderness", [0, 40, 80, 120, 200]), "value": None, "note": "random runes, value varies",
     "url": WIKI + "Lundail"},
    {"key": "miscellania", "title": "Top up Miscellania's coffers", "where": "Miscellania", "minutes": 2.0, "qty": 1,
     "quest": "Throne of Miscellania", "value": None,
     "note": "keep 500k (750k after Royal Trouble) in the coffers for the most resources",
     "url": WIKI + "Managing_Miscellania"},
    {"key": "tears", "title": "Tears of Guthix", "where": "Lumbridge Swamp caves", "minutes": 4.0, "qty": 1,
     "quest": "Tears of Guthix", "period": "weekly", "value": None,
     "note": "XP in your lowest skill; needs 1 quest point or 100k XP since last time",
     "url": WIKI + "Tears_of_Guthix_(minigame)"},
]


def last_reset(now):
    return int(now // 86400 * 86400)


def _done_key(acct):
    return f"dailies_done:{acct or '*'}"


def mark(db, acct, key, done, now=None):
    d = db.kv_get(_done_key(acct)) or {}
    if done:
        d[key] = int(now or time.time())
    else:
        d.pop(key, None)
    db.kv_set(_done_key(acct), d)
    return d


def _quantity(task, diaries):
    """(qty, highest tier done or None, the next tier's extra or None)."""
    q = task["qty"]
    if not isinstance(q, tuple):
        return q, None, None
    region, amounts = q
    tiers = diaries.get(region)
    if tiers is None:
        return amounts[0], None, None
    done = 0
    for i in range(4):
        if tiers[i]:
            done = i + 1
    nxt = next(((DIARY_TIERS[i - 1], amounts[i] - amounts[done]) for i in range(done + 1, 5) if amounts[i] > amounts[done]), None)
    return amounts[done], done, nxt


def plan(db, engine, acct=None, now=None, diaries=None, quests_done=()):
    """Every task with its value today, gp per minute, status and what unlocks more. `diaries`
    and `quests_done` try out unlocks you don't have yet."""
    from . import account, planner
    now = now or time.time()
    acct = acct or planner.main_account(db)
    prog = account.progress(db, acct) if acct else {"quests": {}, "levels": {}, "known": False}
    if quests_done:
        prog = dict(prog, quests=dict(prog["quests"], **{q: 2 for q in quests_done}))
    diaries = diaries if diaries is not None else account.diaries(db, acct)
    done = db.kv_get(_done_key(acct)) or {}
    herb_box = (db.kv_get("dailies_wiki") or {}).get("herbBox") or HERB_BOX
    find, _ = planner._price_fn(engine)

    def price(name, side):
        if name == "@herb box":
            return herb_box
        iid = find(name)
        r = engine.by_id.get(iid) if iid else None
        if not r:
            return None
        hi, lo = r.get("high"), r.get("low")
        if hi and lo and hi > 2 * lo:
            hi = lo  # a lone stale trade far from the other side
        p = (lo or hi) if side == "sell" else (hi or lo)
        if p and side == "sell":
            p -= engine.tax(p, iid)
        return p

    class Missing(Exception):
        pass

    def strict(name, side):
        v = price(name, side)
        if v is None:
            raise Missing(name)
        return v

    def worth(task, q):
        try:
            return task["value"](q, strict)
        except Missing:
            return None  # no price yet

    reset = last_reset(now)
    rows = []
    for t in TASKS:
        qty, tier, nxt = _quantity(t, diaries)
        needs = []
        if t.get("quest") and prog["quests"]:
            st = {k.lower(): v for k, v in prog["quests"].items()}.get(t["quest"].lower(), 0)
            if st < 2:
                needs.append(t["quest"])
        for sk, lv in (t.get("levels") or {}).items():
            have = (prog["levels"].get(sk) or [None])[0]
            if have is not None and have < lv:
                needs.append(f"{lv} {sk}")
        if isinstance(t["qty"], tuple) and not qty:
            region, amounts = t["qty"]
            first = next(i for i, a in enumerate(amounts) if a)
            needs.append(f"{DIARY_TIERS[first - 1]} {region} diary")
        value = worth(t, qty) if t["value"] and qty else None
        auto = t.get("auto")
        minutes = 0.0 if auto and (diaries.get(auto[0]) or [0, 0, 0, 0])[auto[1]] else t["minutes"]
        period = t.get("period", "daily")
        when = done.get(t["key"])
        if period == "weekly":
            opens = (when // 86400 * 86400 + 7 * 86400) if when else None
            is_done = bool(opens and now < opens)
        else:
            opens = reset + 86400
            is_done = bool(when and when >= reset)
        next_value = None
        if nxt and t["value"]:
            more = worth(t, qty + nxt[1])
            next_value = more - (value or 0) if more is not None else None
        rows.append({"key": t["key"], "title": t["title"], "where": t["where"], "period": period, "qty": qty,
                     "item": t.get("item"), "value": value, "minutes": minutes,
                     "varies": t["value"] is None,
                     "perMinute": (value / minutes) if value and minutes else None,
                     "available": not needs, "needs": needs, "done": is_done, "doneAt": when,
                     "resets": opens, "note": t.get("note"), "url": t["url"], "auto": minutes == 0.0,
                     "diaryKnown": bool(diaries) or not isinstance(t["qty"], tuple),
                     "next": {"tier": nxt[0], "region": t["qty"][0], "extra": nxt[1], "value": next_value} if nxt else None})
    rows.sort(key=lambda r: (not r["available"], r["done"], -(r["perMinute"] or 0), -(r["value"] or 0)))
    todo = [r for r in rows if r["available"] and not r["done"]]
    return {"acct": acct, "now": int(now), "reset": reset + 86400, "tasks": rows, "diariesKnown": bool(diaries),
            "todo": {"value": sum(r["value"] or 0 for r in todo), "minutes": sum(r["minutes"] for r in todo),
                     "count": len(todo)},
            "source": "OSRS Wiki, Repeatable events (CC BY-NC-SA 3.0)", "url": WIKI + "Repeatable_events"}


def refresh(db, client, max_age=7 * 86400):
    """The herb box's average value, from the Wiki's own drop calculator (one request a week)."""
    d = db.kv_get("dailies_wiki") or {}
    if d.get("fetched") and time.time() - d["fetched"] < max_age:
        return 0
    r = client.wiki_api({"action": "expandtemplates", "prop": "wikitext",
                         "text": "{{Average drop value|mob=herb box|killname=opening}}"})
    m = re.search(r">([\d,]+)<", r.get("expandtemplates", {}).get("wikitext", ""))
    if m:
        db.kv_set("dailies_wiki", {"fetched": int(time.time()), "herbBox": int(m.group(1).replace(",", ""))})
        return 1
    return 0


def short_gp(n):
    n = float(n or 0)
    return f"{n / 1e6:.2f}m" if abs(n) >= 1e6 else f"{n / 1e3:.0f}k" if abs(n) >= 1e4 else f"{n:,.0f}"


def reset_notice(db, engine, now=None):
    """(key, message) once a day after the reset, for the tasks worth the most, or None."""
    now = now or time.time()
    if now - last_reset(now) > 3 * 3600:
        return None
    p = plan(db, engine, now=now)
    worth = [r for r in p["tasks"] if r["available"] and not r["done"] and (r["value"] or 0) > 0 and r["period"] == "daily"]
    if not worth:
        return None
    top = ", ".join(f"{r['title'].split(' from ')[0]} ({short_gp(r['value'])})" for r in worth[:3])
    more = f" and {len(worth) - 3} more" if len(worth) > 3 else ""
    total = sum(r["value"] for r in worth)
    mins = sum(r["minutes"] for r in worth)
    return (("dailies", last_reset(now)), f"Daily reset: {top}{more}. About {short_gp(total)} for {mins:.0f} min.")
