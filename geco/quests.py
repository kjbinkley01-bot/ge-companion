"""Quest data from the OSRS Wiki: requirements, prerequisite quests, items, length, quest
points, and the structure of each quick guide (used to estimate how long a quest takes).

Read once in bulk through the MediaWiki API (category lists, then 50 pages of wikitext per
request, a second apart) and refreshed weekly. A parsed snapshot ships in
`geco/quests_snapshot.json` so the planner works before the first fetch and in demo mode.
Quest data is from the OSRS Wiki (CC BY-NC-SA 3.0).
"""
import json
import os
import re
import time

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quests_snapshot.json")
SKILLS = ["Attack", "Strength", "Defence", "Ranged", "Prayer", "Magic", "Runecraft", "Construction",
          "Hitpoints", "Agility", "Herblore", "Thieving", "Crafting", "Fletching", "Slayer", "Hunter",
          "Mining", "Smithing", "Fishing", "Cooking", "Firemaking", "Woodcutting", "Farming", "Sailing"]
SKILL_SET = {s.lower(): s for s in SKILLS}
SCHEMA = """
CREATE TABLE IF NOT EXISTS quest_data (name TEXT PRIMARY KEY, data TEXT NOT NULL, fetched INTEGER);
"""


def init(db):
    db.conn.executescript(SCHEMA)
    db.conn.commit()


def norm(name):
    """Name key that survives small differences between RuneLite and the Wiki."""
    s = (name or "").lower().replace("&", "and").replace("’", "'")
    s = re.sub(r"\s*\(quest\)$", "", s)
    s = re.sub(r"^the ", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


# Wikitext helpers ---------------------------------------------------------------------------

def template_params(text, name):
    """Top level parameters of the first {{name ...}} template, as {key: raw value}."""
    i = text.find("{{" + name)
    if i < 0:
        return None
    depth, j, start = 0, i, None
    parts, cur = [], []
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
                break
            cur.append(two)
            j += 2
            continue
        ch = text[j]
        if ch == "|" and depth == 1:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        j += 1
    out = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
SCP = re.compile(r"\{\{\s*SCP\s*\|\s*([A-Za-z ]+?)\s*\|\s*([\d,]+)", re.I)


def plain(s):
    """Wikitext to readable text (links to their label, templates dropped)."""
    s = LINK.sub(lambda m: m.group(2) or m.group(1), s or "")
    s = re.sub(r"\{\{\s*SCP\s*\|\s*([A-Za-z ]+?)\s*\|\s*([\d,]+)[^}]*\}\}", r"\2 \1", s, flags=re.I)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"'''?", "", s)
    return re.sub(r"\s+", " ", s).strip(" *:")


def parse_requirements(text, quest_names):
    """Skill levels (with boostable flags), quest points, combat level, direct prerequisite
    quests and any other requirement lines from a Quest details `requirements` value.

    Prerequisite quests are listed as a tree: the ones directly under "Completion of the
    following quests" are this quest's own; deeper ones are theirs. Top level lines that name
    a quest (for example a partly completed miniquest) count as direct too."""
    skills, other, direct = {}, [], []
    qp = combat = None
    header = None  # depth of the active "completion of the following quests" line
    for line in [l for l in (text or "").splitlines() if l.strip()]:
        depth = len(line) - len(line.lstrip("*"))
        body = line.lstrip("*").strip()
        low = body.lower()
        if header is not None and depth <= header:
            header = None
        if re.search(r"completion of|the following quests|following quests", low) and not LINK.search(body):
            header = depth
            continue
        hits = SCP.findall(body)
        links = [m.group(1).strip() for m in LINK.finditer(body)]
        qlinks = [l for l in links if norm(l) in quest_names]
        if "warriors' guild" in low or "warriors guild" in low:
            other.append({"kind": "warriors_guild", "text": "Enter the Warriors' Guild (Attack + Strength of 130, or 99 in either)"})
            continue
        if len(hits) > 1 and re.search(r"\bor\b", low):
            other.append({"kind": "text", "text": plain(body)})
            continue
        for sk, lvl in hits:
            sk_l = sk.strip().lower()
            lvl = int(lvl.replace(",", ""))
            if sk_l in ("quest", "quest points", "qp"):
                qp = max(qp or 0, lvl)
            elif sk_l == "combat":
                combat = max(combat or 0, lvl)
            elif sk_l in SKILL_SET:
                name = SKILL_SET[sk_l]
                boost = True
                if re.search(r"\{\{\s*boostable\s*\|\s*no", body, re.I) or "not boostable" in low:
                    boost = False
                prev = skills.get(name)
                if not prev or lvl > prev["level"]:
                    note = re.sub(r"^\d+\s+\w+\s*", "", plain(body)).strip(" ()") if len(hits) == 1 else ""
                    skills[name] = {"level": lvl, "boostable": boost, "note": note or None}
        if hits:
            continue
        if qlinks:
            if header is None or depth == header + 1:
                direct.extend(qlinks)
            continue
        txt = plain(body)
        if txt and header is None and not re.match(r"(?i)^quests?:?$", txt):
            other.append({"kind": "text", "text": txt})
    seen, out = set(), []
    for q in direct:
        k = norm(q)
        if k not in seen:
            seen.add(k)
            out.append(quest_names[k])
    return {"skills": skills, "qp": qp, "combat": combat, "quests": out, "other": other[:12]}


QTY = re.compile(r"^\s*(\d[\d,]*)\s*(?:x\s*)?", re.I)


def parse_items(text):
    """[{name, qty, note}] from an items field: the first link on each line, with a leading
    quantity if there is one."""
    out = []
    for line in (text or "").splitlines():
        body = line.lstrip("*").strip()
        if not body or body.startswith("{{"):
            continue
        m = LINK.search(body)
        if not m:
            continue
        lead = plain(body[:m.start()])
        q = re.search(r"(\d[\d,]*)", lead)
        qty = int(q.group(1).replace(",", "")) if q else 1
        out.append({"name": m.group(1).strip(), "qty": qty, "line": plain(body)[:140]})
    return out


def guide_features(text):
    """Counts that track how long a guide takes to follow."""
    t = text or ""
    lower = t.lower()
    steps = len(re.findall(r"(?m)^\*+\s*\S", t))
    return {
        "steps": steps,
        "sections": len(re.findall(r"(?m)^===?[^=]", t)),
        "teleports": len(re.findall(r"teleport", lower)),
        "fights": len(re.findall(r"\b(kill|defeat|fight)\b", lower)),
        "bosses": len(re.findall(r"\bboss\b|\{\{\s*infobox monster", lower)),
        "chats": len(re.findall(r"\{\{\s*chat option", lower)),
        "puzzles": len(re.findall(r"\bpuzzle", lower)),
        "words": len(re.findall(r"\w+", plain(t))),
    }


def parse_quest(title, text, quest_names, guide_text=None):
    d = template_params(text, "Quest details") or {}
    r = template_params(text, "Quest rewards") or {}
    if not d:
        return None
    qp = None
    m = re.match(r"\s*(\d+)", r.get("qp", "") or "")
    if m:
        qp = int(m.group(1))
    start_links = [m.group(1) for m in LINK.finditer(d.get("start", ""))]
    req = parse_requirements(d.get("requirements", ""), quest_names)
    walk = guide_text
    if walk is None:
        i = text.find("==Walkthrough==")
        walk = text[i:] if i >= 0 else ""
    return {
        "name": title,
        "difficulty": plain(d.get("difficulty", "")),
        "length": plain(d.get("length", "")),
        "start": plain(d.get("start", ""))[:200],
        "startPlace": start_links[-1] if start_links else None,
        "members": "free" not in (d.get("members", "") or "").lower(),
        "qp": qp or 0,
        "req": req,
        "items": parse_items(d.get("items", "")),
        "recommended": parse_items(d.get("recommended", "")),
        "kills": plain(d.get("kills", ""))[:300],
        "guide": guide_features(walk),
        "hasQuickGuide": guide_text is not None,
    }


# Fetching -----------------------------------------------------------------------------------

def _members(client, cat):
    out, cont = [], None
    while True:
        p = {"action": "query", "list": "categorymembers", "cmtitle": cat, "cmlimit": "500", "cmnamespace": "0"}
        if cont:
            p["cmcontinue"] = cont
        d = client.wiki_api(p)
        out += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        cont = (d.get("continue") or {}).get("cmcontinue")
        if not cont:
            return out
        time.sleep(1.0)


def _texts(client, titles, sleep=1.0):
    out = {}
    for k in range(0, len(titles), 50):
        d = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                             "titles": "|".join(titles[k:k + 50]), "redirects": "1"})
        for p in d.get("query", {}).get("pages", []):
            revs = p.get("revisions") or []
            if revs:
                out[p["title"]] = revs[0]["slots"]["main"]["content"]
        time.sleep(sleep)
    return out


def fetch_all(client, sleep=1.0):
    """Every quest and miniquest, parsed. About 12 requests."""
    titles = []
    for cat in ("Category:Quests", "Category:Miniquests"):
        titles += [t for t in _members(client, cat) if "/" not in t and not t.startswith(("Quests", "Quest "))]
    titles = sorted(set(titles))
    texts = _texts(client, titles, sleep)
    texts = {t: x for t, x in texts.items() if "{{Quest details" in x}
    names = {norm(t): t for t in texts}
    guides = _texts(client, [t + "/Quick guide" for t in texts], sleep)
    out = {}
    for t, x in texts.items():
        q = parse_quest(t, x, names, guides.get(t + "/Quick guide"))
        if q:
            q["miniquest"] = "{{Infobox Quest" in x and "miniquest" in x[:3000].lower()
            out[t] = q
    return out


def store(db, data):
    now = int(time.time())
    db.conn.executemany("INSERT OR REPLACE INTO quest_data (name, data, fetched) VALUES (?,?,?)",
                        [(k, json.dumps(v), now) for k, v in data.items()])
    db.conn.commit()


def load(db):
    """{name: quest} from the database, or the shipped snapshot if it has not been fetched yet."""
    rows = db.q("SELECT name, data FROM quest_data")
    if rows:
        return {r["name"]: json.loads(r["data"]) for r in rows}
    return load_snapshot()


def load_snapshot():
    try:
        with open(SNAPSHOT, encoding="utf-8") as f:
            return json.load(f)["quests"]
    except (OSError, ValueError, KeyError):
        return {}


def fetched_at(db):
    r = db.one("SELECT MAX(fetched) AS t FROM quest_data")
    return r["t"] if r else None


def refresh(db, client, max_age=7 * 86400, sleep=1.0):
    """Fetch again when the stored data is older than a week. Returns the count stored."""
    last = fetched_at(db)
    if last and time.time() - last < max_age:
        return 0
    data = fetch_all(client, sleep)
    if len(data) > 50:
        store(db, data)
    return len(data)
