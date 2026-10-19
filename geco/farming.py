"""Farming runs and bird house runs: the best crops, patches and teleports for your account.

Data (OSRS Wiki, CC BY-NC-SA 3.0), read in three bulk requests and refreshed weekly:
* The patch and seed tables (`<Patch> patch/Seeds`, `/Patches`), the disease table and the bird
  house table, rendered by the Wiki itself through `expandtemplates`, so every value and price
  comes out as the Wiki shows it.
* The "Farming runs" page: each run's route ("#[[Place]] fruit tree patch:") with its
  requirements and teleport options, quickest first, plus its example setup.
* Herb, allotment and hop seed pages for their harvest yield values.

For each run the planner checks every stop against your quests, levels, unlocks (diaries you
tick, items in your bank) and picks the quickest teleport you can use. For each kind of patch it
picks the crop for the mode: most XP, or best value (profit for crops you sell, least gp per XP
for trees). Herb, allotment and hop yields follow the Wiki: 3 harvest lives plus one per compost
tier, each pick saving a life with a chance from the seed's values at your level (more with
magic secateurs, a Farming cape or a patch bonus). Disease follows the Wiki's per growth cycle
chances by compost; disease free patches and paid gardeners never lose a crop.
Display only: nothing here touches the game.
"""
import json
import math
import os
import re
import time

from .quests import LINK, norm, plain

VERSION = 1
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "farming_snapshot.json")
WIKI = "https://oldschool.runescape.wiki/w/"

TABLES = [("herb_seeds", "{{:Herb patch/Seeds}}"), ("tree_seeds", "{{:Tree patch/Seeds}}"),
          ("fruit_seeds", "{{:Fruit tree patch/Seeds}}"), ("special_seeds", "{{:Special patches/Seeds}}"),
          ("hops_seeds", "{{:Hops patch/Seeds}}"), ("bush_seeds", "{{:Bush patch/Seeds}}"),
          ("allotment_seeds", "{{:Allotment patch/Seeds}}"), ("flower_seeds", "{{:Flower patch/Seeds}}"),
          ("herb_patches", "{{:Allotment patch/Patches|herb=yes|title=Herb}}"),
          ("birds", "{{:Bird house trapping/Table}}"), ("bird_seeds", "{{:Bird house trapping/Seeds}}"),
          ("disease", "{{:Disease (Farming)}}")]

# Run key: (title on the "Farming runs" page, patch kinds it covers)
RUNS = {
    "herb": ("Herb run", ["herb"]),
    "tree": ("Tree run", ["tree"]),
    "fruit": ("Fruit tree run", ["fruit", "calquat"]),
    "combined": ("Tree, fruit tree, calquat tree, and celastrus tree run", ["tree", "fruit", "calquat", "celastrus"]),
    "hardwood": ("Hardwood tree run", ["hardwood"]),
    "allotment": ("Allotment, flower, and herb run", ["allotment", "flower", "herb"]),
    "hops": ("Hop run", ["hops"]),
    "bush": ("Bush run", ["bush"]),
    "hopbush": ("Hop and bush run", ["hops", "bush"]),
}
LABEL = {"herb": "Herb", "tree": "Tree", "fruit": "Fruit tree", "calquat": "Calquat", "celastrus": "Celastrus",
         "hardwood": "Hardwood", "allotment": "Allotment", "flower": "Flower", "hops": "Hops", "bush": "Bush",
         "redwood": "Redwood", "crystal": "Crystal", "spirit": "Spirit tree"}
# Minutes per stop including the teleport, from the Wiki's "five to ten minutes" for a herb run;
# trees take longer to check, pay and replant.
STOP_MINUTES = {"herb": 0.75, "tree": 1.1, "fruit": 1.0, "calquat": 1.2, "celastrus": 0.8, "hardwood": 1.2,
                "allotment": 1.4, "flower": 0.3, "hops": 0.9, "bush": 0.9}
MAX_RUNS_DAY = 4  # nobody farms every 50 or 80 minutes all day; per day figures stop here
SLOWER_TELEPORT = 0.35  # extra minutes for each step down the Wiki's quickest-first list
COMPOST = [("none", None, 0), ("compost", "Compost", 1), ("supercompost", "Supercompost", 2),
           ("ultracompost", "Ultracompost", 3)]
HERB_LIKE = {"herb", "allotment", "hops"}
TREE_LIKE = {"tree", "fruit", "calquat", "celastrus", "hardwood", "redwood"}

# Unlocks the plugin cannot see, ticked on the Runs page.
UNLOCKS = {
    "kandarin_medium": "Kandarin medium diary (Catherby herbs +5% saved lives)",
    "kandarin_hard": "Kandarin hard diary (+10%)",
    "kandarin_elite": "Kandarin elite diary (+15%)",
    "kourend_easy": "Kourend & Kebos easy diary (Hosidius herb patch disease free)",
    "kourend_hard": "Kourend & Kebos hard diary (+5% at Hosidius and the Farming Guild)",
    "falador_medium": "Falador medium diary (+10% XP at the Falador patch)",
    "falador_elite": "Falador elite diary (Falador Park tree patch disease free)",
    "morytania_elite": "Morytania elite diary (Harmony Island herb patch)",
    "fortis_champion": "Fortis Colosseum Champion rank (Civitas herb patch disease free)",
    "spirit_trees": "Spirit trees you planted (Port Sarim, Etceteria, Brimhaven, Hosidius, Farming Guild)",
    "fairy_staffless": "Lumbridge elite diary (fairy rings without a staff)",
}
# Requirements the route text leaves implicit, by place.
PLACE_NEEDS = {
    "troll stronghold": {"quests": ["My Arm's Big Adventure"]},
    "weiss": {"quests": ["Making Friends with My Arm"]},
    "harmony island": {"unlocks": ["morytania_elite"], "quests": ["The Great Brain Robbery"]},
    "prifddinas": {"quests": ["Song of the Elves"]},
    "lletya": {"quests": ["Mourning's End Part I"], "partial": True},
    "fossil island": {"quests": ["Bone Voyage"]},
    "port phasmatys": {"quests": ["Priest in Peril"]},
    "farming guild": {"levels": {"Farming": 45}},
    "kastori": {"quests": ["Children of the Sun"]},
    "civitas": {"quests": ["Children of the Sun"]},
    "ortus": {"quests": ["Children of the Sun"]},
    "auburn": {"quests": ["Children of the Sun"]},
    "nemus": {"quests": ["Children of the Sun"]},
    "locus": {"quests": ["Children of the Sun"]},
    "etceteria": {"quests": ["The Fremennik Trials"]},
}
# Patch levels inside the Farming Guild.
GUILD_LEVEL = {"herb": 65, "tree": 65, "fruit": 85, "celastrus": 85, "redwood": 90, "bush": 45, "allotment": 45,
               "flower": 45}
HERB_ONLY = {"troll stronghold", "weiss", "harmony island"}
DISEASE_FREE = {"troll stronghold": None, "weiss": None, "harmony island": None, "hosidius": "kourend_easy",
                "civitas": "fortis_champion", "falador park": "falador_elite"}


# Parsing ---------------------------------------------------------------------------------------

def _clean(s):
    return re.sub(r"\s+", " ", plain(re.sub(r"(?s)<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", s or ""))).strip()


def targets(cell):
    """Link targets in a cell, files and icons left out (their link= target counts when alone)."""
    out = []
    for m in LINK.finditer(cell or ""):
        t = m.group(1).strip()
        if not t.lower().startswith(("file:", "category:")) and t not in out:
            out.append(t)
    if not out:
        out = [x.strip() for x in re.findall(r"link=([^|\]]+)", cell or "")]
    return out


def labels(cell):
    """[(target, label)] for plain links, labels being what the Wiki shows (e.g. Tomatoes(5))."""
    return [(m.group(1).strip(), (m.group(2) or m.group(1)).strip()) for m in LINK.finditer(cell or "")
            if not m.group(1).lower().startswith(("file:", "category:"))]


def _num(s):
    m = re.search(r"-?[\d,]*\.?\d+", (s or "").replace("−", "-"))
    return float(m.group(0).replace(",", "")) if m else None


def growth(s):
    """Minutes from "4×20 mins", "6 × 160 mins" or "8 × 640 mins"."""
    m = re.search(r"(\d+)\s*[×x]\s*(\d+)\s*min", s or "")
    if m:
        return int(m.group(1)) * int(m.group(2))
    h = re.search(r"(\d+)\s*h", s or "")
    return int(h.group(1)) * 60 if h else None


def protection(cell):
    """(item, qty) a gardener takes, "free", "immune" or None."""
    t = _clean(cell).lower()
    if not t or t in ("n/a", "none", "-"):
        return None
    if "immune" in t:
        return "immune"
    if "free" in t:
        return "free"
    ls = labels(cell)
    if not ls:
        return None
    target, label = ls[0]
    name = label if re.search(r"\(\d+\)$", label) else target
    q = re.search(r"[×x]\s*(\d+)", _clean(cell))
    return [name, int(q.group(1)) if q else 1]


def _kind(name, table):
    n = (name or "").lower()
    for k in ("calquat", "celastrus", "redwood", "crystal", "spirit"):
        if k in n:
            return k
    if any(k in n for k in ("teak", "mahogany", "camphor", "ironwood", "rosewood")):
        return "hardwood"
    return {"herb_seeds": "herb", "tree_seeds": "tree", "fruit_seeds": "fruit", "hops_seeds": "hops",
            "bush_seeds": "bush", "allotment_seeds": "allotment", "flower_seeds": "flower"}.get(table)


def parse_seeds(table, text):
    """Crops from one seed table section: [{kind, seed, level, growth, protection, crop, xp...}]."""
    from .skillguides import tables
    out = []
    for head, rows in tables(text):
        h = [_clean(x).lower() for x in head]

        def col(*keys, avoid=()):
            for i, c in enumerate(h):
                if any(k in c for k in keys) and not any(a in c for a in avoid):
                    return i
            return None
        c_lv, c_seed = col("level"), col("seed", "sapling", "frag")
        c_prot = col("protection farmer") if col("protection farmer") is not None else col("protection")
        c_grow, c_crop = col("growth"), col("crop", "logs")
        c_plant = col("experience plant", "plant", avoid=("plant ", "uses")) if any("experience" in x for x in h) else None
        c_plant = next((i for i, c in enumerate(h) if c.startswith("experience plant")), c_plant)
        c_check = next((i for i, c in enumerate(h) if "check" in c), None)
        c_harv = next((i for i, c in enumerate(h) if "harvest" in c), None)
        c_total = next((i for i, c in enumerate(h) if c.startswith("experience total") or c == "xp"), None)
        if c_lv is None or c_seed is None:
            continue
        for r in rows:
            lv = _num(_clean(r[c_lv]))
            seed = (targets(r[c_seed]) or [None])[0]
            if not lv or not seed:
                continue
            kind = _kind(seed + " " + (_clean(r[c_seed])), table)
            if not kind:
                continue
            crop = (targets(r[c_crop]) or [None])[0] if c_crop is not None else None
            num = lambda c: _num(_clean(r[c])) if c is not None and _num(_clean(r[c])) is not None else None  # noqa: E731
            out.append({"kind": kind, "seed": seed, "name": re.sub(r"\s*(tree )?seed$|^Acorn$", "", seed).strip() or "Oak",
                        "level": int(str(_clean(r[c_lv])).split("/")[0].strip() or lv),
                        "growth": growth(_clean(r[c_grow])) if c_grow is not None else None,
                        "protection": protection(r[c_prot]) if c_prot is not None else None,
                        "crop": crop, "plantXp": num(c_plant), "checkXp": num(c_check), "harvestXp": num(c_harv),
                        "totalXp": num(c_total), "seedQty": _qty(r[c_seed])})
    return out


def _qty(cell):
    m = re.search(r"[×x]\s*(\d+)", _clean(cell))
    return int(m.group(1)) if m else None


def parse_birds(text):
    from .skillguides import tables
    out = []
    for head, rows in tables(text):
        for r in rows:
            item = (targets(r[0]) or [None])[0]
            vals = [_num(_clean(x)) for x in r[2:6]]
            if item and all(v is not None for v in vals):
                out.append({"item": item, "logs": _clean(r[1]), "craftLevel": int(vals[0]), "craftXp": vals[1],
                            "hunterLevel": int(vals[2]), "hunterXp": vals[3]})
        if out:
            break
    return out


def parse_bird_seeds(text):
    from .skillguides import tables
    out = []
    for head, rows in tables(text):
        for r in rows:
            seed = (targets(r[0]) or targets(r[1]) or [None])[0]
            amount = _num(_clean(r[2])) if len(r) > 2 else None
            if seed and amount:
                out.append({"seed": seed, "amount": int(amount)})
        if out:
            break
    return out


def parse_disease(text):
    """{plant label: {"cycles": n, "none"..."ultracompost": chance}} from the Wiki's table."""
    from .skillguides import tables
    out = {}
    for head, rows in tables(text):
        if "cycles" not in " ".join(_clean(x).lower() for x in head):
            continue
        for r in rows:
            vals = [re.match(r"\s*(\d+)\s*/\s*128", _clean(x)) for x in r[2:6]]
            if all(vals):
                out[_clean(r[0]).lower()] = {"cycles": int(_num(_clean(r[1]))),
                                             **{c[0]: int(v.group(1)) / 128 for c, v in zip(COMPOST, vals)}}
        break
    return out


def parse_herb_patches(text):
    """{place: bonus text} for herb patches."""
    from .skillguides import tables
    out = {}
    for head, rows in tables(text):
        h = [_clean(x).lower() for x in head]
        if "location" not in h:
            continue
        li, bi = h.index("location"), next((i for i, c in enumerate(h) if "bonus" in c), None)
        for r in rows:
            if "herb" in _clean(r[0]).lower():
                out[_clean(r[li])] = _clean(r[bi]) if bi is not None else ""
    return out


def parse_yield(text):
    """(low, high) chance-to-save values from a seed page's {{Farming yield calculator}}."""
    m = re.search(r"\{\{\s*Farming yield calculator([^}]*)\}\}", text or "", re.I)
    if not m:
        return None
    p = dict(x.split("=", 1) for x in m.group(1).split("|") if "=" in x)
    try:
        return int(p["low"].strip()), int(p["high"].strip())
    except (KeyError, ValueError):
        return None


KIND_WORDS = [("calquat tree", "calquat"), ("celastrus tree", "celastrus"), ("fruit tree", "fruit"),
              ("calquat", "calquat"), ("celastrus", "celastrus"), ("hardwood", "hardwood"), ("redwood", "redwood"),
              ("allotment", "allotment"), ("flower", "flower"), ("herb", "herb"), ("hop", "hops"), ("bush", "bush"),
              ("tree", "tree")]


def parse_runs(text):
    """{run title: {"needs": [text], "recommend": [text], "example": {...}, "stops": [...]}}."""
    from . import setups
    out = {}
    hs = [(m.start(), m.end(), m.group(1).strip()) for m in re.finditer(r"(?m)^==\s*([^=\n]+?)\s*==\s*$", text)]
    for k, (s, e, title) in enumerate(hs):
        body = text[e:hs[k + 1][0] if k + 1 < len(hs) else len(text)]
        subs = {m.group(1).strip().lower(): m.end() for m in re.finditer(r"(?m)^===\s*([^=\n]+?)\s*===", body)}

        def sub(name):
            for t, pos in subs.items():
                if name in t:
                    nxt = min([p for p in subs.values() if p > pos] + [len(body)])
                    return body[pos:nxt]
            return ""
        needs = [_clean(l) for l in sub("requirements").splitlines() if l.startswith("*")]
        recs = [_clean(l) for l in sub("recommendations").splitlines() if l.startswith("*")]
        exs = setups.examples(sub("equipment and inventory") or sub("inventory"))
        seq = next((sub(t) for t in subs if "sequence" in t), "")
        stops = []
        for line in seq.splitlines():
            if re.match(r"#(?!#)", line):
                raw = line[1:].strip()
                txt = _clean(raw).lower()
                # Patch kinds are named after the place ("[[Catherby]] fruit tree patch"), so the
                # place name itself ("Tree Gnome Village", "Catherby") is not read for them.
                after = _clean(raw[raw.find("]]") + 2:] if "]]" in raw else raw).lower()
                kinds, rest = [], after
                for word, kind in KIND_WORDS:
                    if re.search(r"\b" + word + r"s?\b", rest):
                        kinds.append(kind)
                        rest = re.sub(r"\b" + word + r"s?\b", " ", rest)
                place = (targets(raw) or [_clean(raw).split(" patch")[0]])[0]
                if "start at" in txt or not re.search(r"patch", txt):
                    continue
                needs_line = re.search(r"\((?:requires?|needs?)\s+(.+?)\)", raw, re.I)
                stops.append({"place": place, "text": _clean(raw).rstrip(":"), "kinds": kinds,
                              "needs": needs_line.group(1) if needs_line else "", "teleports": []})
            elif re.match(r"##(?!#)", line) and stops:
                stops[-1]["teleports"].append(line[2:].strip())
        if stops or exs:
            out[title] = {"needs": needs, "recommend": recs, "example": exs[0] if exs else None, "stops": stops}
    return out


# Fetching --------------------------------------------------------------------------------------

def fetch_all(client, sleep=1.0):
    text = "".join(f"\n@@@{k}@@@\n{v}\n" for k, v in TABLES) + "\n@@@end@@@\n"
    d = client.wiki_api({"action": "expandtemplates", "text": text, "prop": "wikitext"})
    w = d.get("expandtemplates", {}).get("wikitext", "")

    def seg(k):
        i = w.find(f"@@@{k}@@@")
        j = w.find("\n@@@", i + len(k) + 6)
        return w[i:j] if i >= 0 else ""
    crops = []
    for key, _ in TABLES:
        if key.endswith("_seeds") and key != "bird_seeds":
            crops += parse_seeds(key, seg(key))
    time.sleep(sleep)
    r = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                         "titles": "Farming runs", "redirects": "1"})
    pages = r.get("query", {}).get("pages", [])
    runs = parse_runs(pages[0]["revisions"][0]["slots"]["main"]["content"]) if pages and pages[0].get("revisions") else {}
    time.sleep(sleep)
    seeds = sorted({c["seed"] for c in crops if c["kind"] in HERB_LIKE})
    yields = {}
    for k in range(0, len(seeds), 50):
        r = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                             "titles": "|".join(seeds[k:k + 50]), "redirects": "1"})
        for p in r.get("query", {}).get("pages", []):
            if p.get("revisions"):
                y = parse_yield(p["revisions"][0]["slots"]["main"]["content"])
                if y:
                    yields[p["title"]] = y
        time.sleep(sleep)
    for c in crops:
        if c["seed"] in yields:
            c["yield"] = list(yields[c["seed"]])
    return {"source": "OSRS Wiki farming pages (CC BY-NC-SA 3.0)", "version": VERSION, "fetched": int(time.time()),
            "crops": crops, "runs": runs, "birds": parse_birds(seg("birds")), "birdSeeds": parse_bird_seeds(seg("bird_seeds")),
            "disease": parse_disease(seg("disease")), "herbPatches": parse_herb_patches(seg("herb_patches"))}


_cache = {}


def load(db=None):
    if db is not None:
        d = db.kv_get("farming_wiki")
        if d and d.get("crops") and d.get("version") == VERSION:
            return d
    if "snap" not in _cache:
        try:
            with open(SNAPSHOT, encoding="utf-8") as f:
                _cache["snap"] = json.load(f)
        except (OSError, ValueError):
            _cache["snap"] = {"crops": [], "runs": {}, "birds": [], "birdSeeds": [], "disease": {}, "herbPatches": {}}
    return _cache["snap"]


def refresh(db, client, max_age=7 * 86400):
    d = db.kv_get("farming_wiki") or {}
    if d.get("fetched") and time.time() - d["fetched"] < max_age and d.get("version") == VERSION:
        return 0
    data = fetch_all(client)
    if len(data["crops"]) >= 30 and data["runs"]:
        db.kv_set("farming_wiki", data)
    return len(data["crops"])


TOOLS = ["Spade", "Rake", "Seed dibber", "Secateurs", "Magic secateurs", "Watering can(8)", "Gricoller's can",
         "Bottomless compost bucket", "Farmer's strawhat", "Farmer's jacket", "Farmer's shirt", "Farmer's boro trousers",
         "Farmer's boots", "Farming cape", "Farming cape(t)", "Dramen staff", "Lunar staff", "Hammer", "Chisel",
         "Clockwork", "Digsite pendant", "Quest point cape", "Ectophial", "Xeric's talisman", "Explorer's ring 2",
         "Explorer's ring 3", "Explorer's ring 4", "Ardougne cloak 1", "Ardougne cloak 2", "Ardougne cloak 3",
         "Ardougne cloak 4", "Music cape", "Skills necklace", "Combat bracelet", "Ring of dueling", "Slayer ring",
         "Amulet of glory", "Quetzal whistle", "Perfected quetzal whistle", "Royal seed pod", "Teleport crystal",
         "Eternal teleport crystal", "Pendant of ates", "Stony basalt", "Icy basalt", "Construction cape",
         "Max cape", "Ring of wealth", "Ring of the elements", "Kharedst's memoirs", "Book of the dead",
         "Spirit tree", "Sailors' amulet"]


def referenced_names(data):
    """Item names the runs use: teleport options, example setups and farming tools (seeds and
    produce are GE items, found by name)."""
    out = set(TOOLS)
    for run in (data or {}).get("runs", {}).values():
        for st in run.get("stops", []):
            for opt in st.get("teleports", []):
                out.update(t for t in targets(opt) if not re.search(r"(?i)spellbook|\(location\)", t))
        ex = run.get("example") or {}
        out.update(ex.get("eq", {}).values())
        out.update(re.split(r"[;\\]", c[0])[0] for c in (ex.get("inv") or []) + (ex.get("rp") or []) if c)
    return {n for n in out if n}


# Planning --------------------------------------------------------------------------------------

def chance_to_save(low, high, level, bonus=0.0):
    """The Wiki's chance to keep a harvest life on a pick, at a Farming level."""
    lv = max(1, min(99, level))
    base = (1 + math.floor(low * (99 - lv) / 98 + high * (lv - 1) / 98 + 0.5)) / 256
    return min(0.98, base * (1 + bonus))


def expected_harvest(lives, cts):
    return lives / (1 - cts)


class Context:
    """Everything about the account a run plan depends on."""

    def __init__(self, db, engine, acct=None, unlocks=None):
        from . import account, planner, quests, setups
        self.db, self.engine = db, engine
        self.acct = acct or planner.main_account(db)
        prog = account.progress(db, self.acct) if self.acct else {"levels": {}, "quests": {}, "known": False}
        self.levels = {k: (v[0] if isinstance(v, (list, tuple)) else v) for k, v in prog["levels"].items()}
        self.known = bool(prog.get("known"))
        self.states = {norm(k): v for k, v in prog["quests"].items()}
        self.quest_names = {norm(k): k for k in quests.load(db)}
        self.own = setups.owned(db, self.acct)
        self.items = setups.Items(engine, setups.load(db).get("items"))
        self.ticked = set(unlocks if unlocks is not None else (db.kv_get("farming_unlocks") or []))
        self.auto = derived_unlocks(db, self.acct)
        self.unlocks = self.ticked | set(self.auto)
        from . import patches
        self.pace = patches.pace(db, self.acct) if self.acct else {}
        self.data = load(db)
        self.disease = self.data.get("disease") or {}
        self._find = {}

    def level(self, skill):
        return self.levels.get(skill) or (1 if self.known else 99)

    def quest(self, name, partial=False):
        """True when done (or started, for partial), None when the account is unknown."""
        if not self.states:
            return None
        st = self.states.get(norm(name), 0)
        return st >= (1 if partial else 2)

    def find(self, name):
        if name not in self._find:
            self._find[name] = self.items.find(name)
        return self._find[name]

    def has(self, name):
        info = self.find(name)
        return bool(info) and any(self.own.get(i) for i in info.get("ids") or [info["id"]])

    def price(self, name, side="buy"):
        """Instant-buy price to buy, instant-sell price after tax to sell. A lone stale trade far
        from the other side (Woad leaf "bought" at 48,984 while selling at 112) is ignored."""
        info = self.find(name)
        r = self.engine.by_id.get(info["id"]) if info else None
        if not r:
            return None
        hi, lo = r.get("high"), r.get("low")
        if hi and lo and hi > 2 * lo:
            hi = lo
        p = (lo or hi) if side == "sell" else (hi or lo)
        if p and side == "sell":
            p -= self.engine.tax(p, info["id"])
        return p


# Diary tiers that set the unlocks above: (region as the plugin names it, tier 0 to 3).
DIARY_UNLOCKS = {"kandarin_medium": ("Kandarin", 1), "kandarin_hard": ("Kandarin", 2), "kandarin_elite": ("Kandarin", 3),
                 "kourend_easy": ("Kourend & Kebos", 0), "kourend_hard": ("Kourend & Kebos", 2),
                 "falador_medium": ("Falador", 1), "falador_elite": ("Falador", 3),
                 "morytania_elite": ("Morytania", 3), "fairy_staffless": ("Lumbridge & Draynor", 3)}


def derived_unlocks(db, acct):
    """{unlock: why} the plugin has seen: diary tiers, and a grown spirit tree of yours."""
    from . import account, patches
    out = {}
    d = account.diaries(db, acct) if acct else {}
    for key, (region, tier) in DIARY_UNLOCKS.items():
        tiers = d.get(region) or []
        if len(tiers) > tier and tiers[tier]:
            out[key] = "diary"
    if acct and any(p["kind"] == "spirit" and p["crop"] for p in patches.status(db, acct)["patches"]):
        out["spirit_trees"] = "patch"
    return out


def _needs(stop, kind, ctx, run_title):
    """What a stop still needs on this account: [text]."""
    out = []
    place = stop["place"].lower()
    text = (stop.get("text") or "").lower() + " " + (stop.get("needs") or "").lower()
    for key, need in PLACE_NEEDS.items():
        if key in place or key in text.split(" patch")[0]:
            for q in need.get("quests", []):
                ok = ctx.quest(q, need.get("partial", False))
                if ok is False:
                    out.append(("Start " if need.get("partial") else "") + q)
            for u in need.get("unlocks", []):
                if u not in ctx.unlocks:
                    out.append(UNLOCKS[u].split(" (")[0])
            for sk, lv in need.get("levels", {}).items():
                if ctx.level(sk) < lv:
                    out.append(f"{lv} {sk}")
    for t in targets(stop.get("needs") or ""):
        if norm(t) in ctx.quest_names and ctx.quest(ctx.quest_names[norm(t)]) is False:
            n = ctx.quest_names[norm(t)]
            if n not in out:
                out.append(n)
    m = re.search(r"(\d+)\s+farming", text)
    lv = int(m.group(1)) if m else None
    if "farming guild" in place:
        lv = max(lv or 0, GUILD_LEVEL.get(kind, 45))
    if lv and ctx.level("Farming") < lv:
        out.append(f"{lv} Farming")
    return sorted(set(out), key=out.index)


TELEPORT_RULES = [
    (r"fairy ring", "Fairytale II - Cure a Queen", True),
    (r"spirit tree", "Tree Gnome Village", False),
    (r"lunar spellbook|catherby teleport", "Lunar Diplomacy", False),
    (r"ectophial", "Ghosts Ahoy", False),
    (r"quetzal", "Children of the Sun", False),
    (r"stony basalt|icy basalt", "Making Friends with My Arm", False),
    (r"ardougne teleport|teleport to ardougne", "Plague City", False),
    (r"watchtower teleport", "Watchtower", False),
    (r"trollheim teleport|teleport to trollheim", "Eadgar's Ruse", False),
]
# Standard spellbook teleports: (pattern, Magic level, the tablet sold on the GE).
SPELLS = [(r"varrock", 25, "Varrock teleport"), (r"lumbridge", 31, "Lumbridge teleport"),
          (r"falador", 37, "Falador teleport"), (r"(?:to )?(?:your )?house|house portal", 40, "Teleport to house"),
          (r"camelot", 45, "Camelot teleport"), (r"ardougne", 51, "Ardougne teleport"),
          (r"watchtower", 58, "Watchtower teleport")]


def _spell(low, ctx):
    """(tablet, needs a tablet) when an option starts with a standard spellbook teleport."""
    for pat, lv, tablet in SPELLS:
        if re.search(r"^teleport (?:to )?(?:the centre of )?(?:" + pat + r")\b|^(?:use (?:a |the )?)?(?:" + pat +
                     r") teleport\b", low):
            return tablet, ctx.level("Magic") < lv or "tablet" in low
    return None, False


PLANTED_SPIRIT = r"spirit tree.*\b(port sarim|etceteria|brimhaven|hosidius|farming guild)\b|player.planted"


def item_groups(opt, ctx):
    """Items an option names, as groups of alternatives: "a [[skills necklace]] or [[Fishing
    cape]]" is one group, "a [[ring of dueling]]. Bring [[logs]]" two."""
    groups, last_end = [], None
    for m in LINK.finditer(opt):
        t = m.group(1).strip()
        info = ctx.find(t)
        if not info or norm(t) in ctx.quest_names or t.lower().startswith(("file:", "category:")):
            continue
        between = opt[last_end:m.start()] if last_end is not None else ""
        if groups and re.fullmatch(r"[\s,]*(?:or|,)?[\s,]*(?:an?|the)?\s*", between.lower()):
            if info not in groups[-1]:
                groups[-1].append(info)
        else:
            groups.append([info])
        last_end = m.end()
    return groups


def pick_teleport(options, ctx):
    """The quickest option you can use: {text, index, bring, buy, missing}. Items in an option are
    alternatives when listed with "or"; an owned one is brought, else the cheapest sold one is
    bought. The Wiki lists options quickest first; an option that needs buying counts 1.5 places
    slower, plus one for each 20k it costs, and when none works, the one missing the least is shown."""
    tried = []
    for i, opt in enumerate(options):
        low = _clean(opt).lower()
        bring, buy, missing = [], [], []
        for pat, quest, partial in TELEPORT_RULES:
            if quest and re.search(pat, low) and ctx.quest(quest, partial) is False:
                missing.append(quest)
        if re.search(PLANTED_SPIRIT, low) and "spirit_trees" not in ctx.unlocks:
            missing.append("Your own spirit tree there")
        if "fairy ring" in low and "fairy_staffless" not in ctx.unlocks:
            staff = next((n for n in ("Dramen staff", "Lunar staff") if ctx.has(n)), None)
            if staff:
                bring.append(staff)
            elif ctx.own:
                missing.append("Dramen staff")
            else:
                bring.append("Dramen staff")
        for sk, lv in re.findall(r"\{\{\s*SCP\s*\|\s*([A-Za-z]+)\s*\|\s*(\d+)", opt) + \
                [(b, a) for a, b in re.findall(r"requires? (\d+) ([A-Z][a-z]+)", _clean(opt))]:
            if sk in SKILLS and ctx.level(sk) < int(lv) and f"{lv} {sk}" not in missing:
                missing.append(f"{lv} {sk}")
        groups = item_groups(opt, ctx)
        for g in groups:
            own = next((x for x in g if ctx.has(x["name"])), None)
            sold = sorted((x for x in g if x.get("tradeable")), key=lambda x: ctx.price(x["name"]) or 1e12)
            if own:
                bring.append(own["name"])
            elif sold:
                buy.append(sold[0]["name"])
            elif ctx.own or ctx.known:
                missing.append(g[0]["name"] + (" (or " + ", ".join(x["name"] for x in g[1:]) + ")" if len(g) > 1 else ""))
            else:
                bring.append(g[0]["name"])
        tablet, need_tab = _spell(low, ctx) if not groups else (None, False)
        if tablet and need_tab:
            (bring if ctx.has(tablet) else buy).append(tablet)
        tried.append({"text": _clean(opt), "index": i, "bring": bring + buy, "buy": buy, "missing": missing,
                      "spell": bool(tablet and not need_tab)})
    ok = [t for t in tried if not t["missing"]]
    if ok:
        # Buying costs a place and more for dear items: a 770k ring is no teleport for a herb patch.
        def cost(t):
            gp = sum(ctx.price(n) or 0 for n in t["buy"])
            return t["index"] + (1.5 + min(10.0, gp / 20000) if t["buy"] else 0)
        return min(ok, key=cost)
    if tried:
        return min(tried, key=lambda t: (len(t["missing"]), t["index"]))
    return {"text": "", "index": 0, "bring": [], "buy": [], "missing": [], "spell": False}


def run_requirements(run, ctx):
    """The run's requirement lines tied to stops they name ({place: [needs]}), the rest as notes."""
    per, notes = {}, []
    places = [st["place"] for st in run.get("stops", [])]
    for line in run.get("needs") or []:
        low = line.lower()
        if re.match(r"level \d+ farming", low) or "to plant" in low:
            continue  # crop levels are handled by the crop choice
        stop = next((p for p in places if re.sub(r"\s*\(location\)$", "", p).lower() in low), None)
        need = []
        for qn in ctx.quest_names.values():
            if re.search(r"\b" + re.escape(qn.lower()) + r"\b", low) and ctx.quest(qn) is False:
                need.append(qn)
        for lv, sk in re.findall(r"(\d+)\s+([A-Z][a-z]+)", line):
            if sk in SKILLS and sk != "Farming" and ctx.level(sk) < int(lv):
                need.append(f"{lv} {sk}")
        if stop:
            per.setdefault(stop, []).extend(need)
        elif need:
            notes.append(line)
    return per, notes


SKILLS = {"Attack", "Strength", "Defence", "Ranged", "Prayer", "Magic", "Runecraft", "Construction", "Hitpoints",
          "Agility", "Herblore", "Thieving", "Crafting", "Fletching", "Slayer", "Hunter", "Mining", "Smithing",
          "Fishing", "Cooking", "Firemaking", "Woodcutting", "Farming", "Sailing"}


def _disease(kind, crop, compost):
    """The Wiki's disease row for a crop. It lists herbs, fruit trees, maples and magic trees; the
    other quick crops use the herb row and the other trees the maple row (an approximation)."""
    if kind in ("herb", "allotment", "hops", "flower", "bush"):
        return "all herbs"
    if kind in ("fruit", "calquat", "celastrus"):
        return "all fruit trees"
    if crop and "magic" in crop["name"].lower():
        return "magic tree"
    return "maple trees"


def survival(ctx, kind, crop, compost, disease_free=False):
    if disease_free:
        return 1.0
    d = ctx.disease.get(_disease(kind, crop, compost))
    if not d:
        return 1.0
    return (1 - d[compost]) ** d["cycles"]


def outfit_bonus(ctx):
    """Farmer's outfit XP bonus for the pieces you own (2.5% for the set)."""
    parts = [("Farmer's strawhat", 0.004), ("Farmer's jacket", 0.008), ("Farmer's boro trousers", 0.006),
             ("Farmer's boots", 0.002)]
    owned = [b for n, b in parts if ctx.has(n) or ctx.has(n.replace("Farmer's jacket", "Farmer's shirt"))]
    return 0.025 if len(owned) == 4 else sum(owned)


def seed_qty(crop):
    """Seeds per patch, as the seed table states (allotments 3, hops 3 or 4), else one."""
    if crop.get("seedQty"):
        return crop["seedQty"]
    return 3 if crop["kind"] == "allotment" else 4 if crop["kind"] == "hops" else 1


def crop_yield(crop, crops):
    """The seed's (low, high) yield values; a seed page without them borrows the nearest level's."""
    if crop.get("yield"):
        return crop["yield"]
    near = sorted((abs(c["level"] - crop["level"]), c["yield"]) for c in crops
                  if c["kind"] == crop["kind"] and c.get("yield"))
    return near[0][1] if near else (40, 80)


def sapling(crop, ctx):
    """What to buy and plant: the sapling when the GE sells one, else the seed."""
    if crop["kind"] in TREE_LIKE or crop["kind"] == "crystal":
        base = re.sub(r"\s*(tree )?seed$", "", crop["seed"]).replace("Acorn", "Oak").strip()
        for cand in (base + " sapling", base.split(" ")[0] + " sapling"):
            if ctx.find(cand):
                return cand
    return crop["seed"]


XP_GP = 50  # Most XP mode pays up to this many gp for an extra XP (so no 650k gout tubers)
# Flowers that keep birds and pests off an allotment: a guarded allotment cannot catch disease.
FLOWER_GUARDS = {"Marigold": {"Potato", "Onion", "Tomato"}, "Rosemary": {"Cabbage"},
                 "Nasturtium": {"Watermelon"}, "White lily": None}


def utility(o, mode, kind):
    """How good a crop option is for the mode (higher is better)."""
    if mode == "xp":
        return o["xp"] - (o["cost"] - o["value"]) / XP_GP
    if kind in TREE_LIKE and o["value"] < o["cost"]:
        return -(o["gpPerXp"] if o["gpPerXp"] is not None else 1e9) / 1000  # trees: least gp per XP
    return o["profit"]


def crop_metrics(crop, ctx, mode, patch=None):
    """Per patch expected XP, cost, value and profit for a crop, with the best compost and payment."""
    kind = crop["kind"]
    patch = patch or {}
    lv = ctx.level("Farming")
    xp_mult = (1 + outfit_bonus(ctx)) * (1.1 if patch.get("falador") else 1.0)
    plant = sapling(crop, ctx)
    seed_cost = (ctx.price(plant) or 0) * seed_qty(crop)
    produce = ctx.price(crop["crop"], "sell") if crop.get("crop") else None
    prot = crop.get("protection") if isinstance(crop.get("protection"), list) else None
    pay_cost = (ctx.price(prot[0]) or 0) * prot[1] if prot else 0
    safe = bool(patch.get("diseaseFree") or patch.get("guarded") or crop.get("protection") == "immune")
    pays = [False, True] if prot and not safe else [False]
    options = []
    if kind in HERB_LIKE:
        low, high = crop_yield(crop, ctx.data.get("crops", []))
        bonus = (0.10 if ctx.has("Magic secateurs") else 0) + (0.05 if ctx.has("Farming cape") else 0) + patch.get("yieldBonus", 0)
        cts = chance_to_save(low, high, lv, bonus)
        for name, item, tier in COMPOST:
            for pay in pays:
                surv = 1.0 if (pay or safe) else survival(ctx, kind, crop, name)
                n = expected_harvest(3 + tier, cts) * surv
                xp = surv * ((crop.get("plantXp") or 0) + n * (crop.get("harvestXp") or 0)) * xp_mult
                cost = seed_cost + ((ctx.price(item) or 0) if item else 0) + (pay_cost if pay else 0)
                options.append({"compost": name, "pay": pay, "xp": xp, "cost": cost, "value": n * (produce or 0),
                                "harvest": n, "survival": surv})
    else:
        base_xp = (crop.get("totalXp") or ((crop.get("plantXp") or 0) + (crop.get("checkXp") or 0)))
        fruit = 6 if kind in ("fruit", "calquat") else 4 if kind == "bush" else 1 if kind == "flower" else 0
        if kind == "bush":
            base_xp += 4 * (crop.get("harvestXp") or 0)
        if kind == "calquat":
            base_xp += 6 * (crop.get("harvestXp") or 0)
        for name, item, tier in COMPOST:
            for pay in pays:
                surv = 1.0 if (pay or safe) else survival(ctx, kind, crop, name)
                cost = seed_cost + ((ctx.price(item) or 0) if item else 0) + (pay_cost if pay else 0)
                options.append({"compost": name, "pay": pay, "xp": surv * base_xp * xp_mult, "cost": cost,
                                "value": surv * fruit * (produce or 0), "harvest": fruit, "survival": surv})
    for o in options:
        o["profit"] = o["value"] - o["cost"]
        o["gpPerXp"] = (o["cost"] - o["value"]) / o["xp"] if o["xp"] else None
    best = max(options, key=lambda o: (round(utility(o, mode, kind), 3), o["profit"]))
    return dict(best, crop=crop["name"], seed=plant, seedQty=seed_qty(crop), level=crop["level"], kind=kind,
                growth=crop.get("growth"), protection=crop.get("protection"), produce=crop.get("crop"),
                producePrice=produce, seedPrice=ctx.price(plant), guarded=bool(patch.get("guarded")),
                score=utility(best, mode, kind))


def choose_crop(kind, ctx, mode, patch=None):
    """The best crop for a kind of patch at your level, with the next best for comparison."""
    lv = ctx.level("Farming")
    crops = [c for c in ctx.data.get("crops", []) if c["kind"] == kind and c["level"] <= lv]
    if not crops:
        return None, []
    rows = sorted((crop_metrics(c, ctx, mode, patch) for c in crops), key=lambda r: -r["score"])
    return rows[0], rows[:6]


def guards(flower, allotment):
    g = FLOWER_GUARDS.get(flower, False)
    return g is None or (g is not False and allotment in g)


def choose_allotment(ctx, mode):
    """Allotment and flower chosen together: a flower that guards the allotment saves its compost
    or payment, so a weaker flower can win."""
    lv = ctx.level("Farming")
    allots = [c for c in ctx.data.get("crops", []) if c["kind"] == "allotment" and c["level"] <= lv]
    flowers = [c for c in ctx.data.get("crops", []) if c["kind"] == "flower" and c["level"] <= lv]
    if not allots or not flowers:
        return None
    fm = {f["name"]: crop_metrics(f, ctx, mode) for f in flowers}
    best = None
    for a in allots:
        plain_ = crop_metrics(a, ctx, mode)
        guarded = crop_metrics(a, ctx, mode, {"guarded": True})
        for f in flowers:
            am = guarded if guards(f["name"], a["name"]) else plain_
            score = 2 * am["score"] + fm[f["name"]]["score"]  # two allotments to a flower patch
            if best is None or score > best[0]:
                best = (score, am, fm[f["name"]])
    return {"allotment": best[1], "flower": best[2]}


def _patch_traits(stop, ctx):
    place = stop["place"].lower() + " " + (stop.get("text") or "").lower()
    free = False
    for key, unlock in DISEASE_FREE.items():
        if key in place and (unlock is None or unlock in ctx.unlocks):
            free = True
    bonus = 0.0
    if "catherby" in place:
        bonus = 0.15 if "kandarin_elite" in ctx.unlocks else 0.10 if "kandarin_hard" in ctx.unlocks else \
            0.05 if "kandarin_medium" in ctx.unlocks else 0.0
    if ("hosidius" in place or "farming guild" in place) and "kourend_hard" in ctx.unlocks:
        bonus += 0.05
    return {"diseaseFree": free, "yieldBonus": bonus,
            "falador": "falador" in place and "park" not in place and "falador_medium" in ctx.unlocks}


def _crop(ctx, kind, name):
    return next(x for x in ctx.data["crops"] if x["kind"] == kind and x["name"] == name)


def plan_run(key, ctx, mode="xp"):
    """One run planned for the account: stops, crops, totals, shopping."""
    title, kinds = RUNS[key]
    run = ctx.data.get("runs", {}).get(title) or {"stops": [], "needs": [], "example": None}
    chosen, alts = {}, {}
    for k in kinds:
        chosen[k], alts[k] = choose_crop(k, ctx, mode)
    pair = choose_allotment(ctx, mode) if "allotment" in kinds and "flower" in kinds else None
    if pair:
        chosen.update(pair)
    stops = []
    per, notes = run_requirements(run, ctx)
    for s in run["stops"]:
        ks = [k for k in s["kinds"] if k in kinds]
        if not ks:
            ks = (["herb"] if any(h in s["place"].lower() for h in HERB_ONLY) else kinds) if key == "allotment" else kinds[:1]
        for k in ks:
            count = 2 if k == "allotment" else 3 if (k == "hardwood" and "fossil" in s["place"].lower()) else 1
            need = _needs(s, k, ctx, title) + per.get(s["place"], [])
            c = chosen.get(k)
            if not c:
                need.append(f"{min((x['level'] for x in ctx.data['crops'] if x['kind'] == k), default=1)} Farming")
            need = sorted(set(need), key=need.index)
            traits = _patch_traits(s, ctx)
            if pair and k in ("allotment", "flower"):
                # The pair chosen for the run, the flower guarding both allotments.
                traits["guarded"] = k == "allotment" and pair["allotment"]["guarded"]
                m = crop_metrics(_crop(ctx, k, c["crop"]), ctx, mode, traits)
            elif c:
                # Each patch gets its own best crop: a disease free or bonus patch can favour another.
                m = choose_crop(k, ctx, mode, traits)[0]
            else:
                m = None
            stops.append({"place": re.sub(r"\s*\(location\)$", "", s["place"]), "kind": k, "count": count,
                          "usable": not need, "needs": need, "traits": traits, "crop": m})
    # Teleports once per place, for usable stops.
    by_place = {}
    for s, raw in [(x, next((r for r in run["stops"] if re.sub(r"\s*\(location\)$", "", r["place"]) == x["place"]), None))
                   for x in stops]:
        if s["usable"] and raw and s["place"] not in by_place:
            by_place[s["place"]] = pick_teleport(raw["teleports"], ctx)
    for s in stops:
        s["teleport"] = by_place.get(s["place"])
    usable = [s for s in stops if s["usable"] and s["crop"]]
    places = []
    for s in usable:
        if s["place"] not in places:
            places.append(s["place"])
    minutes = 0.0
    measured = False
    for p in places:
        ks = [s for s in usable if s["place"] == p]
        group = "trees" if all(s["kind"] in TREE_LIKE for s in ks) else "patches"
        if ctx.pace.get(group):
            minutes += ctx.pace[group]["minutes"]  # your own time from one stop to the next
            measured = True
            continue
        minutes += max(STOP_MINUTES.get(s["kind"], 1.0) for s in ks) + 0.3 * (len(ks) - 1)
        minutes += SLOWER_TELEPORT * (by_place.get(p) or {}).get("index", 0)
        minutes += 2.0 if (by_place.get(p) or {}).get("missing") else 0  # no teleport: a walk or a slower route
    xp = sum(s["crop"]["xp"] * s["count"] for s in usable)
    profit = sum(s["crop"]["profit"] * s["count"] for s in usable)
    cost = sum(s["crop"]["cost"] * s["count"] for s in usable)
    grow = max([s["crop"]["growth"] or 0 for s in usable] or [0])
    runs_day = min(1440 / grow, MAX_RUNS_DAY) if grow else 0
    return {"key": key, "title": title, "kinds": kinds, "mode": mode, "stops": stops,
            "crops": {k: chosen[k] for k in kinds}, "alternatives": alts,
            "patches": sum(s["count"] for s in usable), "places": len(places),
            "xp": xp, "profit": profit, "cost": cost, "minutes": round(minutes, 1), "growth": grow,
            "xpPerHour": xp / minutes * 60 if minutes else 0, "profitPerHour": profit / minutes * 60 if minutes else 0,
            "perDay": {"runs": runs_day, "xp": xp * runs_day, "profit": profit * runs_day},
            "locked": [s for s in stops if not s["usable"]], "notes": notes, "measured": measured,
            "url": WIKI + "Farming_runs#" + title.replace(" ", "_"), "example": run.get("example")}


def plan_birdhouses(ctx, mode="xp"):
    """Bird house run on Fossil Island: the best house you can build and place, and seeds."""
    hunter, craft = ctx.level("Hunter"), ctx.level("Crafting")
    houses = [b for b in ctx.data.get("birds", []) if b["hunterLevel"] <= hunter]
    need = []
    if ctx.quest("Bone Voyage") is False:
        need.append("Bone Voyage")
    if not houses:
        return {"key": "birdhouse", "title": "Bird house run", "usable": False, "needs": need + ["5 Hunter"]}
    best = houses[-1]
    built = craft >= best["craftLevel"]
    seeds = []
    for s in ctx.data.get("birdSeeds", []):
        p = ctx.price(s["seed"])
        if p:
            seeds.append((p * s["amount"], s["seed"], s["amount"]))
    seed_cost, seed, amount = min(seeds) if seeds else (0, "Barley seed", 10)
    logs = (best["logs"] + " logs") if best["logs"].lower() != "regular" else "Logs"
    house_cost = ((ctx.price(logs) or 0) + (ctx.price("Clockwork") or 0)) if built else (ctx.price(best["item"]) or 0)
    xp = 4 * best["hunterXp"]
    craft_xp = 4 * best["craftXp"] if built else 0
    cost = 4 * (seed_cost + house_cost)
    minutes = 3.0 + (0 if ctx.has("Digsite pendant") else 0.5)
    return {"key": "birdhouse", "title": "Bird house run", "usable": not need, "needs": need, "house": best,
            "build": built, "seed": seed, "seedAmount": amount, "xp": xp, "craftXp": craft_xp, "cost": cost,
            "profit": -cost, "minutes": minutes, "growth": 50, "xpPerHour": xp / minutes * 60,
            "profitPerHour": -cost / minutes * 60, "perDay": {"runs": MAX_RUNS_DAY, "xp": xp * MAX_RUNS_DAY},
            "logs": logs, "url": WIKI + "Bird_house_trapping",
            "note": "Nests (seeds, rings and clue nests) are a bonus not counted here."}


SUPER_INGREDIENTS = ["Pineapple", "Watermelon", "Calquat fruit", "Papaya fruit", "Coconut shell", "Jangerberries",
                     "Whiteberries", "Poison ivy berries", "Cactus spine", "Grimy avantoe", "Grimy kwuarm",
                     "Grimy snapdragon", "Grimy cadantine", "Grimy lantadyme", "Grimy dwarf weed", "Grimy torstol"]


def plan_compost(ctx):
    """Make or buy compost: a bin takes 15 ingredients (the Farming Guild's big bin 30) and 25
    volcanic ash (50) turns a bin of supercompost into ultracompost."""
    rows = sorted((ctx.price(n) or 1e12, n) for n in SUPER_INGREDIENTS)
    cost, ing = rows[0] if rows and rows[0][0] < 1e12 else (None, None)
    sup, ultra, ash = ctx.price("Supercompost"), ctx.price("Ultracompost"), ctx.price("Volcanic ash")
    sup_sell = ctx.price("Supercompost", "sell")
    make_ultra = (cost or 0) + 25 * (ash or 0) / 15 if cost is not None and ash else None
    return {"key": "supercompost", "title": "Supercompost run", "ingredient": ing, "ingredientPrice": cost,
            "supercompost": sup, "ultracompost": ultra, "volcanicAsh": ash,
            "saveSuper": (sup - cost) if sup and cost is not None else None,
            "profitSell": (sup_sell - cost) if sup_sell and cost is not None else None,
            "makeUltra": make_ultra, "saveUltra": (ultra - make_ultra) if ultra and make_ultra else None,
            "bins": 4 + (2 if ctx.level("Farming") >= 45 else 0),  # Catherby, Ardougne, Falador, Phasmatys, guild 2x
            "url": WIKI + "Farming_runs#Supercompost_run"}


# Runs that cover the same patches: a routine takes one option from each group.
GROUPS = [("Herbs and allotments", [["allotment"], ["herb"]]),
          ("Trees", [["combined"], ["tree", "fruit"], ["tree"], ["fruit"]]),
          ("Hardwood trees", [["hardwood"]]),
          ("Hops and bushes", [["hopbush"], ["hops", "bush"], ["hops"], ["bush"]]),
          ("Bird houses", [["birdhouse"]])]


def routine(plans, mode="xp"):
    """The best way through each group of overlapping runs, done each time it is ready (up to
    MAX_RUNS_DAY a day): most XP a day, or most profit a day. Separate tree and fruit tree runs
    beat the combined run when trees grow back sooner. Trees cost gp, so in value mode they are
    picked by the least gp per XP instead."""
    out, day = [], {"xp": 0.0, "profit": 0.0, "minutes": 0.0}
    for label, options in GROUPS:
        best = None
        for keys in options:
            rs = [plans[k] for k in keys if k in plans]
            if len(rs) < len(keys) or not all((r.get("patches") or r.get("usable")) and r.get("minutes") for r in rs):
                continue
            xp, profit = sum(r["xp"] for r in rs), sum(r["profit"] for r in rs)
            minutes = sum(r["minutes"] for r in rs)
            per_day = {"xp": sum(r["xp"] * r["perDay"]["runs"] for r in rs),
                       "profit": sum(r["profit"] * r["perDay"]["runs"] for r in rs),
                       "minutes": sum(r["minutes"] * r["perDay"]["runs"] for r in rs)}
            gpx = -profit / xp if xp else None
            if mode == "xp":
                score = per_day["xp"] - per_day["minutes"]  # a minute breaks ties
            elif label in ("Trees", "Hardwood trees", "Bird houses") and profit < 0:
                score = -(gpx or 0) / 1000  # least gp per XP, below any run that makes money
            else:
                score = per_day["profit"]
            cand = {"group": label, "runs": keys, "xp": xp, "profit": profit, "minutes": round(minutes, 1),
                    "perMinute": (xp if mode == "xp" else profit) / minutes, "gpPerXp": gpx, "perDay": per_day,
                    "score": score}
            if best is None or score > best["score"]:
                best = cand
        if best:
            out.append(best)
            for k in day:
                day[k] += best["perDay"][k]
    return {"groups": out, "perDay": day}


def _tracked(db, acct):
    from . import patches
    return patches.status(db, acct) if acct else None


def plan_all(db, engine, mode="xp", acct=None, unlocks=None):
    ctx = Context(db, engine, acct, unlocks)
    runs = [plan_run(k, ctx, mode) for k in RUNS]
    birds = plan_birdhouses(ctx, mode)
    ranked = [r for r in runs if r["patches"]] + ([birds] if birds.get("usable") else [])
    key = "xpPerHour" if mode == "xp" else "profitPerHour"
    ranked.sort(key=lambda r: -r[key])
    plans = {r["key"]: r for r in ranked}
    return {"mode": mode, "acct": ctx.acct, "accountKnown": ctx.known,
            "levels": {s: ctx.level(s) for s in ("Farming", "Hunter", "Crafting", "Magic", "Agility")},
            "owned": {n: ctx.has(n) for n in ("Magic secateurs", "Bottomless compost bucket", "Farming cape",
                                               "Farmer's strawhat", "Digsite pendant", "Dramen staff")},
            "outfit": outfit_bonus(ctx), "unlocks": sorted(ctx.unlocks), "unlockLabels": UNLOCKS,
            "ticked": sorted(ctx.ticked), "autoUnlocks": ctx.auto, "pace": ctx.pace,
            "runs": runs, "birdhouse": birds, "ranked": [r["key"] for r in ranked],
            "routine": routine(plans, mode), "compost": plan_compost(ctx), "tracked": _tracked(db, ctx.acct), "source": "OSRS Wiki farming pages (CC BY-NC-SA 3.0)",
            "fetched": ctx.data.get("fetched")}


TRAINING_LEVELS = (1, 15, 30, 45, 60, 75, 85, 99)
_train_cache = {}


def training_methods(db, engine, acct=None, mode="fastest", ttl=1800):
    """Farming runs as a training method for the planner: the routine's XP per hour of play at
    each level (crops, patches and teleports as they would be then), its gp per XP, and the XP a
    day it allows, since the patches set the pace, not your play time."""
    key = (acct, mode, round(time.time() / ttl))
    if key in _train_cache:
        return _train_cache[key]
    ctx = Context(db, engine, acct)
    want = "value" if mode == "cheapest" else "xp"
    curve, now_lv, at_now = [], ctx.level("Farming"), None
    for lv in TRAINING_LEVELS:
        ctx.levels["Farming"] = lv
        plans = {r["key"]: r for r in [plan_run(k, ctx, want) for k in RUNS] if r["patches"]}
        rt = routine(plans, want)
        d = rt["perDay"]
        if d["minutes"]:
            curve.append([lv, round(d["xp"] / d["minutes"] * 60)])
        if lv <= now_lv or at_now is None:
            at_now = (rt, d)
    ctx.levels["Farming"] = now_lv
    if not curve:
        return []
    rt, d = at_now
    runs = ", ".join(LABEL.get(k, k) if k in LABEL else RUNS[k][0] for g in rt["groups"] for k in g["runs"])
    gpx = (-d["profit"] / d["xp"]) if d["xp"] else None
    out = [{"name": "Farming runs", "lo": 1, "hi": 99, "curve": curve, "typical": None, "gpPerXp": gpx,
            "req": {}, "items": [], "url": WIKI + "Farming_runs", "source": "runs", "hasSetup": False,
            "summary": f"Your best routine on the Runs page ({runs}). XP per hour of play; the patches set the pace, "
                       f"about {round(d['xp']):,} XP a day doing each run when it is ready.",
            "perDay": round(d["xp"])}]
    _train_cache.clear()
    _train_cache[key] = out
    return out


# Setups ----------------------------------------------------------------------------------------

def run_setup(db, engine, key, mode="xp", acct=None):
    """A run as an Inventory Setups setup: tools, compost, seeds, payments, teleports, outfit.
    Wearable teleports go in their slot, a Farmer's outfit you own replaces the Wiki's example
    gear, and more than 8 compost buckets become one bottomless bucket so the run fits."""
    from . import setups
    ctx = Context(db, engine, acct)
    if key == "birdhouse":
        b = plan_birdhouses(ctx, mode)
        names = [("Digsite pendant", 1)]
        if b.get("house"):
            if b["build"]:
                names += [("Hammer", 1), ("Chisel", 1), ("Clockwork", 4)] + [(b["logs"], 1)] * 4
            else:
                names += [(b["house"]["item"], 1)] * 4
            names += [(b["seed"], b["seedAmount"] * 4)]
        s = setups.from_items("run", "Bird house run", None, names, ctx.items, ctx.own, b["url"],
                              notes=f"Bird house run: {b.get('house', {}).get('item', '')} on the four Fossil Island spots.")
        return s, b
    if key == "supercompost":
        c = plan_compost(ctx)
        names = [("Amulet of glory", 1), (c["ingredient"] or "Pineapple", 15), ("Ectophial", 1)]
        s = setups.from_items("run", "Supercompost run", None, names, ctx.items, ctx.own, c["url"],
                              notes="Supercompost run: fill each bin with 15 " + (c["ingredient"] or "ingredients") +
                                    ", collect with empty buckets from the Tool Leprechaun.")
        return s, c
    if key == "willow":
        names = [("Magic secateurs" if ctx.has("Magic secateurs") else "Secateurs", 1), ("Ring of dueling", 1), ("Logs", 1)]
        for tab, lv in (("Varrock teleport", 25), ("Falador teleport", 37), ("Lumbridge teleport", 31)):
            if ctx.level("Magic") < lv:
                names.append((tab, 1))
        url = WIKI + "Farming_runs#Willow_branch_run"
        s = setups.from_items("run", "Willow branch run", None, names, ctx.items, ctx.own, url,
                              notes="Willow branch run: 6 branches a willow, about 30 minutes after its health check.")
        return s, {"key": "willow", "title": "Willow branch run", "url": url}
    r = plan_run(key, ctx, mode)
    usable = [s for s in r["stops"] if s["usable"] and s["crop"]]
    eq = dict((r.get("example") or {}).get("eq") or {})
    # The example's teleport jewellery is one route among several: keep only what you own; the
    # route chosen below adds its own.
    raw = (ctx.data.get("runs", {}).get(r["title"]) or {}).get("stops", [])
    tele_text = " ".join(o for st in raw for o in st.get("teleports", [])).lower()
    for slot, name in list(eq.items()):
        if not ctx.has(name) and name.lower() in tele_text:
            del eq[slot]
    for slot, piece in (("head", "Farmer's strawhat"), ("body", "Farmer's jacket"), ("legs", "Farmer's boro trousers"),
                        ("feet", "Farmer's boots")):
        if ctx.has(piece) or (piece == "Farmer's jacket" and ctx.has("Farmer's shirt")):
            eq[slot] = piece if ctx.has(piece) else "Farmer's shirt"
    names = [("Spade", 1), ("Rake", 1)]
    if any(not s["crop"]["seed"].lower().endswith("sapling") for s in usable):
        names.append(("Seed dibber", 1))
    if ctx.has("Magic secateurs"):
        names.append(("Magic secateurs", 1))
    composts = {}
    for s in usable:
        c = s["crop"]["compost"]
        if c != "none":
            composts[c] = composts.get(c, 0) + s["count"]
    buckets = sum(composts.values())
    tip = ""
    if composts and (ctx.has("Bottomless compost bucket") or buckets > 8):
        names.append(("Bottomless compost bucket", 1))
        if not ctx.has("Bottomless compost bucket"):
            tip = f" Without a bottomless bucket, {buckets} buckets of compost won't fit: take the first stops' worth and bank on the way."
    else:
        for c, n in composts.items():
            names += [(dict((x[0], x[1]) for x in COMPOST)[c], 1)] * n  # buckets do not stack
    seeds, pays = {}, {}
    for s in usable:
        c = s["crop"]
        seeds[c["seed"]] = seeds.get(c["seed"], 0) + c["seedQty"] * s["count"]
        if c["pay"] and isinstance(c["protection"], list):
            pays[c["protection"][0]] = pays.get(c["protection"][0], 0) + c["protection"][1] * s["count"]
    for n, q in seeds.items():
        if "sapling" in n.lower():
            names += [(n, 1)] * q  # saplings do not stack
        else:
            names.append((n, q))
    for n, q in pays.items():
        names.append((n, q))  # gardeners take noted payments, so one slot each
    trees = sum(s["count"] for s in usable if s["kind"] in TREE_LIKE)
    if trees:
        names.append(("Coins", 200 * trees))  # gardeners chop the old tree for 200 each
    seen, worn = set(), set()
    for s in usable:
        for t in (s["teleport"] or {}).get("bring", []):
            if t in seen:
                continue
            seen.add(t)
            info = ctx.find(t)
            slot = (info or {}).get("slot")
            if slot and slot != "weapon" or t in ("Dramen staff", "Lunar staff"):
                slot = slot or "weapon"
                if slot in worn or (eq.get(slot) and ctx.has(eq[slot]) and eq[slot] != t and slot != "weapon"):
                    names.append((t, 1))  # the slot holds another teleport, or gear you own
                else:
                    eq[slot] = t
                    worn.add(slot)
            else:
                names.append((t, 1))
    setup = setups.from_items("run", r["title"], None, names, ctx.items, ctx.own, r["url"], eq_names=eq,
                              allow=("coins",),
                              notes=f"{r['title']} for Farming {ctx.level('Farming')}: " +
                                    ", ".join(f"{LABEL.get(k, k)} {v['crop']}" for k, v in r["crops"].items() if v) +
                                    ". Payments go to the gardeners noted." + tip)
    return setup, r
