"""Your farming patches and bird houses, as the RuneLite plugin saw them.

The plugin writes the farming varbits of the region you stand in (and the four bird house
spaces on Fossil Island). They are decoded here with the tables of RuneLite's own Time Tracking
plugin (`farming_patches.json`, ported from net.runelite.client.plugins.timetracking.farming,
BSD 2-Clause, copyright notice kept in the file), and grown forward the same way: a crop moves one
stage on every farming tick of its rate (5, 10, 20, 40, 80, 160 or 640 minutes), with the
account's tick offset when Time Tracking has learned it. Diseases can't be predicted, so a patch
counts as ready once its last stage is due.

Also measured here: your real minutes between stops on a run, from the times you planted.
Display only: the plugin reads what the game sends and changes nothing.
"""
import json
import os
import statistics
import time

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "farming_patches.json")
VARBITS = [4771, 4772, 4773, 4774, 4775, 7904, 7905, 7906, 7907, 7908, 7909, 7910, 7911, 7912]  # A to N
BIRD_SPACES = ["Mushroom Meadow (north)", "Mushroom Meadow (south)", "Verdant Valley (northeast)",
               "Verdant Valley (southwest)"]
BIRD_MINUTES = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS farm_patch (acct TEXT NOT NULL, region INTEGER NOT NULL, varbit INTEGER NOT NULL,
    value INTEGER NOT NULL, t INTEGER NOT NULL, PRIMARY KEY (acct, region, varbit));
CREATE TABLE IF NOT EXISTS farm_change (acct TEXT NOT NULL, t INTEGER NOT NULL, region INTEGER NOT NULL,
    varbit INTEGER NOT NULL, old INTEGER, new INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS farm_change_t ON farm_change(acct, t);
CREATE TABLE IF NOT EXISTS bird_house (acct TEXT NOT NULL, space INTEGER NOT NULL, value INTEGER NOT NULL,
    t INTEGER NOT NULL, PRIMARY KEY (acct, space));
"""

# RuneLite patch implementations to the kinds the run planner uses.
IMPL_KIND = {"HERB": "herb", "ALLOTMENT": "allotment", "FLOWER": "flower", "TREE": "tree", "FRUIT_TREE": "fruit",
             "HOPS": "hops", "BUSH": "bush", "HARDWOOD_TREE": "hardwood", "CALQUAT": "calquat",
             "CELASTRUS": "celastrus", "REDWOOD": "redwood", "SPIRIT_TREE": "spirit", "CRYSTAL_TREE": "crystal",
             "CACTUS": "cactus", "MUSHROOM": "mushroom", "BELLADONNA": "belladonna", "SEAWEED": "seaweed",
             "HESPORI": "hespori", "ANIMA": "anima", "CORAL": "coral", "COMPOST": "compost"}
KIND_LABEL = {"herb": "Herbs", "allotment": "Allotments", "flower": "Flowers", "tree": "Trees", "fruit": "Fruit trees",
              "hops": "Hops", "bush": "Bushes", "hardwood": "Hardwood trees", "calquat": "Calquat",
              "celastrus": "Celastrus", "redwood": "Redwood", "spirit": "Spirit trees", "crystal": "Crystal tree",
              "birdhouse": "Bird houses"}
# Patches the game draws inside one region file only from part of it (as Time Tracking checks).
BOUNDS = {
    ("Catherby", 11062): lambda x, y, p: (x < 2840 and y >= 3440 and p == 0) if (x >= 2816 and y < 3456) else True,
    ("Catherby", 11317): lambda x, y, p: x >= 2840 or y < 3440 or p == 1,
    ("Falador", 12083): lambda x, y, p: y >= 3272,
    ("Port Sarim", 12082): lambda x, y, p: y < 3272,
    ("Fossil Island", 14651): lambda x, y, p: p == 0,
}
STOP_GAP = 12 * 60  # plantings further apart than this are separate runs

_data = {}


def data():
    if "d" not in _data:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
        by_region = {}
        for r in d["regions"]:
            for rid in [r["region"]] + r["extra"]:
                by_region.setdefault(rid, []).append(r)
        d["byRegion"] = by_region
        _data["d"] = d
    return _data["d"]


def init(db):
    db.conn.executescript(SCHEMA)


def regions_for(region, x, y, plane):
    out = []
    for r in data()["byRegion"].get(region, []):
        rule = BOUNDS.get((r["name"], r["region"]))
        if rule is None or rule(x or 0, y or 0, plane or 0):
            out.append(r)
    return out


def decode(impl, value):
    """(produce key, crop state, stage) for a patch value, as Time Tracking reads it, or None."""
    imp = data()["impls"].get(impl)
    if not imp:
        return None
    for lo, hi, prod, state, (kind, n) in imp["rows"]:
        if lo <= value <= hi:
            if kind == "v":
                stage = value + n
            elif kind == "n":
                stage = n - value
            elif kind == "k":
                stage = n
            else:  # the produce's last stage
                stage = data()["produce"][prod]["stages"] - 1
            return prod, state, stage
    return None


def stages_rate(prod, state):
    p = data()["produce"][prod]
    stages = p["harvestStages"] if state in ("HARVESTABLE", "FILLING") else p["stages"]
    rate = p["regrow"] if state == "HARVESTABLE" else p["tick"] if state == "GROWING" else 0
    return stages, rate


def tick_time(rate, ticks, t, offset=None):
    """The time of a farming tick `ticks` after the one at or before t (RuneLite's getTickTime)."""
    off = 0
    if offset and offset[0] is not None and offset[1] is not None and (offset[1] >= rate or offset[1] >= 40):
        off = (offset[0] % rate) * 60
    now = t + off
    cur = now - now % (rate * 60)
    return cur + ticks * rate * 60 - off


def predict(impl, value, seen, now, offset=None, autoweed=False):
    """{produce, state, stage, stages, doneAt} grown forward from when the value was seen."""
    st = decode(impl, value)
    if not st:
        return None
    prod, state, stage = st
    stages, rate = stages_rate(prod, state)
    if autoweed and prod == "WEEDS":
        stage, stages, rate = 0, 1, 0
    done = 0
    if rate > 0:
        t_now, t_seen = tick_time(rate, 0, now, offset), tick_time(rate, 0, seen, offset)
        delta = int((t_now - t_seen) // (rate * 60))
        done = tick_time(rate, stages - 1 - stage, t_seen, offset)
        stage = min(stage + delta, stages - 1)
    return {"produce": prod, "name": data()["produce"][prod]["name"], "state": state, "stage": stage,
            "stages": stages, "doneAt": done}


# Ingest -------------------------------------------------------------------------------------

def apply_farm(db, acct, t, e):
    vals = e.get("vals") or []
    if len(vals) < len(VARBITS):
        return
    if e.get("tickOffset") not in (None, "") or e.get("weeds") is not None:
        db.kv_set(f"farm_tick:{acct}", {"offset": _int(e.get("tickOffset")), "precision": _int(e.get("tickPrecision")),
                                        "weeds": e.get("weeds")})
    for r in regions_for(e.get("region"), e.get("x"), e.get("y"), e.get("plane")):
        for p in r["patches"]:
            v = int(vals[VARBITS.index(p["varbit"])])
            if decode(p["impl"], v) is None:
                continue
            prev = db.one("SELECT value FROM farm_patch WHERE acct=? AND region=? AND varbit=?", (acct, r["region"], p["varbit"]))
            if prev and prev["value"] != v:
                db.run("INSERT INTO farm_change (acct, t, region, varbit, old, new) VALUES (?,?,?,?,?,?)",
                       (acct, t, r["region"], p["varbit"], prev["value"], v))
            db.run("INSERT OR REPLACE INTO farm_patch (acct, region, varbit, value, t) VALUES (?,?,?,?,?)",
                   (acct, r["region"], p["varbit"], v, t))
    birds = e.get("birdhouses")
    if birds and len(birds) == 4:
        old = {r["space"]: r["value"] for r in db.q("SELECT space, value FROM bird_house WHERE acct=?", (acct,))}
        # Varps can lag a tick on arrival: three or four houses dropping to nothing at once is that.
        if sum(1 for i, v in enumerate(birds) if v <= 0 and old.get(i, 0) > 0) > 2:
            return
        for i, v in enumerate(birds):
            if old.get(i) != v:
                db.run("INSERT OR REPLACE INTO bird_house (acct, space, value, t) VALUES (?,?,?,?)", (acct, i, int(v), t))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# Reading --------------------------------------------------------------------------------------

def _patch_meta():
    out = {}
    for r in data()["regions"]:
        for p in r["patches"]:
            out[(r["region"], p["varbit"])] = (r, p)
    return out


def status(db, acct, now=None):
    """Every patch and bird house seen for an account, grown forward to now, and readiness per kind."""
    now = now or int(time.time())
    tick = db.kv_get(f"farm_tick:{acct}") or {}
    offset = (tick.get("offset"), tick.get("precision"))
    autoweed = tick.get("weeds") == 1
    meta = _patch_meta()
    patches = []
    for row in db.q("SELECT * FROM farm_patch WHERE acct=?", (acct,)):
        m = meta.get((row["region"], row["varbit"]))
        if not m:
            continue
        r, p = m
        pr = predict(p["impl"], row["value"], row["t"], now, offset, autoweed)
        if not pr:
            continue
        kind = IMPL_KIND.get(p["impl"], p["impl"].lower())
        if kind == "compost":
            continue
        planted = pr["produce"] not in ("WEEDS", "SCARECROW") and pr["state"] not in ("EMPTY", "FILLING")
        if not planted:
            state = "empty"
        elif pr["state"] == "DISEASED":
            state = "diseased"
        elif pr["state"] == "DEAD":
            state = "dead"
        elif pr["state"] == "HARVESTABLE" or (pr["doneAt"] and pr["doneAt"] <= now):
            state = "ready"
        else:
            state = "growing"
        patches.append({"place": r["name"] + (f" ({p['name'].lower()})" if p["name"] else ""), "region": r["region"],
                        "kind": kind, "crop": pr["name"] if planted else None, "state": state,
                        "doneAt": pr["doneAt"] if planted else None, "stage": pr["stage"], "stages": pr["stages"],
                        "seen": row["t"]})
    birds = []
    for row in db.q("SELECT * FROM bird_house WHERE acct=? ORDER BY space", (acct,)):
        v = row["value"]
        state = "empty" if v <= 0 else "growing" if v % 3 == 0 else "built"
        done = row["t"] + BIRD_MINUTES * 60 if state == "growing" else None
        if done and done <= now:
            state = "ready"
        birds.append({"place": BIRD_SPACES[row["space"]] if row["space"] < 4 else "Bird house", "kind": "birdhouse",
                      "state": state, "doneAt": done, "seen": row["t"]})
    groups = {}
    for p in patches + birds:
        g = groups.setdefault(p["kind"], {"kind": p["kind"], "label": KIND_LABEL.get(p["kind"], p["kind"].title()),
                                          "patches": 0, "planted": 0, "ready": 0, "sick": 0, "readyAt": None})
        g["patches"] += 1
        if p["state"] in ("growing", "ready", "diseased", "dead"):
            g["planted"] += 1
            if p["state"] == "ready":
                g["ready"] += 1
            if p["state"] in ("diseased", "dead"):
                g["sick"] += 1
            if p.get("doneAt"):
                g["readyAt"] = max(g["readyAt"] or 0, p["doneAt"])
    for g in groups.values():
        g["allReady"] = bool(g["planted"]) and g["ready"] + g["sick"] >= g["planted"]
    order = list(KIND_LABEL)
    return {"acct": acct, "now": now, "patches": sorted(patches, key=lambda p: (order.index(p["kind"]) if p["kind"] in order else 99, p["place"])),
            "birdhouses": birds, "groups": sorted(groups.values(), key=lambda g: order.index(g["kind"]) if g["kind"] in order else 99),
            "tickOffsetKnown": offset[0] is not None, "pace": pace(db, acct)}


def pace(db, acct, days=60, now=None):
    """Your minutes per stop on a run: the median time between planting in one place and the next,
    for plantings under STOP_GAP apart. {kind group: {"minutes", "n"}} once there are 5 or more."""
    now = now or int(time.time())
    meta = _patch_meta()
    rows = db.q("SELECT * FROM farm_change WHERE acct=? AND t>? ORDER BY t", (acct, now - days * 86400))
    plants = []
    for r in rows:
        m = meta.get((r["region"], r["varbit"]))
        if not m:
            continue
        st = decode(m[1]["impl"], r["new"])
        if st and st[0] not in ("WEEDS", "SCARECROW") and st[1] == "GROWING" and st[2] == 0:
            plants.append((r["t"], r["region"], IMPL_KIND.get(m[1]["impl"], "")))
    gaps = {}
    for (t0, r0, _), (t1, r1, k1) in zip(plants, plants[1:]):
        if r1 != r0 and 0 < t1 - t0 <= STOP_GAP:
            group = "trees" if k1 in ("tree", "fruit", "calquat", "celastrus", "hardwood", "redwood") else "patches"
            gaps.setdefault(group, []).append((t1 - t0) / 60)
    return {g: {"minutes": round(statistics.median(v), 2), "n": len(v)} for g, v in gaps.items() if len(v) >= 5}


def due_notices(db, accounts, now=None, window=6 * 3600):
    """[(key, message)] for kinds whose planted patches all became ready in the last `window`."""
    now = now or int(time.time())
    out = []
    for a in accounts:
        st = status(db, a["acct"], now)
        for g in st["groups"]:
            if g["allReady"] and g["readyAt"] and now - window < g["readyAt"] <= now:
                who = f"{a.get('name')}: " if len(accounts) > 1 and a.get("name") else ""
                sick = f", {g['sick']} diseased or dead" if g["sick"] else ""
                unit = ("bird house", "bird houses") if g["kind"] == "birdhouse" else ("patch", "patches")
                out.append((("farm", a["acct"], g["kind"], g["readyAt"]),
                            f"{who}{g['label']} ready: {g['planted']} {unit[0] if g['planted'] == 1 else unit[1]}{sick}"))
    return out
