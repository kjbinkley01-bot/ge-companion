"""Slayer: your task as the plugin reports it, task history, and the block and skip advisor."""
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS slayer_task (acct TEXT NOT NULL, assigned INTEGER NOT NULL, task TEXT, area TEXT,
    amount INTEGER, master INTEGER, streak INTEGER, done INTEGER, finished INTEGER DEFAULT 0,
    PRIMARY KEY (acct, assigned));
"""


def apply(db, acct, t, e):
    """The latest Slayer state, and the task log: a new task opens a row, a finished one
    (count down to 0) closes it as finished, a task replaced by another (a skip) closes it unfinished."""
    db.kv_set(f"slayer:{acct}", {"t": t, "task": e.get("task"), "area": e.get("area"), "count": e.get("count"),
                                 "original": e.get("original"), "master": e.get("master"), "points": e.get("points"),
                                 "streak": e.get("streak"), "wildyStreak": e.get("wildyStreak"),
                                 "blocked": e.get("blocked") or []})
    cur = db.one("SELECT * FROM slayer_task WHERE acct=? AND done IS NULL ORDER BY assigned DESC LIMIT 1", (acct,))
    count, task = e.get("count") or 0, e.get("task")
    if count > 0 and task:
        if cur and cur["task"] == task and cur["amount"] == e.get("original"):
            return
        if cur:
            db.run("UPDATE slayer_task SET done=?, finished=0 WHERE acct=? AND assigned=?", (t, acct, cur["assigned"]))
        db.run("INSERT OR REPLACE INTO slayer_task (acct, assigned, task, area, amount, master, streak) VALUES (?,?,?,?,?,?,?)",
               (acct, t, task, e.get("area"), e.get("original"), e.get("master"), e.get("streak")))
    elif cur:
        db.run("UPDATE slayer_task SET done=?, finished=1 WHERE acct=? AND assigned=?", (t, acct, cur["assigned"]))


def state(db, acct):
    return db.kv_get(f"slayer:{acct}") if acct else None


def history(db, acct, days=180, now=None):
    """Finished tasks with minutes taken and Slayer XP gained (from the XP log)."""
    now = now or int(time.time())
    out = []
    for r in db.q("SELECT * FROM slayer_task WHERE acct=? AND finished=1 AND assigned>? ORDER BY assigned",
                  (acct, now - days * 86400)):
        xp = db.q("SELECT MIN(xp) AS a, MAX(xp) AS b FROM xp_log WHERE acct=? AND skill='Slayer' AND t BETWEEN ? AND ?",
                  (acct, r["assigned"], r["done"]))
        gained = (xp[0]["b"] - xp[0]["a"]) if xp and xp[0]["a"] is not None else None
        out.append(dict(r, minutes=(r["done"] - r["assigned"]) / 60, xp=gained))
    return out


# Wiki data -------------------------------------------------------------------------------------
# Masters: points per task, 10th, 50th, 100th, 250th, 1,000th (OSRS Wiki "Slayer reward point",
# Oct 2026), the cost to block a task with them (Aug 2025 rework), the combat and Slayer levels
# they need, and whether their assignment table lives on a "/Slayer assignments" subpage.
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

VERSION = 1
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slayer_snapshot.json")
WIKI = "https://oldschool.runescape.wiki/w/"
MASTERS = {
    "Turael": {"points": [0, 0, 0, 0, 0, 0], "block": 40, "combat": 3, "sub": True},
    "Mazchna": {"points": [6, 30, 90, 150, 210, 300], "block": 50, "combat": 20, "sub": True},
    "Vannaka": {"points": [8, 40, 120, 200, 280, 400], "block": 60, "combat": 40, "sub": False},
    "Chaeldar": {"points": [10, 50, 150, 250, 350, 500], "block": 70, "combat": 70, "sub": False},
    "Konar quo Maten": {"points": [18, 90, 270, 450, 630, 900], "elite": ("Kourend & Kebos", [20, 100, 300, 500, 700, 1000]),
                        "block": 80, "combat": 75, "sub": False},
    "Nieve": {"points": [12, 60, 180, 300, 420, 600], "elite": ("Western Provinces", [15, 75, 225, 375, 525, 750]),
              "block": 90, "combat": 85, "sub": True},
    "Duradel": {"points": [15, 75, 225, 375, 525, 750], "block": 100, "combat": 100, "slayer": 50, "sub": True},
}
EVERY = [1, 10, 50, 100, 250, 1000]
REPRESENTATIVE = {"Bear": "Grizzly bear", "Bears": "Grizzly bear", "Birds": "Chicken", "Dogs": "Jackal",
                  "Dog slayer task": "Jackal", "Elves": "Elf Warrior", "Fossil Island Wyvern": "Spitting wyvern",
                  "Fossil Island wyverns": "Spitting wyvern", "Kalphite": "Kalphite Soldier",
                  "Kalphites": "Kalphite Soldier", "Metal dragon": "Steel dragon", "Metal dragons": "Steel dragon",
                  "Monkeys": "Monkey", "Spiritual creature": "Spiritual mage", "Spiritual creatures": "Spiritual mage",
                  "Troll": "Mountain troll", "Trolls": "Mountain troll", "TzHaar": "TzHaar-Ket",
                  "Vampyre": "Feral Vampyre", "Vampyres": "Feral Vampyre", "Warped creature": "Warped terrorbird",
                  "Scabarites": "Scarab mage", "Crawling Hands": "Crawling Hand",
                  "Custodian stalker": "Juvenile custodian stalker", "Lesser Nagua": "Sulphur Nagua",
                  "Sea snake": "Sea Snake Young"}


def _section(text, title="Tasks"):
    m = re.search(r"(?m)^==\s*" + title + r"\s*==\s*$", text)
    if not m:
        return ""
    nxt = re.search(r"(?m)^==[^=]", text[m.end():])
    return text[m.end():m.end() + nxt.start()] if nxt else text[m.end():]


def parse_requirements(cell, quest_names=()):
    """{levels: {skill: n}, quests: [[alternatives]], unlock: name or None, text} from a requirement cell."""
    from .quests import LINK, plain
    levels = {}
    for sk, lv in re.findall(r'data-skill="([^"]+)" data-level="(\d+)"', cell):
        levels["Combat" if sk.startswith("Combat") else sk] = int(lv)
    for sk, lv in re.findall(r"\{\{\s*SCP\s*\|\s*([A-Za-z ]+?)\s*\|\s*(\d+)", cell):
        levels["Combat" if sk.startswith("Combat") else sk] = int(lv)
    clean = re.sub(r"(?s)<ref[^>]*/>|<ref[^>]*>.*?</ref>|\{\{CiteTwitter[^}]*\}\}", "", cell)
    qnames = {n.lower() for n in quest_names}
    quests = []
    for part in re.split(r",\s*", clean):
        alts = [m.group(1).strip() for m in LINK.finditer(part) if m.group(1).strip().lower() in qnames]
        if alts:
            quests.append(alts)
    unlock = re.search(r"''([^']+)''\s*ability", clean)
    text = re.sub(r"\s+", " ", plain(re.sub(r"<[^>]+>", " ", clean))).strip()
    return {"levels": levels, "quests": quests, "unlock": unlock.group(1) if unlock else None, "text": text}


def parse_assignments(text, quest_names=()):
    """[{task, monster, min, max, weight, req}] from an assignment table."""
    from .skillguides import tables
    from .quests import LINK, plain
    out = []
    for head, rows in tables(text):
        h = [re.sub(r"\s+", " ", plain(x)).strip().lower() for x in head]
        if "monster" not in h or not any("weight" in c for c in h):
            continue
        cm, ca = h.index("monster"), next(i for i, c in enumerate(h) if c == "amount")
        cw = next(i for i, c in enumerate(h) if "weight" in c)
        cr = next((i for i, c in enumerate(h) if "unlock" in c or "requirement" in c), None)
        for r in rows:
            if len(r) <= max(cm, ca, cw):
                continue
            links = [m for m in LINK.finditer(r[cm]) if not m.group(1).lower().startswith("file:")]
            if not links:
                continue
            target = links[0].group(1).strip()
            label = (links[0].group(2) or "").strip()
            task = target.split("/", 1)[1] if target.startswith("Slayer task/") else re.sub(r"\s+", " ", plain(r[cm])).strip()
            task = task[:1].upper() + task[1:]
            monster = target.split("/", 1)[1] if target.startswith("Slayer task/") else target
            amt = re.findall(r"(\d+)\s*[-\u2013]\s*(\d+)", plain(r[ca]))
            w = re.findall(r"\d+", re.sub(r"<[^>]+>", "", plain(r[cw])))
            if not amt or not w:
                continue
            out.append({"task": task or label, "monster": monster, "min": int(amt[-1][0]), "max": int(amt[-1][1]),
                        "weight": int(w[0]), "req": parse_requirements(r[cr], quest_names) if cr is not None else
                        {"levels": {}, "quests": [], "unlock": None, "text": ""}})
        if out:
            break
    return out


def parse_monster(text):
    """(hitpoints, Slayer XP) from a monster's {{Infobox Monster}}, first version."""
    def num(key):
        m = re.search(r"\|\s*" + key + r"\d?\s*=\s*([\d,.]+)", text or "")
        return float(m.group(1).replace(",", "")) if m else None
    hp, xp = num("hitpoints"), num("slayxp")
    return (hp, xp if xp is not None else hp) if hp else None


def fetch_all(client, quest_names=(), sleep=1.0):
    import time as _t
    inline = [m for m, v in MASTERS.items() if not v["sub"]]
    d = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                         "titles": "|".join(inline), "redirects": "1"})
    pages = {p["title"]: p["revisions"][0]["slots"]["main"]["content"] for p in d.get("query", {}).get("pages", [])
             if p.get("revisions")}
    _t.sleep(sleep)
    text = ""
    for m, v in MASTERS.items():
        body = f"{{{{:{m}/Slayer assignments}}}}" if v["sub"] else _section(pages.get(m, ""))
        text += f"\n@@@{m}@@@\n{body}\n"
    w = client.wiki_api({"action": "expandtemplates", "text": text + "\n@@@end@@@\n", "prop": "wikitext"})
    w = w.get("expandtemplates", {}).get("wikitext", "")
    masters = {}
    for m in MASTERS:
        i = w.find(f"@@@{m}@@@")
        j = w.find("\n@@@", i + len(m) + 6)
        masters[m] = parse_assignments(w[i:j], quest_names) if i >= 0 else []
    _t.sleep(sleep)
    names = sorted({t["monster"] for ts in masters.values() for t in ts})
    monsters = {}
    for k in range(0, len(names), 50):
        r = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                             "titles": "|".join(names[k:k + 50]), "redirects": "1"})
        q = r.get("query", {})
        hop = {x["from"]: x["to"] for x in (q.get("normalized") or []) + (q.get("redirects") or [])}
        found = {}
        for p in q.get("pages", []):
            if p.get("revisions"):
                got = parse_monster(p["revisions"][0]["slots"]["main"]["content"])
                if got:
                    found[p["title"]] = {"hp": got[0], "xp": got[1]}
        for n in names[k:k + 50]:
            t = n
            for _ in range(3):  # normalized, then redirected
                t = hop.get(t, t)
            if t in found:
                monsters[n] = found[t]
        _t.sleep(sleep)
    # Tasks that cover a group of monsters: one typical member stands in for the estimate.
    missing = {n: REPRESENTATIVE[n] for n in names if n not in monsters and n in REPRESENTATIVE}
    if missing:
        r = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                             "titles": "|".join(sorted(set(missing.values()))), "redirects": "1"})
        q = r.get("query", {})
        hop = {x["from"]: x["to"] for x in (q.get("normalized") or []) + (q.get("redirects") or [])}
        found = {p["title"]: parse_monster(p["revisions"][0]["slots"]["main"]["content"])
                 for p in q.get("pages", []) if p.get("revisions")}
        for n, rep in missing.items():
            t = hop.get(hop.get(rep, rep), hop.get(rep, rep))
            if found.get(t):
                monsters[n] = {"hp": found[t][0], "xp": found[t][1], "as": rep}
    return {"source": "OSRS Wiki Slayer master assignment tables and monster infoboxes (CC BY-NC-SA 3.0)",
            "version": VERSION, "fetched": int(_t.time()), "masters": masters, "monsters": monsters}


_cache = {}


def load(db=None):
    if db is not None:
        d = db.kv_get("slayer_wiki")
        if d and d.get("masters") and d.get("version") == VERSION:
            return d
    if "snap" not in _cache:
        try:
            with open(SNAPSHOT, encoding="utf-8") as f:
                _cache["snap"] = json.load(f)
        except (OSError, ValueError):
            _cache["snap"] = {"masters": {}, "monsters": {}}
    return _cache["snap"]


def refresh(db, client, quest_names=(), max_age=7 * 86400):
    d = db.kv_get("slayer_wiki") or {}
    if d.get("fetched") and time.time() - d["fetched"] < max_age and d.get("version") == VERSION:
        return 0
    data = fetch_all(client, quest_names)
    if sum(len(v) for v in data["masters"].values()) >= 100:
        db.kv_set("slayer_wiki", data)
    return len(data["masters"])


# The advisor ---------------------------------------------------------------------------------
RATING = {"like": 3, "ok": 2, "dislike": 1}
SKIP_MINUTES = 1.0  # a trip to the master for a new task


def prefs(db, acct):
    return db.kv_get(f"slayer_prefs:{acct or '*'}") or {}


def save_prefs(db, acct, p):
    keep = {k: p[k] for k in ("master", "goal", "cancel", "unlocks", "ratings") if k in p}
    db.kv_set(f"slayer_prefs:{acct or '*'}", keep)
    return keep


def avg_points(master, diaries):
    """Points per finished task over a long streak (every 10th, 50th... pays more)."""
    m = MASTERS[master]
    pts = m["points"]
    if m.get("elite") and (diaries.get(m["elite"][0]) or [0, 0, 0, 0])[3]:
        pts = m["elite"][1]
    total = pts[0] * 1000
    for k in range(1, 6):
        total += (pts[k] - pts[k - 1]) * (1000 // EVERY[k])
    return total / 1000


def block_slots(qp, diaries):
    return min(6, (qp or 0) // 50) + (1 if (diaries.get("Lumbridge & Draynor") or [0, 0, 0, 0])[3] else 0)


def _active_minutes(sessions, a, b):
    mins = sum(max(0, min(b, s["last"]) - max(a, s["start"])) for s in sessions) / 60
    return mins or (b - a) / 60


def measured(db, acct):
    """{task: {"xph", "minutes", "n"}} from finished tasks, timed by your play sessions."""
    sessions = db.q("SELECT start, last FROM sessions WHERE acct=?", (acct,)) if acct else []
    by = {}
    for h in history(db, acct):
        if not h["xp"]:
            continue
        mins = _active_minutes(sessions, h["assigned"], h["done"])
        if mins < 3:
            continue
        by.setdefault(h["task"].lower(), []).append((h["xp"] / mins * 60, mins))
    out = {}
    for t, v in by.items():
        v.sort()
        mid = v[len(v) // 2]
        out[t] = {"xph": mid[0], "minutes": mid[1], "n": len(v)}
    return out


def estimate(task, monsters, combat):
    """Rough XP per task and minutes, from the monster's hitpoints and your combat level."""
    m = monsters.get(task["monster"]) or {}
    amount = (task["min"] + task["max"]) / 2
    if not m.get("hp"):
        return None, None
    dps = 0.9 + 0.055 * max(3, combat or 90)
    secs = 3 + m["hp"] / dps
    return amount * (m.get("xp") or m["hp"]), amount * secs / 60


def eligible(task, levels, combat, quest_done, unlocks, seen, known):
    """[] when the master can give you this task, else what it needs."""
    if not known:
        return []
    need = []
    for sk, lv in task["req"]["levels"].items():
        have = combat if sk == "Combat" else (levels.get(sk) or [1])[0]
        if (have or 0) < lv:
            need.append(f"{lv} {sk}")
    for alts in task["req"]["quests"]:
        if not any(quest_done(q) for q in alts):
            need.append(" or ".join(alts))
    u = task["req"]["unlock"]
    if u and u not in unlocks and task["task"].lower() not in seen:
        need.append(f"the {u} unlock")
    return need


def optimise(rows, slots, points, cancel, goal):
    """Pick blocks and skips for the best rate you can keep up on points.
    rows: [{task, weight, xp, minutes, rating, force}] eligible tasks."""
    def value(r):
        return r["rating"] if goal == "liked" else (r["xp"] / r["minutes"] if r["minutes"] else 0)
    order = sorted(rows, key=lambda r: (r["force"] != "skip", value(r)))
    best = None
    for k in range(0, len(order)):
        bad = [r for r in order[:k] if r["force"] != "do"] + [r for r in order[k:] if r["force"] == "skip"]
        kept = [r for r in rows if r not in bad]
        if not kept:
            break
        blocks = sorted(bad, key=lambda r: -r["weight"])[:slots]
        skips = [r for r in bad if r not in blocks]
        wk, ws = sum(r["weight"] for r in kept), sum(r["weight"] for r in skips)
        per = ws / wk
        balance = points - per * cancel
        if goal == "liked":
            rate = sum(r["weight"] * r["rating"] for r in kept) / wk
        else:
            xp = sum(r["weight"] * r["xp"] for r in kept)
            mins = sum(r["weight"] * r["minutes"] for r in kept) + ws * SKIP_MINUTES
            rate = xp / mins * 60 if mins else 0
        cand = {"k": k, "blocks": [r["task"] for r in blocks], "skips": [r["task"] for r in skips], "rate": rate,
                "skipsPerTask": per, "balance": balance, "keptShare": wk / (wk + sum(r["weight"] for r in bad))}
        # Another block or skip has to earn its place: 2% better than the plan with fewer.
        if balance >= 0 and (best is None or rate > best["rate"] * 1.02):
            best = cand
    return best


def advise(db, engine, acct=None, master=None, goal=None):
    """Your Slayer picture: current task, each master's best block and skip lists for your goal,
    and every task with its chance, size and rate."""
    from . import account, planner
    acct = acct or planner.main_account(db)
    data = load(db)
    prog = account.progress(db, acct) if acct else {"levels": {}, "quests": {}, "qp": None, "combat": None, "known": False}
    diaries = account.diaries(db, acct)
    st = state(db, acct) or {}
    pr = prefs(db, acct)
    goal = goal or pr.get("goal") or "xp"
    levels, combat = prog["levels"], prog.get("combat") or (90 if not prog["known"] else 3)
    qstate = {k.lower(): v for k, v in prog["quests"].items()}
    quest_done = (lambda q: qstate.get(q.lower(), 0) == 2) if qstate else (lambda q: True)
    seen = {r["task"].lower() for r in db.q("SELECT DISTINCT task FROM slayer_task WHERE acct=?", (acct,))} if acct else set()
    unlocks = set(pr.get("unlocks") or [])
    ratings = pr.get("ratings") or {}
    meas = measured(db, acct) if acct else {}
    slots = block_slots(prog.get("qp"), diaries) if prog["known"] else 6
    slay = (levels.get("Slayer") or [None])[0]
    masters = []
    for name, m in MASTERS.items():
        if name == "Turael":
            continue
        tasks = data.get("masters", {}).get(name) or []
        locked_master = []
        if prog["known"] and combat < m["combat"]:
            locked_master.append(f"combat {m['combat']}")
        if prog["known"] and m.get("slayer") and (slay or 1) < m["slayer"]:
            locked_master.append(f"{m['slayer']} Slayer")
        rows, locked = [], []
        total_w = 0
        for t in tasks:
            need = eligible(t, levels, combat, quest_done, unlocks, seen, prog["known"])
            ms = meas.get(t["task"].lower())
            xp, mins = estimate(t, data.get("monsters", {}), combat)
            src = "rough"
            if ms:
                mins = ms["minutes"]
                xp = ms["xph"] * mins / 60
                src = f"yours ({ms['n']})"
            r = {"task": t["task"], "weight": t["weight"], "min": t["min"], "max": t["max"], "xp": xp or 0,
                 "minutes": mins or 0, "source": src if xp else "no estimate",
                 "rating": RATING.get(ratings.get(t["task"], "ok"), 2),
                 "force": ratings.get(t["task"]) if ratings.get(t["task"]) in ("skip", "do") else None,
                 "xph": (xp / mins * 60) if xp and mins else None}
            if need:
                locked.append(dict(r, needs=need))
            else:
                rows.append(r)
                total_w += t["weight"]
        if goal != "liked":
            rows = [r for r in rows if r["minutes"]] + [dict(r, minutes=0.0) for r in rows if not r["minutes"]]
        cancel = (pr.get("cancel") or {}).get(name) or m["block"] - 10
        pts = avg_points(name, diaries)
        plan = optimise([r for r in rows if r["minutes"] or goal == "liked"], slots, pts, cancel, goal) if rows else None
        for r in rows:
            r["chance"] = r["weight"] / total_w if total_w else 0
            r["advice"] = ("block" if plan and r["task"] in plan["blocks"] else
                           "skip" if plan and r["task"] in plan["skips"] else "do")
            r["blocked"] = r["task"] in (st.get("blocked") or []) or r["task"].lower() in {b.lower() for b in st.get("blocked") or []}
        rows.sort(key=lambda r: -r["weight"])
        masters.append({"master": name, "url": WIKI + name.replace(" ", "_"), "locked": locked_master,
                        "points": pts, "cancel": cancel, "blockCost": m["block"], "plan": plan, "tasks": rows,
                        "lockedTasks": locked, "eligible": len(rows)})
    usable = [m for m in masters if not m["locked"] and m["plan"]]
    best = max(usable, key=lambda m: m["plan"]["rate"]) if usable else None
    chosen = next((m for m in masters if m["master"] == (master or pr.get("master"))), None) or best or (masters[-1] if masters else None)
    return {"acct": acct, "known": prog["known"], "goal": goal, "slots": slots, "state": st, "best": best["master"] if best else None,
            "master": chosen["master"] if chosen else None, "masters": masters, "measuredTasks": len(meas),
            "unlockNames": sorted({t["req"]["unlock"] for ts in data.get("masters", {}).values() for t in ts if t["req"]["unlock"]}),
            "unlocks": sorted(unlocks), "ratings": ratings, "source": data.get("source")}
