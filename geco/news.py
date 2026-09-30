"""Old School news: game updates, developer blogs, polls and Jagex's news feed.

Sources (public, read-only, sent with the configured User-Agent):
  OSRS Wiki update pages  every Jagex post is copied to the Wiki's Update namespace with its
                          date, category and full text, including links to the items it
                          mentions. Read through the MediaWiki API in bulk (lists of up to 500
                          posts, then the text of 20 posts per request).
  Jagex news RSS          the newest posts, checked every 30 minutes, so a post shows up
                          before the Wiki has copied it.

Each post is tagged with the tradeable items it mentions (Wiki links to an item page, or an
item's full name when it is at least two words) and the market categories of those items.
Developer blogs, polls and future update posts are marked as upcoming: content that has been
announced but is not in the game yet.
"""
import calendar
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import networth

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    nid TEXT PRIMARY KEY, t INTEGER NOT NULL, title TEXT NOT NULL, url TEXT, kind TEXT, source TEXT,
    summary TEXT, items TEXT, cats TEXT, upcoming INTEGER DEFAULT 0, fetched INTEGER);
CREATE INDEX IF NOT EXISTS news_t ON news(t);
"""
WIKI_CATEGORIES = [("Category:Game updates", "game"), ("Category:Developer Blogs", "devblog"),
                   ("Category:Future Updates", "future"), ("Category:Polls", "poll")]
KIND_LABELS = {"game": "Game update", "devblog": "Developer blog", "future": "Future update", "poll": "Poll",
               "community": "Community", "technical": "Technical", "event": "Event", "news": "News"}
UPCOMING = {"devblog", "future", "poll"}
RSS_KINDS = {"game updates": "game", "future updates": "future", "dev blogs": "devblog",
             "developer blogs": "devblog", "polls": "poll", "community": "community", "technical": "technical",
             "events": "event"}
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


# Tagging ------------------------------------------------------------------------------

class Tagger:
    """Finds the tradeable items a post mentions."""

    def __init__(self, mapping):
        self.by_name = {m["name"].lower(): iid for iid, m in mapping.items() if m.get("name")}
        multi = sorted((n for n in self.by_name if " " in n and len(n) >= 8), key=len, reverse=True)
        # One alternation per first letter keeps the regex fast on long posts.
        self.patterns = {}
        for n in multi:
            self.patterns.setdefault(n[0], []).append(re.escape(n))
        self.regex = {k: re.compile(r"(?<![\w'])(" + "|".join(v) + r")(?![\w'])") for k, v in self.patterns.items()}

    def tag(self, wikitext):
        found = {}
        for target in LINK_RE.findall(wikitext or ""):
            iid = self.by_name.get(target.strip().replace("_", " ").lower())
            if iid:
                found[iid] = found.get(iid, 0) + 1
        text = (wikitext or "").lower()
        for rx in self.regex.values():
            for m in rx.findall(text):
                iid = self.by_name.get(m)
                if iid:
                    found[iid] = found.get(iid, 0) + 1
        return found


def _cats_for(ids, mapping):
    out = {}
    for iid in ids:
        name = (mapping.get(iid) or {}).get("name")
        if name:
            c = networth.category(name)
            out[c] = out.get(c, 0) + 1
    return sorted(out, key=lambda c: -out[c])


def _parse_date(s):
    """'30 September 2026' (the Wiki's date format) to a UTC day timestamp."""
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s or "")
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return calendar.timegm((int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)), 0, 0, 0))


def _summary(wikitext):
    """First sentence or two of readable text."""
    text = TEMPLATE_RE.sub("", wikitext or "")
    text = TEMPLATE_RE.sub("", text)
    text = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)
    text = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"'{2,}", "", text)
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 40 and not line.startswith(("=", "*", "#", "|", "{", "!", "__")):
            return html.unescape(line)[:280]
    return ""


def _store(db, rows):
    if rows:
        db.many("INSERT OR REPLACE INTO news (nid, t, title, url, kind, source, summary, items, cats, upcoming, "
                "fetched) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)


# Fetching -----------------------------------------------------------------------------

def _wiki_titles(client, cat, since, limit=500):
    """(title, category timestamp) for posts in a Wiki category newer than `since`, newest first."""
    out, cont = [], None
    while True:
        params = {"action": "query", "list": "categorymembers", "cmtitle": cat, "cmsort": "timestamp",
                  "cmdir": "desc", "cmprop": "title|timestamp", "cmlimit": str(limit), "cmnamespace": "112"}
        if cont:
            params["cmcontinue"] = cont
        resp = client.wiki_api(params)
        members = (resp.get("query") or {}).get("categorymembers") or []
        stop = False
        for m in members:
            t = calendar.timegm(time.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%SZ"))
            if t < since:
                stop = True
                break
            out.append((m["title"], t))
        cont = (resp.get("continue") or {}).get("cmcontinue")
        if stop or not cont:
            return out
        time.sleep(1.0)


def fetch_wiki(db, client, mapping, days=800, max_new=400, sleep=1.0):
    """Store update posts from the Wiki that are not saved yet. Returns how many were added."""
    tagger = Tagger(mapping)
    since = int(time.time() - days * 86400)
    known = {r["nid"] for r in db.q("SELECT nid FROM news WHERE source='wiki'")}
    todo = []
    for cat, kind in WIKI_CATEGORIES:
        for title, t in _wiki_titles(client, cat, since):
            if "wiki:" + title not in known and all(x[0] != title for x in todo):
                todo.append((title, t, kind))
        time.sleep(sleep)
    todo = todo[:max_new]
    added = 0
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        resp = client.wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                                "titles": "|".join(b[0] for b in batch)})
        pages = {p["title"]: p for p in (resp.get("query") or {}).get("pages") or []}
        rows = []
        for title, t, kind in batch:
            page = pages.get(title) or {}
            revs = page.get("revisions") or []
            text = revs[0]["slots"]["main"]["content"] if revs else ""
            head = re.search(r"\{\{Update\s*\|([^}]*)\}\}", text or "")
            fields = {}
            if head:
                for part in head.group(1).split("|"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        fields[k.strip().lower()] = v.strip()
            day = _parse_date(fields.get("date")) or t
            found = tagger.tag(text)
            ids = sorted(found, key=lambda k: -found[k])[:40]
            k = {"game": "game", "devblog": "devblog", "future": "future", "poll": "poll"}.get(
                (fields.get("category") or "").lower(), kind)
            rows.append(("wiki:" + title, day, title.split(":", 1)[-1], fields.get("url"), k, "wiki",
                         _summary(text), json.dumps(ids), json.dumps(_cats_for(ids, mapping)),
                         1 if k in UPCOMING else 0, int(time.time())))
        _store(db, rows)
        added += len(rows)
        time.sleep(sleep)
    return added


def fetch_rss(db, client, mapping):
    """Store the newest Jagex posts from the RSS feed (text tagging is limited to the blurb)."""
    root = ET.fromstring(client.news_rss())
    tagger = Tagger(mapping)
    rows = []
    wiki_urls = {r["url"] for r in db.q("SELECT url FROM news WHERE source='wiki' AND url IS NOT NULL")}
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or link in wiki_urls:
            continue
        try:
            t = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except (TypeError, ValueError):
            t = int(time.time())
        kind = RSS_KINDS.get((it.findtext("category") or "").strip().lower(), "news")
        desc = html.unescape(re.sub(r"<[^>]+>", "", it.findtext("description") or "")).strip()
        found = tagger.tag(title + "\n" + desc)
        ids = sorted(found, key=lambda k: -found[k])
        rows.append(("rss:" + link, t, title, link, kind, "rss", desc[:280], json.dumps(ids),
                     json.dumps(_cats_for(ids, mapping)), 1 if kind in UPCOMING else 0, int(time.time())))
    _store(db, rows)
    return len(rows)


def demo_news(db, mapping, now=None):
    """Synthetic posts for demo mode (no network)."""
    if db.one("SELECT 1 AS x FROM news LIMIT 1"):
        return 0
    now = now or time.time()
    names = {m["name"]: iid for iid, m in mapping.items()}
    posts = [
        (150, "game", "Barrows Rebalance", "Dharok's and Guthan's sets get a damage rework.",
         ["Dharok's helm", "Dharok's platebody", "Dharok's platelegs", "Dharok's greataxe"]),
        (120, "devblog", "Herblore Changes Blog", "Proposed changes to prayer and restore potions.",
         ["Prayer potion(4)", "Super restore(4)", "Grimy ranarr weed"]),
        (100, "poll", "Poll 82: Skilling Rewards", "Vote on new uses for runite bars and magic logs.",
         ["Runite bar", "Magic logs"]),
        (75, "game", "Wilderness Loot Update", "Bandos drops rebalanced and new wilderness rewards.",
         ["Bandos chestplate", "Bandos tassets"]),
        (60, "game", "Fishing Trawler Rework", "Shark and anglerfish catch rates adjusted.",
         ["Shark", "Raw anglerfish", "Anglerfish"]),
        (40, "devblog", "Rune Economy Blog", "Looking at the supply of death and blood runes.",
         ["Death rune", "Blood rune", "Nature rune"]),
        (21, "game", "Zulrah Drop Table Changes", "Zulrah's scales drop rates adjusted.", ["Zulrah's scales"]),
        (12, "game", "Bond Price Changes", "Membership bonds now last longer.", ["Old school bond"]),
        (4, "future", "Coming Soon: Twisted Upgrades", "A new use for the Twisted bow is on the way.",
         ["Twisted bow"]),
        (1, "poll", "Poll 83: Dragon Bones", "Should dragon bones give more prayer experience?", ["Dragon bones"]),
    ]
    rows = []
    for days_ago, kind, title, summary, items in posts:
        ids = [names[n] for n in items if n in names]
        rows.append(("demo:" + title, int((now - days_ago * 86400) // 86400 * 86400), title, None, kind, "demo",
                     summary, json.dumps(ids), json.dumps(_cats_for(ids, mapping)), 1 if kind in UPCOMING else 0,
                     int(now)))
    _store(db, rows)
    return len(rows)


# Reading ------------------------------------------------------------------------------

def _row(r, mapping):
    ids = json.loads(r["items"] or "[]")
    return {"nid": r["nid"], "t": r["t"], "title": r["title"], "url": r["url"], "kind": r["kind"],
            "kindLabel": KIND_LABELS.get(r["kind"], r["kind"]), "source": r["source"], "summary": r["summary"],
            "upcoming": bool(r["upcoming"]), "cats": json.loads(r["cats"] or "[]"),
            "items": [{"id": i, "name": (mapping.get(i) or {}).get("name"), "icon": (mapping.get(i) or {}).get("icon")}
                      for i in ids]}


def feed(db, mapping, days=60, kind=None, item=None, limit=200, held=None):
    since = int(time.time() - days * 86400)
    rows = db.q("SELECT * FROM news WHERE t>=? ORDER BY t DESC, title LIMIT ?", (since, limit * 3))
    out = []
    for r in rows:
        if kind == "upcoming" and not r["upcoming"]:
            continue
        if kind and kind not in ("upcoming", "held") and r["kind"] != kind:
            continue
        ids = json.loads(r["items"] or "[]")
        if item is not None and item not in ids:
            continue
        x = _row(r, mapping)
        if held:
            x["held"] = [i for i in ids if i in held]
            if kind == "held" and not x["held"]:
                continue
        out.append(x)
        if len(out) >= limit:
            break
    return out


def item_news(db, mapping, iid, days=800, limit=50):
    return feed(db, mapping, days, item=iid, limit=limit)


def status(db):
    r = db.one("SELECT COUNT(*) AS n, MIN(t) AS first, MAX(t) AS last, MAX(fetched) AS fetched FROM news")
    return dict(r) if r else {}

