"""Skill training methods and temporary boosts from the OSRS Wiki's training guides.

The members training guides share a layout: sections like "Fastest experience" and "Other
methods" hold one subsection per method, headed "Levels 55-99: Knights of Ardougne", with an
inventory, requirements in the text, and XP rates either in a table (often by level, sometimes
with a GP/XP column) or in sentences like "around 50,000-60,000 experience per hour". Each
method keeps its level range, rate curve, cost per XP when the Wiki lists one, requirements,
items and a link to its section.

Boosts come from the "Temporary skill boost" subpages (one table per skill). Only visible
boosts count for quest requirements, and random ones (spicy stew) are left out.

Read in bulk (two requests, all guides in one and all boost tables in the other) and
refreshed weekly with the quest data. A parsed snapshot ships in
`geco/skillguides_snapshot.json`. Content from the OSRS Wiki (CC BY-NC-SA 3.0).
"""
import json
import math
import os
import re
import time
from urllib.parse import quote

from .quests import LINK, SCP, SKILL_SET, norm, plain

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skillguides_snapshot.json")
WIKI = "https://oldschool.runescape.wiki/w/"
MELEE = "Pay-to-play Melee training"
PAGES = {
    "Attack": MELEE, "Strength": MELEE, "Defence": MELEE, "Ranged": "Pay-to-play Ranged training",
    "Prayer": "Pay-to-play Prayer training", "Magic": "Pay-to-play Magic training",
    "Runecraft": "Pay-to-play Runecraft training", "Construction": "Construction training",
    "Agility": "Agility training", "Herblore": "Herblore training", "Thieving": "Thieving training",
    "Crafting": "Pay-to-play Crafting training", "Fletching": "Fletching training", "Slayer": "Slayer training",
    "Hunter": "Hunter training", "Mining": "Pay-to-play Mining training", "Smithing": "Pay-to-play Smithing training",
    "Fishing": "Pay-to-play Fishing training", "Cooking": "Pay-to-play Cooking training",
    "Firemaking": "Pay-to-play Firemaking training", "Woodcutting": "Pay-to-play Woodcutting training",
    "Sailing": "Sailing training",  # Farming's guide counts XP per run, so the training table covers it
}
BOOST_SKILLS = sorted(set(PAGES) | {"Hitpoints", "Farming"})
# Sections that describe gear, tips or references rather than a way to train.
SKIP = re.compile(r"(?i)^(references|see also|trivia|changes|gallery|equipment|inventory|recommended|"
                  r"general|tips|choosing|comparison|post.?99|quests? for|quest experience|overview|"
                  r"strateg|weapon choice|items in order|other equipment|ore table|fish$|experience rates/profit|"
                  r"recommended tree|using slayer|player-owned|task summary|slayer locations|prayer$|"
                  r"dwarf multicannon|combating|planning to die|logging out|obtaining|restoring|setting up|example)")
SPECIFIC_SKIP = re.compile(r"(?i)\bquesting\b|quiz|^(starting off|other (?:early )?(?:training )?methods|"
                           r"profitable methods|efficiency|passive training methods)$")
DASH = "\u2013\u2014"


# Template expansion -------------------------------------------------------------------------
# Guide tables compute their numbers with Wiki variables, #expr and GE price templates. These
# are worked out here (prices from the GE when the guides are read); requirement templates and
# inventories are kept as written, and any other template gives up its parameter text.

KEEP = re.compile(r"(?i)^\s*(scp|boostable|inventory|equipment)\b")
DROP = re.compile(r"(?i)^\s*(main|see also|reflist|refn|clear|external|#if|#switch|#tag|anchor|efn|citation|cite|"
                  r"notelist|infobox|navbox|training guides|members|skill clear|update|has|tooltip|toc|otheruses)\b")
TOKEN = re.compile(r"\s*(\d+(?:\.\d+)?(?:e[+-]?\d+)?|round|mod|ceil|floor|abs|trunc|not|and|or|<=|>=|!=|<>|[-+*/^()<>=])", re.I)


def _expr(src):
    """Evaluate a MediaWiki #expr (numbers, + - * / ^ mod round, ceil floor abs trunc)."""
    src = src.replace(",", "")
    toks, i = [], 0
    while i < len(src):
        m = TOKEN.match(src, i)
        if not m:
            if src[i:].strip() == "":
                break
            raise ValueError(src)
        toks.append(m.group(1).lower())
        i = m.end()
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def unary():
        t = take()
        if t == "-":
            return -unary()
        if t == "+":
            return unary()
        if t in ("ceil", "floor", "abs", "trunc"):
            v = unary()
            return {"ceil": math.ceil, "floor": math.floor, "abs": abs, "trunc": int}[t](v)
        if t == "(":
            v = binary(0)
            take()
            return v
        return float(t)

    prec = {"or": 1, "and": 2, "=": 3, "<": 3, ">": 3, "<=": 3, ">=": 3, "!=": 3, "<>": 3, "+": 4, "-": 4,
            "*": 5, "/": 5, "mod": 5, "round": 0.5, "^": 6}

    def binary(lvl):
        v = unary()
        while peek() in prec and prec[peek()] > lvl:
            op = take()
            r = binary(prec[op] if op != "^" else prec[op] - 0.1)
            if op == "+": v += r
            elif op == "-": v -= r
            elif op == "*": v *= r
            elif op == "/": v = v / r if r else 0.0
            elif op == "mod": v = math.fmod(int(v), int(r)) if int(r) else 0.0
            elif op == "^": v = v ** r
            elif op == "round": v = round(v, int(r))
            elif op in ("=",): v = float(v == r)
            elif op in ("!=", "<>"): v = float(v != r)
            elif op == "<": v = float(v < r)
            elif op == ">": v = float(v > r)
            elif op == "<=": v = float(v <= r)
            elif op == ">=": v = float(v >= r)
            elif op == "and": v = float(bool(v and r))
            elif op == "or": v = float(bool(v or r))
        return v

    v = binary(0)
    return int(v) if float(v).is_integer() else round(v, 4)


def expand(text, price=None):
    """Work out the templates that make guide numbers, in page order (variables are set by
    #vardefine before the tables that read them); see the note above."""
    vars_ = {}
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    text = re.sub(r"(?s)<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", text)
    n = len(text)

    def apply(body):
        parts = [p.replace("\x01", "|") for p in body.split("|")]
        raw = body.replace("\x01", "|")
        name = parts[0].strip()
        low = name.lower()
        if KEEP.match(raw):
            return "{{" + raw + "}}"
        try:
            if low == "var":
                if len(parts) >= 3:
                    vars_[parts[1].strip()] = parts[2].strip()
                    return parts[2].strip()
                return vars_.get(parts[1].strip(), "") if len(parts) > 1 else ""
            if low.startswith(("#vardefine:", "#vardefineecho:")):
                k = name.split(":", 1)[1].strip()
                vars_[k] = parts[1].strip() if len(parts) > 1 else ""
                return vars_[k] if low.startswith("#vardefineecho") else ""
            if low.startswith("#var:"):
                k = name.split(":", 1)[1].strip()
                return vars_.get(k, parts[1].strip() if len(parts) > 1 else "")
            if low.startswith("#expr:"):
                return str(_expr(raw.split(":", 1)[1]))
            if low in ("formatexpr", "fe"):
                return str(_expr(parts[1])) if len(parts) > 1 else ""
            if low.startswith("formatnum:"):
                v = raw.split(":", 1)[1].strip()
                try:
                    f = float(v.replace(",", ""))
                    return f"{f:,.0f}" if abs(f) >= 100 or f.is_integer() else f"{f:,.2f}"
                except ValueError:
                    return v
            if low in ("gep", "gept", "geprice", "gepricetotal", "gepu"):
                item = parts[1].strip() if len(parts) > 1 else ""
                qty = float(parts[2]) if len(parts) > 2 and re.fullmatch(r"\s*\d+(\.\d+)?\s*", parts[2]) else 1
                p = price(item) if price else None
                return str(round(p * qty)) if p is not None else "?"
            if low == "coins" and len(parts) > 1:
                v = parts[-1].strip()
                try:
                    return str(round(_expr(v), 2))
                except (ValueError, ZeroDivisionError, OverflowError, IndexError):
                    return v
            if low in ("nowrap", "small", "nobr", "big", "color", "colour", "tick", "mmgtable num"):
                return parts[-1].strip() if len(parts) > 1 else ""
            if low in ("plink", "plinkt", "plinkp", "plinkit", "item"):
                return "[[" + parts[1].strip() + "]]" if len(parts) > 1 else ""
        except (ValueError, ZeroDivisionError, OverflowError, IndexError, TypeError):
            return "?"
        if DROP.match(raw) or low.startswith("#"):
            return ""
        vals = []
        for p_ in parts[1:]:
            k, eq, v = p_.partition("=")
            if eq and re.fullmatch(r"\s*[\w ]+\s*", k):
                vals.append(f"{k.strip()}: {v.strip()}")
            else:
                vals.append(p_.strip())
        return "\n".join(v for v in vals if v)

    def template(i):
        """Expand the template starting at text[i] ("{{"); returns (text, next index)."""
        j, buf = i + 2, []
        while j < n:
            if text.startswith("{{", j):
                t, j = template(j)
                buf.append(t)
            elif text.startswith("}}", j):
                return apply("".join(buf)), j + 2
            elif text.startswith("[[", j):
                k = text.find("]]", j)
                k = n if k < 0 else k + 2
                buf.append(text[j:k].replace("|", "\x01"))
                j = k
            else:
                buf.append(text[j])
                j += 1
        return "{{" + "".join(buf).replace("\x01", "|"), n

    out, i = [], 0
    while i < n:
        k = text.find("{{", i)
        if k < 0:
            out.append(text[i:])
            break
        out.append(text[i:k])
        t, i = template(k)
        out.append(t)
    return "".join(out)


# Wikitext tables ----------------------------------------------------------------------------

ATTR = re.compile(r'^\s*((?:[\w-]+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s|]+)\s*)+)\|(?!\|)')


def _cell(s):
    """(text, colspan, rowspan) of one table cell."""
    m = ATTR.match(s)
    attrs = m.group(1) if m else ""
    cs = re.search(r"colspan\s*=\s*\"?(\d+)", attrs)
    rs = re.search(r"rowspan\s*=\s*\"?(\d+)", attrs)
    return (ATTR.sub("", s).strip(), int(cs.group(1)) if cs else 1, int(rs.group(1)) if rs else 1)


def _grid(rows):
    """Lay out rows of (text, colspan, rowspan) cells into full rows."""
    out, carry = [], {}
    for cells in rows:
        row, c = {}, 0
        for text, cs, rs in cells:
            while c in carry and carry[c][1] > 0:
                row[c] = carry[c][0]
                carry[c] = (carry[c][0], carry[c][1] - 1)
                c += 1
            for k in range(cs):
                row[c + k] = text
                if rs > 1:
                    carry[c + k] = (text, rs - 1)
            c += cs
        while c in carry and carry[c][1] > 0:
            row[c] = carry[c][0]
            carry[c] = (carry[c][0], carry[c][1] - 1)
            c += 1
        out.append([row[k] for k in sorted(row)])
    return out


def tables(text):
    """[(header labels, [row cells])] for each wikitable."""
    out = []
    for m in re.finditer(r"(?ms)^\{\|.*?^\|\}", text):
        rows = re.split(r"\n\|-(?![\d.])[^\n]*", m.group(0))  # "|-12" is a negative cell, not a new row
        parsed = []
        for chunk in rows:
            cells, kind = [], None
            for line in chunk.split("\n"):
                if line.startswith(("{|", "|}", "|+")):
                    continue
                if line.startswith("!"):
                    kind = kind or "h"
                    cells += [_cell(c) for c in re.split(r"!!|\|\|", line[1:])]
                elif line.startswith("|"):
                    kind = kind or "d"
                    cells += [_cell(c) for c in line[1:].split("||")]
                elif cells:
                    t, cs, rs = cells[-1]
                    cells[-1] = (t + "\n" + line, cs, rs)
            if cells:
                parsed.append((kind, cells))
        nh = 0
        while nh < len(parsed) and parsed[nh][0] == "h":
            nh += 1
        if not nh:
            continue
        grid = _grid([c for _, c in parsed])
        width = max(len(r) for r in grid[:nh])
        head, dup = [], set()
        for col in range(width):
            labels = []
            for r in grid[:nh]:
                if col < len(r) and r[col] not in labels:
                    labels.append(r[col])
            head.append(" ".join(labels))
        # A header cell spanning columns with nothing under it may sit over one data cell.
        first = parsed[0][1]
        c = 0
        for text_, cs, rs in first:
            if cs > 1 and rs >= nh:
                dup.update(range(c + 1, c + cs))
            c += cs
        data = []
        for r in grid[nh:]:
            if len(r) == width:
                data.append(r)
            elif dup and len(r) == width - len(dup):
                full, k = [], 0
                for col in range(width):
                    if col in dup:
                        full.append(full[-1] if full else "")
                    else:
                        full.append(r[k])
                        k += 1
                data.append(full)
        out.append((head, data))
    return out


NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\b", re.I)


def _num(s):
    m = NUM.search(s or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return v * (1000 if unit == "k" else 1_000_000 if unit == "m" else 1)


def _value(cell):
    """A number from a table cell; a range like 60,000-70,000 becomes its middle."""
    t = plain(cell)
    t = re.sub(r"\[\d+\]", "", t)
    parts = [p for p in re.split(r"\s*[" + DASH + r"-]\s*(?=\d)", t) if p.strip()]
    vals = [v for v in (_num(p) for p in parts[:2]) if v is not None]
    if not vals:
        return None
    if re.match(r"\s*[-" + DASH + r"]\s*\d", t):
        return -vals[0]
    return sum(vals) / len(vals)


XPH_HEAD = re.compile(r"(?i)(xp|exp|experience)\s*(/|per)\s*(h\b|hr|hour)|xp/h")


def table_points(head, rows):
    """[(level, xp/h, gp per xp)] from a table with an XP per hour column."""
    h = [plain(c).lower() for c in head]
    raw = [c.lower() for c in head]
    xcols = [i for i, c in enumerate(h) if XPH_HEAD.search(c) and "gp" not in c and "profit" not in c]
    if not xcols:
        return []
    lcols = [i for i, c in enumerate(h) if re.search(r"\blevels?\b|\blvl\b|^req", c) or ("{{scp" in raw[i] and not c.strip())]
    gcols = [i for i, c in enumerate(h) if re.search(r"gp\s*/\s*xp|cost per xp|gp per xp", c)]
    pcols = [i for i, c in enumerate(h) if re.search(r"profit\s*/\s*xp|profit per xp", c)]
    out = []
    for r in rows:
        xs = [v for v in (_value(r[i]) for i in xcols) if v and 500 < v < 3_000_000]
        if not xs:
            continue
        lv = _num(plain(r[lcols[0]])) if lcols else None  # a range like 20-30 starts at its first level
        if lv is not None and not 1 <= lv <= 99:
            lv = None
        g = None
        col = (gcols or pcols or [None])[0]
        if col is not None:
            p = _value(r[col])
            g = -p if p is not None else None  # the Wiki's GP/XP is profit per XP, costs negative
        out.append((int(lv) if lv else None, sum(xs) / len(xs), g))
    return out


RATE = re.compile(r"(?i)(\d[\d,.]*\s*k?)(?:\s*(?:[" + DASH + r"-]|to)\s*(\d[\d,.]*\s*k?))?\s*(?:experience|xp|exp)"
                  r"\s*(?:points\s*)?(?:per hour|an hour|/\s*h(?:ou)?r?\b|per hr\b|hourly)")


def prose_rates(text):
    """XP per hour figures stated in sentences, ranges as their middle."""
    vals = []
    for m in RATE.finditer(plain(text)):
        a = _num(m.group(1))
        b = _num(m.group(2)) if m.group(2) else None
        if a and b and b < a and b < 1000 <= a:
            b *= 1000  # "50-60k" style
        v = (a + b) / 2 if a and b else a
        if v and "k" in m.group(1).lower() and v < 1000:
            v *= 1000
        if v and 500 < v < 3_000_000:
            vals.append(v)
    return vals


# Sections -----------------------------------------------------------------------------------

HEAD = re.compile(r"(?m)^(={2,5})\s*(.*?)\s*\1\s*$")


def sections(text):
    """[(depth, title, start, end of own body, end of subtree, anchor)]."""
    hs = [(len(m.group(1)), plain(m.group(2)), m.start(), m.end(), m.group(2)) for m in HEAD.finditer(text)]
    out = []
    for k, (d, t, s, e, raw) in enumerate(hs):
        own = hs[k + 1][2] if k + 1 < len(hs) else len(text)
        tree = next((hs[j][2] for j in range(k + 1, len(hs)) if hs[j][0] <= d), len(text))
        out.append((d, t, e, own, tree, anchor(raw)))
    return out


def anchor(raw):
    """The Wiki's own section anchor (it keeps the heading's dashes), percent-encoded."""
    a = LINK.sub(lambda m: m.group(2) or m.group(1), raw)
    a = re.sub(r"'{2,3}|<[^>]+>", "", a).strip()
    return quote(re.sub(r"\s+", "_", a))


def level_range(title):
    """(lo, hi) from "Levels 55-99: ...", "Levels 40/50+: ..." or "Levels 45-49, 65-84/91/99: ..."."""
    head = title.split(":", 1)[0] if ":" in title else title
    if not re.match(r"(?i)\s*levels?\s*\d", head):
        return None, None
    nums = [int(n) for n in re.findall(r"\d+", head)]
    lo = nums[0]
    hi = 99 if head.rstrip().endswith("+") or len(nums) == 1 else nums[-1]
    return lo, max(lo, min(99, hi))


def method_name(title):
    if ":" in title and re.match(r"(?i)\s*levels?\s*\d", title):
        return title.split(":", 1)[1].strip() or title
    return re.sub(r"(?i)^\s*levels?\s*[\d/]+\s*(?:[" + DASH + r"-]\s*[\d/]+|\+)?\s*", "", title).strip() or title


# A sentence that states what a method needs, not what helps or what a quest gives.
REQ_WORDS = re.compile(r"(?i)\b(requires?|required|requiring|must (?:have )?(?:complete|completed|started)|"
                       r"need to have (?:completed|started)|players will need to have|to access)\b|^\s*requirements?:|"
                       r"\bafter completing\b.{0,80}\bunlocks?\b")
# Softer wording: often names one of several places or a later upgrade, so it is a hint.
HINT_WORDS = re.compile(r"(?i)\b(who have completed|after completing|once .{0,30}completed|upon completing)\b")
NOT_REQ = re.compile(r"(?i)\b(with (?:the )?completion|for (?:the )?(?:fastest|optimal|best)|if (?:you|the player|players) "
                     r"(?:have|has)|has been completed|can also|additionally|alternatively|also requires|next.best|"
                     r"grants? (?:a total|an additional|access)|will grant|not required|which requires level|"
                     r"is given|awarded|obtain|reward)")
REC_WORDS = re.compile(r"(?i)recommend|helpful|useful|optional|beneficial|ideally|strongly suggest")
# "This area requires..." or "...teleport requires...": one of several places, not the method.
OPTION = re.compile(r"(?i)\b(?:this|the|that|another) (?:area|location|spot|bank|route)\b|teleport|alternative")
# "The goldsmith gauntlets require...": gear that helps, not the method.
GEAR = re.compile(r"(?i)\b(gauntlets|gloves|outfit|cape|ring|amulet|necklace|boots|helm|armour|armor|equipment|"
                  r"pouch|staff|bracelet|graceful|set)\b[^.]{0,25}\brequir")
PARTIAL = re.compile(r"(?i)partial|started|having started|until the part")


def _sentences(text):
    prose = "\n".join(l for l in text.split("\n") if not l.startswith(("|", "!", "{|", "{{Inventory", "{{Equipment")))
    prose = re.sub(r"(?s)\{\{(?:Inventory|Equipment)[^{}]*\}\}", "", prose)
    prose = re.sub(r"(?s)<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", prose)
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", prose) if s.strip()]


def requirements(skill, text, quest_names):
    """Skill levels, quests and other things a method needs, from its sentences."""
    skills, quests_, notes, own = {}, [], [], None
    for s in _sentences(text):
        rec = bool(REC_WORDS.search(s))
        req = bool(REQ_WORDS.search(s)) and not rec and not NOT_REQ.search(s)
        partial = bool(PARTIAL.search(s))
        for sk, lvl in SCP.findall(s):
            name = SKILL_SET.get(sk.strip().lower())
            lvl = int(lvl.replace(",", ""))
            if not name or lvl > 99:
                continue
            if name == skill:
                own = own or lvl
            elif req:
                skills[name] = max(skills.get(name, 0), lvl)
        for m in LINK.finditer(s):
            target = m.group(1).strip()
            k = norm(target)
            if k in quest_names and req and GEAR.search(s):
                note = "Helps: " + quest_names[k]
                if note not in notes:
                    notes.append(note)
            elif k in quest_names and req and OPTION.search(s):
                note = "One option needs " + quest_names[k]
                if note not in notes:
                    notes.append(note)
            elif k in quest_names and req and quest_names[k] not in [q["quest"] for q in quests_]:
                quests_.append({"quest": quest_names[k], "partial": partial})
            elif k in quest_names and HINT_WORDS.search(s) and not NOT_REQ.search(s):
                note = "After " + quest_names[k]
                if note not in notes:
                    notes.append(note)
            elif "diary" in target.lower() and (req or rec):
                label = plain(m.group(0))
                note = ("Needs " if req else "Helps: ") + label
                if note not in notes:
                    notes.append(note)
    return {"skills": skills, "quests": quests_[:6], "notes": notes[:5]}, own


def items(text):
    out = []
    for m in re.finditer(r"(?s)\{\{\s*(Inventory|Equipment)\b(.*?)\}\}", text):
        for part in m.group(2).split("|"):
            v = part.split("=", 1)[1] if "=" in part else part
            v = re.sub(r"<[^>]+>", "", v).strip()
            if not v or re.match(r"(?i)^(align|right|left|float|noplinks|style|class|\d+)$", v) or "=" in part and part.split("=")[0].strip().lower() in ("align", "style", "float"):
                continue
            v = re.sub(r"\s*\*\s*\d+$", "", v)
            if v not in out:
                out.append(v)
    return out[:28]


def summary(text):
    for s in _sentences(text):
        t = plain(s)
        if len(t) > 40 and not t.lower().startswith(("file:", "thumb", "main article")):
            return t[:260]
    return ""


def parse_guide(skill, title, text, quest_names, price=None):
    text = expand(text, price)
    secs = sections(text)
    methods = []
    parent = None
    for k, (d, t, s, own, tree, anc) in enumerate(secs):
        if d == 2:
            parent = t
        if SKIP.search(t) or (parent and d > 2 and SKIP.search(parent)):
            continue
        is_level = level_range(t)[0] is not None
        has_children = any(secs[j][0] == d + 1 for j in range(k + 1, len(secs)) if secs[j][2] <= tree)
        if d == 2 and not is_level and has_children:
            continue  # a group like "Fastest experience"; its subsections are the methods
        if d >= 3:
            # Below a level-headed H2 the subsections are parts of that method.
            par = next((secs[j] for j in range(k - 1, -1, -1) if secs[j][0] < d), None)
            if par and level_range(par[1])[0] is not None and par[0] == 2:
                continue
            if d >= 4:
                continue
        body = text[s:tree]
        if SPECIFIC_SKIP.search(method_name(t)):
            continue
        lo, hi = level_range(t)
        req, own_lv = requirements(skill, body, quest_names)
        if d >= 3:
            # "Lunar spells" says once that they need Lunar Diplomacy; each spell inherits it.
            par = next((secs[j] for j in range(k - 1, -1, -1) if secs[j][0] < d), None)
            if par:
                preq, _ = requirements(skill, text[par[2]:par[3]], quest_names)
                have = {q["quest"] for q in req["quests"]}
                req["quests"] += [q for q in preq["quests"] if q["quest"] not in have]
        if lo is None:
            lo, hi = own_lv or 1, 99
        pts = []
        for head, rows in tables(body):
            pts += table_points(head, rows)
        prose = prose_rates(body)
        curve = sorted({(p[0], round(p[1]), p[2]) for p in pts if p[0]}, key=lambda p: p[0])
        flat = [p[1] for p in pts if not p[0]] + prose
        gps = [p[2] for p in pts if p[2] is not None and abs(p[2]) <= 5000]
        if not curve and not flat:
            continue
        flat.sort()
        methods.append({
            "skill": skill, "name": method_name(t), "group": parent if d > 2 else None,
            "lo": lo, "hi": hi,
            "curve": [[p[0], p[1]] for p in curve][:60],
            "typical": round(flat[len(flat) // 2]) if flat else None,
            "low": round(flat[0]) if flat else None, "high": round(flat[-1]) if flat else None,
            "gpPerXp": round(sorted(gps)[len(gps) // 2], 2) if gps else None,
            "req": req, "items": items(body), "summary": summary(body),
            "url": WIKI + quote(title.replace(" ", "_")) + "#" + anc,
            "fastest": bool(parent and re.search(r"(?i)fastest|recommended training", parent)),
        })
    return methods


# Boosts -------------------------------------------------------------------------------------

def parse_boosts(skill, text):
    """[{item, lo, hi, note}]: visible boosts with a fixed size (lo at level 1, hi at 99)."""
    out = []
    for head, rows in tables(text):
        for r in rows:
            vis = next((plain(c) for c in r if plain(c).lower() in ("visible", "invisible")), None)
            if vis != "Visible":
                continue
            m = re.search(r"\{\{\s*plinkt?\s*\|\s*([^|}]+)", r[0]) or LINK.search(r[0])
            if not m:
                continue
            name = m.group(1).strip()
            if name.lower().startswith(("file:", "image:")):
                continue
            inc = None
            for c in r[1:]:
                t = plain(c).replace("+", "").strip()
                if re.fullmatch(r"\d+", t):
                    inc = (int(t), int(t))
                    break
                mm = re.fullmatch(r"(\d+)\s*[-" + DASH + r"]\s*(\d+)", t)
                if mm:
                    inc = (int(mm.group(1)), int(mm.group(2)))
                    break
                if re.search(r"[±?]|\bor\b|,", t) and re.search(r"\d", t):
                    break  # random or unclear sizes
            if not inc or inc[1] <= 0:
                continue
            note = plain(r[-1]) if len(r) > 3 else ""
            out.append({"item": name, "lo": inc[0], "hi": inc[1], "note": note[:160] if note != "N/A" else ""})
    return out


def boost_at(b, level):
    """Levels a boost adds at a level (percentage boosts grow with level)."""
    return int(b["lo"] + (b["hi"] - b["lo"]) * max(0, level - 1) / 98)


# Fetching -----------------------------------------------------------------------------------

def _texts(client, titles):
    d = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                         "titles": "|".join(titles), "redirects": "1"})
    out = {}
    for p in d.get("query", {}).get("pages", []):
        revs = p.get("revisions") or []
        if revs:
            out[p["title"]] = revs[0]["slots"]["main"]["content"]
    return out


def fetch_all(client, quest_names, price=None, sleep=1.0):
    titles = sorted(set(PAGES.values()))
    texts = _texts(client, titles)
    time.sleep(sleep)
    btexts = _texts(client, ["Temporary skill boost/" + s for s in BOOST_SKILLS])
    by_title = {}
    for skill, title in PAGES.items():
        if title in texts:
            by_title.setdefault(title, parse_guide(skill, title, texts[title], quest_names, price))
    methods = {}
    for skill, title in PAGES.items():
        ms = by_title.get(title) or []
        methods[skill] = [dict(m, skill=skill) for m in ms]
    boosts = {}
    for t, x in btexts.items():
        boosts[t.split("/", 1)[1]] = parse_boosts(t.split("/", 1)[1], x)
    return {"source": "OSRS Wiki training guides and Temporary skill boost (CC BY-NC-SA 3.0)",
            "fetched": int(time.time()), "methods": methods, "boosts": boosts}


_cache = {}


def load(db=None):
    """The parsed guides: stored copy if fetched, else the shipped snapshot."""
    if db is not None:
        d = db.kv_get("skillguides")
        if d and d.get("methods"):
            return d
    if "snap" not in _cache:
        try:
            with open(SNAPSHOT, encoding="utf-8") as f:
                _cache["snap"] = json.load(f)
        except (OSError, ValueError):
            _cache["snap"] = {"methods": {}, "boosts": {}}
    return _cache["snap"]


def refresh(db, client, quest_names, price=None, max_age=7 * 86400):
    """Read the guides again when the stored copy is older than a week. Returns the method count."""
    d = db.kv_get("skillguides") or {}
    if d.get("fetched") and time.time() - d["fetched"] < max_age:
        return 0
    data = fetch_all(client, quest_names, price)
    if sum(len(v) for v in data["methods"].values()) >= 40:
        db.kv_set("skillguides", data)
    return sum(len(v) for v in data["methods"].values())
