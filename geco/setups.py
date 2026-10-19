"""Gear and inventory setups, fitted to what you own and exported to the Inventory Setups plugin.

Where setups come from:
* Bosses and raids: each strategy page's {{Recommended equipment}} ranks gear per slot (head1 is
  best, head5 a budget pick) and its "Inventory setups" section has example inventories and rune
  pouches, matched to a style by their tab label.
* Skilling: the inventory and equipment shown with each method in the training guides.
* Quests: the items to bring.
* Your own sets, built on the dashboard.

Fitting picks, per slot, the best ranked item you own (bank, inventory, worn, or other stored
containers), lists the slots you have nothing for with what each tier costs, and the next upgrade
for slots below the top tier.

Item names become item IDs through the GE item list, then the Wiki's infobox for anything not
sold on the GE (with every version's ID, so a degraded or charged copy counts as owned).

Inventory Setups has no way for another plugin to add a setup, and it rewrites its stored setups
from memory on every save, so setups reach it through its own Import (one pasted setup) or Mass
Import (a file of several). The JSON follows its InventorySetupPortable format.
Content from the OSRS Wiki (CC BY-NC-SA 3.0).
"""
import json
import os
import re
import time
from urllib.parse import quote

from .quests import plain

VERSION = 2
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setups_snapshot.json")
WIKI = "https://oldschool.runescape.wiki/w/"
# Inventory Setups stores equipment by EquipmentInventorySlot index.
EQ_ORDER = ["head", "cape", "neck", "weapon", "body", "shield", "arms", "legs", "hair", "hands", "feet", "jaw",
            "ring", "ammo"]
WEAR = ["head", "cape", "neck", "ammo", "weapon", "body", "shield", "legs", "hands", "feet", "ring"]
ALIAS = {"torso": "body", "gloves": "hands", "boots": "feet", "amulet": "neck", "2h": "weapon", "hand": "hands",
         "quiver": "cape"}
# Templates the Wiki uses in place of an item, picked by price on the Wiki.
PLACEHOLDER = {"cheap prayer": "Prayer potion(4)", "cheap food": "Shark", "cheap restore": "Super restore(4)",
               "cheap brew": "Saradomin brew(4)"}
GENERIC = {"coins", "combat", "food", "weapon", "weapons", "armour", "armor", "runes", "prayer", "potions",
           "antipoison", "teleport", "teleports", "inventory", "equipment", "any", "none", "n/a"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS setups_custom (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    data TEXT NOT NULL, created INTEGER, updated INTEGER);
"""


def init(db):
    db.conn.executescript(SCHEMA)
    db.conn.commit()


# Wikitext templates ----------------------------------------------------------------------------

def template_at(text, i):
    """(name, positional [raw], named {key: raw}, end) for the template starting at text[i]."""
    depth, j, cur, parts = 0, i, [], []
    while j < len(text):
        two = text[j:j + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            j += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            if depth == 0:
                parts.append("".join(cur))
                j += 2
                break
            cur.append(two)
            j += 2
            continue
        if text[j] == "|" and depth == 1:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(text[j])
        j += 1
    name = parts[0][2:].strip() if parts else ""
    pos, named = [], {}
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        if eq and re.fullmatch(r"\s*[\w ]+\s*", k) and "[[" not in k and "{{" not in k:
            named[k.strip().lower()] = v.strip()
        else:
            pos.append(p.strip())
    return name, pos, named, j


def templates(text, name):
    """Every {{name ...}} in text as (start, positional, named)."""
    out = []
    for m in re.finditer(r"\{\{\s*" + re.escape(name) + r"\s*(?=[|}\n])", text, re.I):
        _, pos, named, end = template_at(text, m.start())
        out.append((m.start(), pos, named))
    return out


def names_in(value):
    """Item names in a slot value: {{plink|X}} links, [[X]] links, or plain text split on "/"."""
    v = re.sub(r"\{\{\s*efn[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}", "", value or "", flags=re.I)
    v = re.sub(r"(?s)<ref[^>]*>.*?</ref>|<ref[^>]*/>|<!--.*?-->", "", v)
    found = []
    for m in re.finditer(r"\{\{\s*(plink[a-z]*|plinkp|plinkt)\s*\|\s*([^|}]+)[^}]*\}\}|\[\[([^\]|#]+)(?:[^\]]*)\]\]"
                         r"|\{\{\s*([^|}]+?)\s*\}\}", v, re.I):
        n = (m.group(2) or m.group(3) or "").strip()
        if m.group(4):
            n = PLACEHOLDER.get(m.group(4).strip().lower(), "")
        if n and not n.lower().startswith(("file:", "category:")) and n not in found:
            found.append(n)
    if not found:
        found = [p.strip() for p in re.split(r"\s*/\s*|<br\s*/?>", plain(v)) if p.strip()]
    return found


def cell(value):
    """(name, qty) from an inventory cell like "Coins 10000\\1000000" or "{{Cheap food}}"."""
    v = (value or "").strip()
    if not v:
        return None
    m = re.fullmatch(r"\{\{\s*([^|}]+?)\s*\}\}", v)
    if m:
        name = PLACEHOLDER.get(m.group(1).lower())
        return (name, 1) if name else None
    qty = 1
    q = re.search(r"\s*[\\*]\s*([\d,]+)\s*$", v)
    if q:
        qty = int(q.group(1).replace(",", ""))
        v = v[:q.start()]
    v = plain(v)
    if re.fullmatch(r"(?i)coins( \d+)?", v):
        v = "Coins"
    return (v, qty) if v else None


def inventory(pos, named):
    """28 cells (name, qty) or None, from positional or numbered parameters."""
    cells = [None] * 28
    numbered = {int(k): v for k, v in named.items() if k.isdigit()}
    if numbered:
        for k, v in numbered.items():
            if 1 <= k <= 28:
                cells[k - 1] = cell(v)
    else:
        for i, v in enumerate([p for p in pos][:28]):
            cells[i] = cell(v)
    return cells


def equipment(named):
    out = {}
    for k, v in named.items():
        slot = ALIAS.get(k, k)
        if slot in WEAR and v.strip():
            c = cell(v)
            if c:
                out[slot] = c[0]
    return out


def rune_pouch(pos, named):
    cells = inventory(pos, named)[:4]
    return [c for c in cells if c]


def examples(text):
    """Example setups on a page: [{label, eq, inv, rp}], grouped from Equipment, Inventory and
    Rune pouch templates in order, labelled by the tab they sit in."""
    marks = []
    for kind in ("Equipment", "Inventory", "Rune pouch"):
        for start, pos, named in templates(text, kind):
            marks.append((start, kind, pos, named))
    marks.sort(key=lambda m: m[0])
    out = []
    for start, kind, pos, named in marks:
        before = text[:start]
        tabs = list(re.finditer(r"(?m)(?:^\|-\|\s*|<tabber>\s*\n?\s*)([^=\n|<>]{2,60}?)\s*=\s*$", before))
        heads = list(re.finditer(r"(?m)^=+\s*([^=\n]+?)\s*=+\s*$", before))
        label = (tabs[-1].group(1) if tabs and (not heads or tabs[-1].start() > heads[-1].start())
                 else heads[-1].group(1) if heads else "Setup")
        label = plain(label)
        cur = out[-1] if out else None
        # A new example starts at an Equipment template, a new tab, or a repeated part.
        if not cur or kind == "Equipment" or cur["label"] != label or kind in cur["_has"]:
            cur = {"label": label, "eq": {}, "inv": None, "rp": None, "_has": set()}
            out.append(cur)
        cur["_has"].add(kind)
        if kind == "Equipment":
            cur["eq"] = equipment(named)
        elif kind == "Inventory":
            cur["inv"] = inventory(pos, named)
        else:
            cur["rp"] = rune_pouch(pos, named)
    for ex in out:
        ex.pop("_has")
    return out


def _words(s):
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) > 2 and w not in ("the", "and", "with")}


def parse_strategy(title, text):
    """A boss strategy page: [{style, tiers {slot: [[names]]}, special [[names]], inv, rp, example eq}]."""
    exs = examples(text)
    styles = []
    for start, pos, named in templates(text, "Recommended equipment"):
        tiers = {}
        for k, v in named.items():
            m = re.fullmatch(r"([a-z]+?)(\d*)", k)
            if not m:
                continue
            slot = ALIAS.get(m.group(1), m.group(1))
            if slot not in WEAR and slot != "special":
                continue
            n = int(m.group(2) or 1)
            alts = [a for a in names_in(v) if a.lower() not in GENERIC]
            if alts:
                tiers.setdefault(slot, {})[n] = alts
        if len([k for k in tiers if k != "special"]) < 4:
            continue  # a single swap for one room or phase, not a whole setup
        style = plain(named.get("style", "")) or "Setup"
        seen_styles = [x["style"] for x in styles]
        if style in seen_styles:
            style = f"{style} {seen_styles.count(style) + 1}"
        best, score = None, -1
        for i, ex in enumerate(exs):
            sc = len(_words(style) & _words(ex["label"])) * 10 - i * 0.01
            if ex.get("inv") and sc > score:
                best, score = ex, sc
        styles.append({
            "style": style,
            "tiers": {s: [t[n] for n in sorted(t)] for s, t in tiers.items() if s != "special"},
            "special": [tiers["special"][n] for n in sorted(tiers["special"])] if "special" in tiers else [],
            "inv": best["inv"] if best else None, "rp": best["rp"] if best else None,
            "exampleEq": best["eq"] if best else {}, "example": best["label"] if best else None,
        })
    return {"boss": title.split("/")[0], "url": WIKI + quote(title.replace(" ", "_")), "styles": styles}


# Item IDs from the Wiki -------------------------------------------------------------------------

def parse_infobox(text, want=None, fragment=None):
    """{id, ids, name, image, slot} from an item page's Infobox Item (and Infobox Bonuses)."""
    box = templates(text, "Infobox Item")
    if not box:
        return None
    named = box[0][2]
    bon = templates(text, "Infobox Bonuses")
    bnamed = bon[0][2] if bon else {}
    versions = sorted({int(k[2:]) for k in named if re.fullmatch(r"id\d+", k)})

    def ids(v):
        return [int(x) for x in re.findall(r"\d+", plain(v or ""))]

    def get(d, key, n):
        return d.get(f"{key}{n}") if n is not None and f"{key}{n}" in d else d.get(key)

    pick = None
    if versions:
        for n in versions:
            label = plain(named.get(f"version{n}", ""))
            nm = plain(get(named, "name", n) or "")
            if fragment and label.lower() == fragment.lower():
                pick = n
                break
            if want and nm.lower() == want.lower():
                pick = n
                break
        if pick is None:
            pick = versions[0]
    every = []
    for n in versions or [None]:
        every += ids(get(named, "id", n))
    main = ids(get(named, "id", pick))
    if not main:
        return None
    img = re.search(r"\[\[\s*File:([^\]|]+)", get(named, "image", pick) or "")
    slot = plain(get(bnamed, "slot", pick) or "").lower() or None
    slot = ALIAS.get(slot, slot)
    return {"id": main[0], "ids": sorted(set(every)), "name": plain(get(named, "name", pick) or want or ""),
            "image": img.group(1).strip() if img else None, "slot": slot if slot in WEAR else None}


def fetch_items(client, names, sleep=1.0):
    """{name lower: info} for item names, read from their Wiki pages 50 at a time. Names the
    Wiki titles differently ("Seal of Passage" is "Seal of passage") get a second try in its
    sentence case."""
    out = _fetch_items(client, names, sleep)
    retry = {n[:1].upper() + n[1:].lower(): n for n in names if n.lower() not in out and n[1:] != n[1:].lower()}
    if retry:
        out.update(_fetch_items(client, list(retry), sleep))  # same lower-case key either way
    return out


def _fetch_items(client, names, sleep=1.0):
    out = {}
    names = sorted(set(names))
    for k in range(0, len(names), 50):
        batch = names[k:k + 50]
        d = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                             "titles": "|".join(batch), "redirects": "1"})
        q = d.get("query", {})
        norm_ = {r["from"]: r["to"] for r in q.get("normalized", []) or []}
        redir = {r["from"]: (r["to"], r.get("tofragment")) for r in q.get("redirects", []) or []}
        pages = {p["title"]: p["revisions"][0]["slots"]["main"]["content"]
                 for p in q.get("pages", []) if p.get("revisions")}
        for n in batch:
            title, frag = norm_.get(n, n), None
            if title in redir:
                title, frag = redir[title]
            text = pages.get(title)
            if text:
                info = parse_infobox(text, n, frag)
                if info:
                    out[n.lower()] = info
        time.sleep(sleep)
    return out


# Fetching and storage ----------------------------------------------------------------------------

def fetch_all(client, bosses, extra_names=(), ge_names=frozenset(), sleep=1.0):
    """Strategy pages for every boss (one request), then item IDs from the Wiki, 50 names a request."""
    titles = [b + "/Strategies" for b in bosses]
    d = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                         "titles": "|".join(titles), "redirects": "1"})
    pages = {p["title"]: p["revisions"][0]["slots"]["main"]["content"]
             for p in d.get("query", {}).get("pages", []) if p.get("revisions")}
    out = {}
    for t, x in pages.items():
        s = parse_strategy(t, x)
        if s["styles"]:
            out[s["boss"]] = s
    names = set(extra_names)
    for s in out.values():
        for st in s["styles"]:
            for tiers in list(st["tiers"].values()) + [st["special"]]:
                for alts in tiers:
                    names.update(alts)
            for c in (st["inv"] or []) + (st["rp"] or []):
                if c:
                    names.add(c[0])
            names.update(st["exampleEq"].values())
    # Every name, GE items included: the infobox lists each version's ID (degraded, charged,
    # imbued), so any copy you own counts.
    wanted = sorted(n for n in names if n)
    time.sleep(sleep)
    items = fetch_items(client, wanted, sleep) if wanted else {}
    return {"source": "OSRS Wiki strategy pages and item infoboxes (CC BY-NC-SA 3.0)", "version": VERSION,
            "fetched": int(time.time()),
            "bosses": out, "items": items}


def referenced_names(guides, quest_data):
    """Item names used by training guide setups and quest item lists (looked up with the bosses')."""
    out = set()
    for ms in (guides or {}).get("methods", {}).values():
        for m in ms:
            su = m.get("setup")
            if su:
                out.update(su["eq"].values())
                out.update(c[0] for c in su["inv"] + su["rp"] if c)
    for q in (quest_data or {}).values():
        out.update(i["name"] for i in q.get("items") or [])
    return {n for n in out if n and n.lower() not in GENERIC}


_cache = {}


def load(db=None):
    if db is not None:
        d = db.kv_get("setups_wiki")
        if d and d.get("bosses") and d.get("version") == VERSION:
            return d
    if "snap" not in _cache:
        try:
            with open(SNAPSHOT, encoding="utf-8") as f:
                _cache["snap"] = json.load(f)
        except (OSError, ValueError):
            _cache["snap"] = {"bosses": {}, "items": {}}
    return _cache["snap"]


def refresh(db, client, bosses, extra_names, ge_names, max_age=7 * 86400):
    d = db.kv_get("setups_wiki") or {}
    if d.get("fetched") and time.time() - d["fetched"] < max_age and d.get("version") == VERSION:
        return 0
    data = fetch_all(client, bosses, extra_names, ge_names)
    if len(data["bosses"]) >= 5:
        db.kv_set("setups_wiki", data)
    return len(data["bosses"])


# Resolving names, ownership ---------------------------------------------------------------------

class Items:
    """Name to item lookups across the GE list and the Wiki's item IDs."""

    def __init__(self, engine, wiki_items):
        self.engine = engine
        self.ge = {}
        for iid, m in (engine.mapping or {}).items():
            self.ge.setdefault((m.get("name") or "").lower(), int(iid))
        self.wiki = wiki_items or {}
        self.by_id = {}
        for v in self.wiki.values():
            for i in v.get("ids") or [v["id"]]:
                self.by_id.setdefault(i, v)

    def find(self, name):
        """{id, ids, name, icon, tradeable, slot} or None."""
        if not name:
            return None
        n = name.strip()
        low = n.lower()
        # Charged items are sold by charge count ("Amulet of glory(4)", "Digsite pendant (5)").
        charged = [low + f"{sp}({n})" for n in (4, 3, 5, 6, 8, 2, 1) for sp in ("", " ")]
        for cand in [low, re.sub(r"\s?\(\d\)$", "(4)", low)] + charged:
            if cand in self.ge:
                iid = self.ge[cand]
                m = self.engine.mapping.get(iid) or {}
                w = self.wiki.get(low) or {}
                return {"id": iid, "ids": sorted(set([iid] + (w.get("ids") or []))), "name": m.get("name") or n,
                        "icon": m.get("icon"), "tradeable": True, "slot": w.get("slot")}
        w = self.wiki.get(low)
        if w:
            return {"id": w["id"], "ids": w.get("ids") or [w["id"]], "name": w.get("name") or n,
                    "icon": w.get("image"), "tradeable": False, "slot": w.get("slot")}
        return None

    def by(self, iid):
        """Display info for an item ID."""
        m = (self.engine.mapping or {}).get(iid)
        if m:
            return {"id": iid, "name": m.get("name"), "icon": m.get("icon"), "tradeable": True}
        w = self.by_id.get(iid)
        if w:
            return {"id": iid, "name": w.get("name"), "icon": w.get("image"), "tradeable": False, "slot": w.get("slot")}
        return {"id": iid, "name": f"Item {iid}", "icon": None, "tradeable": False}

    def price(self, iid):
        r = self.engine.by_id.get(iid) if iid else None
        return (r.get("high") or r.get("low")) if r else None


def owned(db, acct=None):
    """{item id: qty} across stored containers (bank, inventory, worn, looting bag and so on)."""
    rows = db.q("SELECT items FROM holdings_live" + (" WHERE acct=?" if acct else ""), (acct,) if acct else ())
    out = {}
    for r in rows:
        try:
            for iid, qty in json.loads(r["items"] or "[]"):
                out[int(iid)] = out.get(int(iid), 0) + int(qty)
        except (ValueError, TypeError):
            continue
    return out


# Building setups --------------------------------------------------------------------------------

def _entry(info, qty, have, extra=None):
    d = {"id": info["id"], "name": info["name"], "icon": info.get("icon"), "qty": qty, "have": have,
         "owned": have >= 1, "tradeable": info.get("tradeable")}
    if extra:
        d.update(extra)
    return d


def _have(info, own):
    """(qty owned, the owned ID) counting any version of the item."""
    best, total = None, 0
    for i in info.get("ids") or [info["id"]]:
        if own.get(i):
            total += own[i]
            best = best or i
    return total, best


def fit_tiers(slot, tiers, items, own):
    """Best owned item in a slot from ranked tiers, the gap or upgrade, and every option."""
    options = []
    chosen = None
    for t, alts in enumerate(tiers, 1):
        for name in alts:
            info = items.find(name)
            if not info:
                options.append({"tier": t, "name": name, "id": None, "price": None, "owned": False})
                continue
            have, oid = _have(info, own)
            o = {"tier": t, "name": info["name"], "id": oid or info["id"], "icon": info.get("icon"),
                 "price": items.price(info["id"]), "owned": have > 0, "tradeable": info.get("tradeable")}
            options.append(o)
            if have and not chosen:
                chosen = dict(o, of=len(tiers))
    up = budget = best = None
    buyable = [o for o in options if o["id"] and not o["owned"] and o["price"]]
    better = [o for o in buyable if chosen and o["tier"] < chosen["tier"]]
    if better:
        # The smallest step up from what you have: the next tier, cheapest in it.
        top = max(o["tier"] for o in better)
        up = min((o for o in better if o["tier"] == top), key=lambda o: o["price"])
    if not chosen and buyable:
        # Nothing owned: the cheapest pick in the lowest tier sold on the GE, and the best one sold.
        low, high = max(o["tier"] for o in buyable), min(o["tier"] for o in buyable)
        budget = min((o for o in buyable if o["tier"] == low), key=lambda o: o["price"])
        best = min((o for o in buyable if o["tier"] == high), key=lambda o: o["price"])
        if best is budget:
            best = None
    return {"slot": slot, "item": chosen, "options": options[:14], "upgrade": up, "budget": budget, "best": best,
            "tiers": len(tiers)}


def from_style(boss, st, items, own, url):
    eq = {}
    for slot in WEAR:
        if slot in st["tiers"]:
            eq[slot] = fit_tiers(slot, st["tiers"][slot], items, own)
        elif st.get("exampleEq", {}).get(slot):
            info = items.find(st["exampleEq"][slot])
            if info:
                have, oid = _have(info, own)
                o = {"tier": 1, "name": info["name"], "id": oid or info["id"], "icon": info.get("icon"),
                     "price": items.price(info["id"]), "owned": have > 0, "tradeable": info.get("tradeable")}
                eq[slot] = {"slot": slot, "item": dict(o, of=1) if have else None, "options": [o],
                            "upgrade": None, "budget": None if have or not o["price"] else o, "tiers": 1}
    inv = _inventory(st.get("inv"), items, own)
    if st.get("special"):
        sp = fit_tiers("special", st["special"], items, own)
        if sp["item"] and not any(c and c["id"] == sp["item"]["id"] for c in inv):
            free = next((i for i, c in enumerate(inv) if c is None), None)
            if free is not None:
                inv[free] = _entry(dict(items.find(sp["item"]["name"]) or {"name": sp["item"]["name"]}, id=sp["item"]["id"]),
                                   1, 1, {"special": True, "price": sp["item"].get("price")})
    else:
        sp = None
    rp = [c for c in _inventory(st.get("rp"), items, own, size=4) if c]
    title = f"{boss}: {st['style']}"
    return finish({"kind": "boss", "title": boss, "subtitle": st["style"], "name": title, "url": url,
                   "eq": eq, "inv": inv, "rp": rp, "extra": [], "special": sp,
                   "notes": f"From the OSRS Wiki's {boss} strategies ({st['style']})"
                            + (f", inventory from the {st['example']} setup" if st.get("example") else "") + "."})


def _inventory(cells, items, own, size=28):
    out = [None] * size
    for i, c in enumerate((cells or [])[:size]):
        if not c:
            continue
        info = items.find(c[0])
        if not info:
            out[i] = {"id": None, "name": c[0], "qty": c[1], "have": 0, "owned": False, "unknown": True}
            continue
        have, oid = _have(info, own)
        if c[1] >= 10000:
            # The Wiki draws "a stack" as 100,000 or 1,000,000: any amount will do.
            out[i] = _entry(dict(info, id=oid or info["id"]), 1, min(have, 1), {"price": items.price(info["id"]),
                                                                              "stack": True})
            continue
        out[i] = _entry(dict(info, id=oid or info["id"]), c[1], have, {"price": items.price(info["id"])})
    return out


def from_items(kind, title, subtitle, names, items, own, url=None, eq_names=None, notes="", allow=()):
    """A setup from a list of (name, qty): quest items or a guide's setup."""
    eq = {}
    for slot, name in (eq_names or {}).items():
        info = items.find(name)
        if not info:
            continue
        have, oid = _have(info, own)
        o = {"tier": 1, "name": info["name"], "id": oid or info["id"], "icon": info.get("icon"),
             "price": items.price(info["id"]), "owned": have > 0, "tradeable": info.get("tradeable")}
        eq[slot] = {"slot": slot, "item": dict(o, of=1) if have else None, "options": [o],
                    "upgrade": None, "budget": None if have or not o["price"] else o, "tiers": 1}
    inv, extra, loose = [None] * 28, [], []
    k = 0
    for name, qty in names:
        if not name or (name.lower() in GENERIC and name.lower() not in allow):
            continue
        info = items.find(name)
        if not info and kind == "quest":
            loose.append({"name": name, "qty": qty})  # "any pickaxe": not one item, so listed
            continue
        stack = qty >= 10000  # the Wiki draws "a stack" as 100,000: any amount will do
        have = _have(info, own)[0] if info else 0
        e = (_entry(dict(info, id=_have(info, own)[1] or info["id"]), 1 if stack else qty,
                    min(have, 1) if stack else have, {"price": items.price(info["id"]), "stack": stack}) if info
             else {"id": None, "name": name, "qty": qty, "have": 0, "owned": False, "unknown": True})
        if k < 28:
            inv[k] = e
            k += 1
        else:
            extra.append(e)
    return finish({"kind": kind, "title": title, "subtitle": subtitle, "name": f"{title}: {subtitle}" if subtitle else title,
                   "url": url, "eq": eq, "inv": inv, "rp": [], "extra": extra, "loose": loose, "special": None,
                   "notes": notes})


def finish(s):
    """Gaps (nothing owned), upgrades, totals."""
    gaps, ups = [], []
    for slot, f in s["eq"].items():
        if not f["item"]:
            pick = f.get("budget") or f.get("upgrade") or (f["options"] or [{}])[0]
            gaps.append({"slot": slot, "name": pick.get("name"), "id": pick.get("id"), "price": pick.get("price"),
                         "best": f.get("best") if f.get("best") and f["best"] is not pick else None,
                         "notSold": not pick.get("price")})
        elif f["upgrade"]:
            ups.append({"slot": slot, "from": f["item"]["name"], "to": f["upgrade"]["name"], "id": f["upgrade"]["id"],
                        "price": f["upgrade"]["price"], "tier": f["upgrade"]["tier"], "fromTier": f["item"]["tier"]})
    # Supplies: the same item in several squares is one line (needed in all, minus what you have).
    need = {}
    for c in [c for c in s["inv"] if c] + s["rp"] + s["extra"]:
        k = c.get("id") or c["name"]
        n = need.setdefault(k, {"c": c, "qty": 0})
        n["qty"] += c.get("qty", 1)
    for k, n in need.items():
        c = n["c"]
        short = max(0, n["qty"] - c.get("have", 0))
        if short:
            gaps.append({"slot": "inventory", "name": c["name"], "id": c.get("id"), "qty": short,
                         "price": None if c.get("stack") else c.get("price"), "unknown": c.get("unknown"),
                         "stack": c.get("stack")})
    s["gaps"], s["upgrades"] = gaps, sorted(ups, key=lambda u: u["price"] or 0)
    counted = [f for f in s["eq"].values()] + [c for c in s["inv"] if c]
    s["haveCount"] = sum(1 for f in s["eq"].values() if f["item"]) + sum(1 for c in s["inv"] if c and c.get("owned"))
    s["count"] = len(counted)
    return s


# Inventory Setups export -------------------------------------------------------------------------

def _is_item(e, qty=None, fuzzy=True):
    if not e or not e.get("id"):
        return None
    d = {"id": int(e["id"])}
    q = qty if qty is not None else e.get("qty", 1)
    if q and q != 1:
        d["q"] = int(q)
    if fuzzy:
        d["f"] = True  # match any charge or dose of the item
    return d


def portable(s, color="#FFFF0000"):
    """Inventory Setups' InventorySetupPortable: {"setup": {...}, "layout": [...]}."""
    eq = [None] * 14
    for slot, f in s["eq"].items():
        # What you own, else the budget pick: Inventory Setups highlights it until you have it.
        it = f.get("item") or f.get("budget")
        if slot in EQ_ORDER and it:
            eq[EQ_ORDER.index(slot)] = _is_item(it, 1)
    inv = [_is_item(c) for c in s["inv"]] + [None] * (28 - len(s["inv"]))
    rp = [x for x in (_is_item(c, fuzzy=False) for c in s["rp"]) if x] or None
    afi = {}
    for c in s.get("extra") or []:
        it = _is_item(c, 1)
        if it:
            afi[str(it["id"])] = it
    layout = [-1] * (56 + len(afi))
    for i, it in enumerate([x for x in eq if x]):
        layout[i] = it["id"]
    for i, it in enumerate(inv):
        if it:
            layout[16 + i] = it["id"]
    for i, it in enumerate(rp or []):
        layout[48 + i] = it["id"]
    for i, k in enumerate(afi):
        layout[56 + i] = int(k)
    missing = [f"{slot}: {f['budget']['name']}" for slot, f in s["eq"].items() if not f.get("item") and f.get("budget")]
    notes = (s.get("notes") or "") + (" Not owned yet: " + ", ".join(missing) + "." if missing else "")
    setup = {"inv": inv[:28], "eq": eq, "rp": rp, "afi": afi, "name": s["name"],
             "notes": notes + " Made by Bankstanding.", "hc": color, "uh": True,
             "iId": next((x["id"] for x in eq if x), None) or next((x["id"] for x in inv if x), None)}
    if s.get("spellbook") is not None:
        setup["sb"] = s["spellbook"]
    return {"setup": {k: v for k, v in setup.items() if v is not None}, "layout": layout}


# Your own sets ----------------------------------------------------------------------------------

def custom_list(db):
    return [dict(r, data=json.loads(r["data"])) for r in db.q("SELECT * FROM setups_custom ORDER BY name")]


def custom_save(db, sid, name, data, now=None):
    now = int(now or time.time())
    clean = {"eq": {k: int(v) for k, v in (data.get("eq") or {}).items() if k in WEAR and v},
             "inv": [[int(c[0]), max(1, int(c[1]))] if c else None for c in (data.get("inv") or [])][:28],
             "rp": [[int(c[0]), max(1, int(c[1]))] for c in (data.get("rp") or []) if c][:4],
             "extra": [[int(c[0]), max(1, int(c[1]))] for c in (data.get("extra") or []) if c][:60],
             "notes": str(data.get("notes") or "")[:500], "spellbook": data.get("spellbook")}
    clean["inv"] += [None] * (28 - len(clean["inv"]))
    name = (name or "My setup").strip()[:60]
    if sid:
        db.run("UPDATE setups_custom SET name=?, data=?, updated=? WHERE id=?", (name, json.dumps(clean), now, int(sid)))
        return int(sid)
    cur = db.conn.execute("INSERT INTO setups_custom (name, data, created, updated) VALUES (?,?,?,?)",
                          (name, json.dumps(clean), now, now))
    db.conn.commit()
    return cur.lastrowid


def custom_delete(db, sid):
    db.run("DELETE FROM setups_custom WHERE id=?", (int(sid),))


def from_custom(row, items, own):
    d = row["data"]
    eq = {}
    for slot, iid in d.get("eq", {}).items():
        info = items.by(iid)
        have = own.get(iid, 0)
        o = {"tier": 1, "name": info["name"], "id": iid, "icon": info.get("icon"), "price": items.price(iid),
             "owned": have > 0, "tradeable": info.get("tradeable")}
        eq[slot] = {"slot": slot, "item": dict(o, of=1), "options": [o], "upgrade": None, "tiers": 1, "missing": not have}
    def ent(c):
        if not c:
            return None
        info = items.by(c[0])
        return _entry(dict(info, id=c[0]), c[1], own.get(c[0], 0), {"price": items.price(c[0])})
    s = {"kind": "mine", "key": f"mine|{row['id']}", "id": row["id"], "title": row["name"], "subtitle": None,
         "name": row["name"], "url": None, "eq": eq, "inv": [ent(c) for c in d.get("inv", [])][:28],
         "rp": [ent(c) for c in d.get("rp", [])], "extra": [ent(c) for c in d.get("extra", [])], "special": None,
         "notes": d.get("notes") or "", "spellbook": d.get("spellbook"), "raw": d}
    s["inv"] += [None] * (28 - len(s["inv"]))
    s = finish(s)
    for slot, f in eq.items():
        if f.get("missing"):
            s["gaps"].append({"slot": slot, "name": f["item"]["name"], "id": f["item"]["id"], "price": f["item"]["price"]})
    return s


def to_custom(s):
    """A built setup as your own set's data (to copy a Wiki setup and edit it)."""
    return {"eq": {slot: f["item"]["id"] for slot, f in s["eq"].items() if f.get("item")},
            "inv": [[c["id"], c.get("qty", 1)] if c and c.get("id") else None for c in s["inv"]],
            "rp": [[c["id"], c.get("qty", 1)] for c in s["rp"] if c and c.get("id")],
            "extra": [[c["id"], c.get("qty", 1)] for c in s.get("extra") or [] if c and c.get("id")],
            "notes": s.get("notes") or "", "spellbook": s.get("spellbook")}


# Catalog, building by key, files for Mass Import ------------------------------------------------

def _context(db, engine, acct):
    from . import planner, quests, skillguides
    acct = acct or planner.main_account(db)
    return {"data": load(db), "guides": skillguides.load(db), "quests": quests.load(db), "acct": acct,
            "items": Items(engine, load(db).get("items")), "own": owned(db, acct)}


def _method(guides, skill, name):
    return next((m for m in guides.get("methods", {}).get(skill, []) if m["name"] == name), None)


def build(db, engine, key, acct=None, ctx=None):
    """A setup by key: "boss|Vorkath|0", "quest|Dragon Slayer II", "method|Agility|Hallowed Sepulchre",
    "run|herb" (a farming run, "run|herb|value" for best value), "mine|3". Raises KeyError when the key is unknown."""
    ctx = ctx or _context(db, engine, acct)
    items, own = ctx["items"], ctx["own"]
    kind, _, rest = key.partition("|")
    if kind == "boss":
        boss, _, idx = rest.rpartition("|")
        b = ctx["data"]["bosses"][boss]
        s = from_style(boss, b["styles"][int(idx)], items, own, b["url"])
    elif kind == "quest":
        q = ctx["quests"][rest]
        names = [(i["name"], i.get("qty") or 1) for i in q.get("items") or []]
        s = from_items("quest", rest, None, names, items, own, WIKI + quote(rest.replace(" ", "_")),
                       notes=f"Items to bring for {rest}, from the OSRS Wiki. Steps: Quest Helper.")
    elif kind == "method":
        skill, _, name = rest.partition("|")
        m = _method(ctx["guides"], skill, name)
        if not m or not m.get("setup"):
            raise KeyError(key)
        su = m["setup"]
        s = from_items("method", name, skill, [tuple(c) for c in su["inv"] if c], items, own, m.get("url"),
                       eq_names=su["eq"], notes=f"{skill} training at {name}, setup from the OSRS Wiki's guide.")
        s["rp"] = [c for c in _inventory([tuple(c) for c in su.get("rp") or []], items, own, size=4) if c]
        s = finish(s)
    elif kind == "run":
        from . import farming
        run, _, mode = rest.partition("|")
        if run not in farming.RUNS and run not in ("birdhouse", "supercompost", "willow"):
            raise KeyError(key)
        s, _ = farming.run_setup(db, engine, run, mode or "xp", ctx["acct"])
    elif kind == "mine":
        row = db.one("SELECT * FROM setups_custom WHERE id=?", (int(rest),))
        if not row:
            raise KeyError(key)
        s = from_custom(dict(row, data=json.loads(row["data"])), items, own)
    else:
        raise KeyError(key)
    s["key"] = key
    s["acct"] = ctx["acct"]
    s["gapCost"] = sum(g.get("price") or 0 for g in s["gaps"] if g.get("slot") != "inventory") + \
        sum((g.get("price") or 0) * (g.get("qty") or 1) for g in s["gaps"] if g.get("slot") == "inventory")
    return s


def plan_keys(db, engine, cfg, acct=None, plan=None):
    """Setups for the active planner goal's steps, in plan order: [(key, label, task index)]."""
    from . import planner
    goal = planner.active(db)
    if not goal:
        return [], None
    p = plan or planner.build(db, engine, cfg, goal, acct=acct)
    guides = None
    out = []
    for i, t in enumerate(p["tasks"]):
        if t["type"] == "quest" and t.get("items"):
            out.append((f"quest|{t['quest']}", t["quest"], i))
        elif t["type"] == "skill":
            from . import skillguides
            guides = guides or skillguides.load(db)
            for sg in t.get("segments") or []:
                m = _method(guides, t["skill"], sg.get("method"))
                if m and m.get("setup"):
                    out.append((f"method|{t['skill']}|{m['name']}", f"{t['skill']}: {m['name']}", i))
    if goal.get("type") == "boss":
        b = load(db)["bosses"].get(goal["name"])
        for k, st in enumerate((b or {}).get("styles", [])):
            out.append((f"boss|{goal['name']}|{k}", f"{goal['name']}: {st['style']}", len(p["tasks"])))
    return out, p["label"]


def catalog(db, engine, cfg, acct=None):
    """Everything that can be built, with how much of each you own."""
    ctx = _context(db, engine, acct)

    def score(key):
        try:
            s = build(db, engine, key, ctx=ctx)
            return {"have": s["haveCount"], "count": s["count"], "gapCost": s["gapCost"]}
        except (KeyError, IndexError, ValueError):
            return None

    keys, label = plan_keys(db, engine, cfg, ctx["acct"])
    plan = [dict({"key": k, "title": t}, **(score(k) or {})) for k, t, _ in keys]
    bosses = []
    for boss, b in sorted(ctx["data"].get("bosses", {}).items()):
        bosses.append({"boss": boss, "url": b["url"], "styles": [
            dict({"key": f"boss|{boss}|{i}", "style": st["style"]}, **(score(f"boss|{boss}|{i}") or {}))
            for i, st in enumerate(b["styles"])]})
    skilling = []
    for skill, ms in sorted(ctx["guides"].get("methods", {}).items()):
        for m in ms:
            if m.get("setup"):
                skilling.append({"key": f"method|{skill}|{m['name']}", "skill": skill, "method": m["name"],
                                 "lo": m["lo"], "hi": m["hi"]})
    quest_rows = [{"key": f"quest|{n}", "title": n, "items": len(q.get("items") or [])}
                  for n, q in sorted(ctx["quests"].items()) if q.get("items")]
    from . import farming
    runs = [{"key": f"run|{k}", "title": t} for k, (t, _) in farming.RUNS.items()] + \
        [{"key": "run|birdhouse", "title": "Bird house run"}, {"key": "run|supercompost", "title": "Supercompost run"},
         {"key": "run|willow", "title": "Willow branch run"}]
    mine = [{"key": f"mine|{r['id']}", "title": r["name"], "updated": r["updated"],
             **(score(f"mine|{r['id']}") or {})} for r in custom_list(db)]
    return {"acct": ctx["acct"], "accountKnown": bool(ctx["own"]), "plan": plan, "planLabel": label,
            "bosses": bosses, "skilling": skilling, "runs": runs, "quests": quest_rows, "mine": mine}


def export_file(folder, name, portables):
    """Write setups for Inventory Setups' Mass Import. Returns the path."""
    os.makedirs(os.path.join(folder, "setups"), exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:60] or "setups"
    path = os.path.join(folder, "setups", slug + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(portables, f, separators=(",", ":"))
    return path


def search_items(engine, wiki_items, q, limit=30):
    """Items to add in the set builder: GE items and the Wiki IDs known from setups."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    out, seen = [], set()
    for iid, m in (engine.mapping or {}).items():
        n = (m.get("name") or "")
        if q in n.lower():
            w = (wiki_items or {}).get(n.lower()) or {}
            out.append({"id": int(iid), "name": n, "icon": m.get("icon"), "slot": w.get("slot"), "tradeable": True,
                        "rank": (not n.lower().startswith(q), len(n))})
            seen.add(n.lower())
    for k, w in (wiki_items or {}).items():
        if q in k and k not in seen:
            out.append({"id": w["id"], "name": w.get("name") or k, "icon": w.get("image"), "slot": w.get("slot"),
                        "tradeable": False, "rank": (not k.startswith(q), len(k))})
    out.sort(key=lambda r: r["rank"])
    for r in out:
        r.pop("rank")
    return out[:limit]
