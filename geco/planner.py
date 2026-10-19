"""The goal planner: everything between your account and a goal, in order, with time and cost.

A goal is a quest, all grandmaster quests, a boss or raid's access, or a skill level. The plan:
* every quest still needed (prerequisites, recursively), in an order that respects them, keeps
  nearby starts together and puts short quests first when there is a choice;
* extra quests to reach a quest point requirement, picked by quest points per minute among
  quests you can already do;
* skill gaps: a boost when the requirement allows one and a boost covers it, else training with
  the fastest (or cheapest) method you can use, at your own measured XP rate when known;
* time for each step (see questtime) and in total;
* one shopping list across all of it, priced live, minus what you already hold.
Display only: nothing here touches the game.
"""
import time

from . import access, account, questtime, training
from .quests import load, norm

SKILL_ORDER = ["Attack", "Strength", "Defence", "Ranged", "Prayer", "Magic", "Runecraft", "Construction",
               "Hitpoints", "Agility", "Herblore", "Thieving", "Crafting", "Fletching", "Slayer", "Hunter",
               "Mining", "Smithing", "Fishing", "Cooking", "Firemaking", "Woodcutting", "Farming", "Sailing"]


def main_account(db):
    a = account.accounts(db)
    return a[0]["acct"] if a else None


def _state(prog, name):
    s = prog["quests"]
    if name in s:
        return s[name]
    k = norm(name)
    for n, v in s.items():
        if norm(n) == k:
            return v
    return None


def goals_catalog(db):
    """What can be picked as a goal."""
    Q = load(db)
    gm = sorted(n for n, q in Q.items() if (q.get("difficulty") or "").lower() == "grandmaster")
    return {"quests": sorted(Q), "grandmaster": gm,
            "bosses": [{"name": b["name"], "group": b["group"]} for b in access.listing()]}


def _targets(goal, Q):
    """(quests, skills {name: (level, boostable, why)}, other requirement texts, label)."""
    kind = goal.get("type")
    by = {norm(k): k for k in Q}
    if kind == "quest":
        n = by.get(norm(goal["name"]))
        if not n:
            raise ValueError(f"Unknown quest: {goal['name']}")
        return [n], {}, [], n
    if kind == "grandmasters":
        gm = [n for n, q in Q.items() if (q.get("difficulty") or "").lower() == "grandmaster"]
        return sorted(gm), {}, [], "All grandmaster quests"
    if kind == "boss":
        b = access.BOSSES.get(goal["name"])
        if not b:
            raise ValueError(f"Unknown boss: {goal['name']}")
        qs = [by[norm(x)] for x in b["quests"] if norm(x) in by]
        sk = {s: (lv, False, goal["name"]) for s, lv in b["skills"].items()}
        return qs, sk, list(b["other"]), f"{goal['name']} access"
    if kind == "skill":
        return [], {goal["skill"]: (int(goal["level"]), False, "goal")}, [], f"{goal['level']} {goal['skill']}"
    raise ValueError("Pick a quest, grandmaster quests, a boss, or a skill level")


def _price_fn(engine):
    by_name = {}
    for iid, m in engine.mapping.items():
        by_name.setdefault((m.get("name") or "").lower(), iid)

    def find(name):
        import re as _re
        n = _re.sub(r"\s*\((weapon|item|quest)\)$", "", (name or "").lower().replace("_", " ").strip())
        for cand in (n, n.rstrip("s"), n[:-2] if n.endswith("es") else None, n + "s", n + "(4)", n + "(3)"):
            if cand and cand in by_name:
                return by_name[cand]
        return None

    def price(name, side="buy"):
        iid = find(name)
        r = engine.by_id.get(iid) if iid else None
        if not r:
            return None
        if side == "buy":
            return r.get("high") or r.get("low")
        p = r.get("low") or r.get("high")
        return p - engine.tax(p, iid) if p else None
    return find, price


def build(db, engine, cfg, goal, acct=None, mode="fastest", now=None):
    now = now or time.time()
    Q = load(db)
    if not Q:
        raise ValueError("No quest data yet")
    acct = acct or main_account(db)
    prog = account.progress(db, acct) if acct else {"qp": None, "combat": None, "levels": {}, "quests": {}, "known": False}
    levels = prog["levels"]
    find, price = _price_fn(engine)
    sessions = db.q("SELECT start, last FROM sessions WHERE acct=?", (acct,)) if acct else []
    pace = questtime.pace(db, acct, Q, sessions) if acct else {"factor": 1.0, "n": 0}
    measured = training.measured_rates(db, acct, now=now) if acct else {}

    def done(n):
        return _state(prog, n) == 2

    tquests, tskills, other, label = _targets(goal, Q)

    # Every quest still needed.
    need, stack = [], list(tquests)
    seen = set()
    while stack:
        n = stack.pop()
        if n in seen or n not in Q or done(n):
            continue
        seen.add(n)
        need.append(n)
        stack.extend(Q[n]["req"]["quests"])

    # Skill requirements: the highest level any needed quest (or the goal) asks for.
    skills = {}
    for s, (lv, boost, why) in tskills.items():
        skills[s] = {"level": lv, "boostable": boost, "why": why}
    qp_need, qp_for = 0, None
    combat = None
    for n in need:
        r = Q[n]["req"]
        for s, d in r["skills"].items():
            cur = skills.get(s)
            if not cur or d["level"] > cur["level"]:
                skills[s] = {"level": d["level"], "boostable": d["boostable"], "why": n, "note": d.get("note")}
            elif d["level"] == cur["level"] and not d["boostable"]:
                cur["boostable"] = False
        if r.get("qp") and r["qp"] > qp_need:
            qp_need, qp_for = r["qp"], n
        if r.get("combat"):
            combat = max(combat or 0, r["combat"])
        for o in r.get("other") or []:
            other.append({"quest": n, **o} if isinstance(o, dict) else {"quest": n, "kind": "text", "text": o})

    # Quest points: what you have plus what the plan earns before the gated quest.
    qp_now = prog.get("qp")
    if qp_now is None:
        qp_now = sum(q.get("qp") or 0 for n, q in Q.items() if done(n))
    filler = []
    if qp_need:
        earned = sum(Q[n].get("qp") or 0 for n in need if n != qp_for)
        short = qp_need - qp_now - earned
        if short > 0:
            cands = []
            for n, q in Q.items():
                if done(n) or n in seen or not q.get("qp"):
                    continue
                r = q["req"]
                if any(not done(p) for p in r["quests"]):
                    continue
                if any((levels.get(s, (1, 0))[0] or 1) < d["level"] for s, d in r["skills"].items()):
                    continue
                if r.get("qp") and r["qp"] > qp_now:
                    continue
                est = questtime.estimate(q, Q, pace["factor"])["minutes"]
                cands.append((est / q["qp"], n))
            for _, n in sorted(cands):
                if short <= 0:
                    break
                filler.append(n)
                need.append(n)
                seen.add(n)
                short -= Q[n]["qp"]

    # Order: prerequisites first; among the ready ones, the same start area as the last quest,
    # then the shortest.
    est = {n: questtime.estimate(Q[n], Q, pace["factor"]) for n in need}
    order, placed = [], set()
    remaining = set(need)
    last_place = None
    while remaining:
        ready = [n for n in remaining if all(p in placed or done(p) or p not in Q for p in Q[n]["req"]["quests"])]
        if not ready:
            ready = list(remaining)  # a loop in the data; place the rest by length
        ready.sort(key=lambda n: (Q[n].get("startPlace") != last_place, n == qp_for, est[n]["minutes"]))
        n = ready[0]
        order.append(n)
        placed.add(n)
        remaining.discard(n)
        last_place = Q[n].get("startPlace")

    wg = [o for o in other if o.get("kind") == "warriors_guild"]
    if wg:
        a, st = levels.get("Attack", (0, 0))[0] or 0, levels.get("Strength", (0, 0))[0] or 0
        for o in wg:
            o["met"] = a + st >= 130 or a >= 99 or st >= 99

    # Skill tasks, placed before the first quest that needs them.
    skill_tasks = {}
    for s, d in skills.items():
        lv, xp = levels.get(s, (None, None))
        if lv is None:
            skill_tasks[s] = {"type": "skill", "skill": s, "target": d["level"], "unknown": True, "why": d["why"],
                              "hours": None, "gp": None, "title": f"Check {s} is {d['level']}+"}
            continue
        if lv >= d["level"]:
            continue
        gap = d["level"] - lv
        boost = training.best_boost(s, gap) if d["boostable"] else None
        if boost:
            skill_tasks[s] = {"type": "boost", "skill": s, "from": lv, "target": d["level"], "boost": boost[0],
                              "amount": boost[1], "item": boost[2], "why": d["why"], "hours": 0.0,
                              "gp": price(boost[2], "buy") if boost[2] else None,
                              "title": f"Boost {s} {lv} to {d['level']} with a {boost[0].lower()} (+{boost[1]})"}
            continue
        p = training.plan_skill(s, lv, xp, d["level"], price, mode, measured.get(s))
        if p:
            p.update(type="skill", why=d["why"], title=f"Train {s} {lv} to {d['level']}")
            skill_tasks[s] = p
    tasks = []
    used = set()
    for n in order:
        for s, t in sorted(skill_tasks.items(), key=lambda kv: SKILL_ORDER.index(kv[0]) if kv[0] in SKILL_ORDER else 99):
            if s in used:
                continue
            need_here = t["why"] == n or (n in Q and s in Q[n]["req"]["skills"]
                                          and (levels.get(s, (0, 0))[0] or 0) < Q[n]["req"]["skills"][s]["level"])
            if need_here:
                tasks.append(t)
                used.add(s)
        q = Q[n]
        e = est[n]
        tasks.append({"type": "quest", "quest": n, "title": n, "difficulty": q.get("difficulty"), "length": q.get("length"),
                      "start": q.get("start"), "startPlace": q.get("startPlace"), "qp": q.get("qp"),
                      "minutes": e["minutes"], "low": e["low"], "high": e["high"], "estSource": e["source"],
                      "hours": e["minutes"] / 60, "state": _state(prog, n) or 0, "filler": n in filler,
                      "gatedQp": qp_need if n == qp_for else None,
                      "prereqs": [p for p in q["req"]["quests"] if p in seen],
                      "items": [i["name"] for i in q.get("items") or []][:20],
                      "other": [{"text": o["text"], "met": o.get("met")} for o in other if o.get("quest") == n][:4],
                      "wiki": access.wiki_url(n), "guide": access.wiki_url(n + "/Quick guide")})
    for s, t in skill_tasks.items():
        if s not in used:
            tasks.insert(0, t)
            used.add(s)

    # Shopping list across the plan: tools once, consumables added up.
    want = {}
    generic = {"coins", "combat", "food", "weapon", "weapons", "armour", "armor", "runes", "prayer", "potions",
               "antipoison", "teleport", "inventory", "equipment"}
    for n in order:
        for it in Q[n].get("items") or []:
            if it["name"].lower().strip() in generic:
                continue
            iid = find(it["name"])
            key = iid or it["name"].lower()
            w = want.setdefault(key, {"id": iid, "name": it["name"], "qty": 0, "for": [], "line": it.get("line")})
            w["qty"] = w["qty"] + it["qty"] if it["qty"] > 1 else max(w["qty"], 1)
            w["for"].append(n)
    for t in skill_tasks.values():
        if t["type"] == "boost" and t.get("item"):
            iid = find(t["item"])
            want.setdefault(iid or t["item"].lower(), {"id": iid, "name": t["item"], "qty": 1, "for": [f"{t['skill']} boost"], "line": None})
    held = {}
    try:
        from . import networth
        view = networth.account_view(db, engine, acct, cfg) if acct else None
        for h in (view or {}).get("holdings", []):
            held[h["id"]] = held.get(h["id"], 0) + h["qty"]
    except Exception:  # noqa: BLE001
        pass
    shop, other_items = [], []
    for w in want.values():
        if w["id"] and engine.by_id.get(w["id"]):
            r = engine.by_id[w["id"]]
            have = held.get(w["id"], 0)
            buy = max(0, w["qty"] - have)
            each = r.get("high") or r.get("low")
            m = engine.mapping.get(w["id"]) or {}
            shop.append({"id": w["id"], "name": m.get("name") or w["name"], "icon": m.get("icon"), "qty": w["qty"], "have": have,
                         "buy": buy, "each": each, "cost": buy * each if each else None, "for": w["for"][:4]})
        else:
            other_items.append({"name": w["name"], "qty": w["qty"], "for": w["for"][:3], "line": w["line"]})
    shop.sort(key=lambda s: -(s["cost"] or 0))

    hours_q = sum(t["hours"] for t in tasks if t["type"] == "quest")
    hours_s = sum(t["hours"] or 0 for t in tasks if t["type"] in ("skill", "boost"))
    reqs = []
    for s, d in sorted(skills.items(), key=lambda kv: SKILL_ORDER.index(kv[0]) if kv[0] in SKILL_ORDER else 99):
        lv = levels.get(s, (None, None))[0]
        reqs.append({"skill": s, "level": d["level"], "have": lv, "boostable": d["boostable"], "why": d["why"],
                     "met": lv is not None and lv >= d["level"], "note": d.get("note")})
    return {
        "goal": goal, "label": label, "acct": acct, "accountKnown": prog["known"], "mode": mode,
        "tasks": tasks, "requirements": reqs,
        "qp": {"need": qp_need, "for": qp_for, "have": qp_now, "plan": sum(Q[n].get("qp") or 0 for n in order), "filler": filler,
               "enough": not qp_need or qp_now + sum(Q[n].get("qp") or 0 for n in order if n != qp_for) >= qp_need},
        "combat": {"need": combat, "have": prog.get("combat")},
        "other": [dict(o) for o in other],
        "shopping": shop, "otherItems": other_items,
        "shoppingCost": sum(s["cost"] or 0 for s in shop),
        "trainingCost": sum(t["gp"] or 0 for t in tasks if t["type"] == "skill" and t.get("gp")),
        "hours": {"quests": hours_q, "skills": hours_s, "total": hours_q + hours_s},
        "counts": {"quests": sum(1 for t in tasks if t["type"] == "quest"), "skills": sum(1 for t in tasks if t["type"] == "skill"),
                   "boosts": sum(1 for t in tasks if t["type"] == "boost")},
        "pace": pace,
        "done": not tasks and all(r["met"] for r in reqs),
    }


# The active goal (shown in the RuneLite panel) ----------------------------------------------

def active(db):
    return db.kv_get("planner_goal") or None


def set_active(db, goal):
    db.kv_set("planner_goal", goal or None)
