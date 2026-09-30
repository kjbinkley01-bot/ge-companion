"""Live account data from the Bankstanding RuneLite plugin.

The plugin (runelite-plugin/) appends one JSON object per line to monthly files in
~/.runelite/bankstanding. This module reads new lines incrementally, stores them, and
derives:

  ge_offers   the latest state of each of your eight GE slots, per account
  ge_fills    every completed piece of a trade, reconstructed from offer changes
  holdings    the latest contents of bank, inventory, equipment, looting bag, seed vault,
              rune pouch, Death's storage and the GE collection boxes
  loot, xp    Loot Tracker drops and experience

GE offers report cumulative numbers (quantity done and gp spent so far), so a trade is the
difference between two reports for the same offer. Offers filled while the app was closed
are caught up from the next report.
"""
import glob
import json
import os
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_files (path TEXT PRIMARY KEY, offset INTEGER NOT NULL, mtime REAL);
CREATE TABLE IF NOT EXISTS accounts (
    acct TEXT PRIMARY KEY, name TEXT, first_seen INTEGER, last_seen INTEGER, world INTEGER,
    online INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ge_offers (
    acct TEXT NOT NULL, slot INTEGER NOT NULL, t INTEGER NOT NULL, state TEXT NOT NULL,
    item INTEGER, price INTEGER, total INTEGER, done INTEGER, spent INTEGER,
    opened INTEGER, last_fill INTEGER,
    PRIMARY KEY (acct, slot));
CREATE TABLE IF NOT EXISTS ge_fills (
    fid INTEGER PRIMARY KEY AUTOINCREMENT, acct TEXT NOT NULL, slot INTEGER, t INTEGER NOT NULL,
    item INTEGER NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, gp INTEGER NOT NULL,
    offer_price INTEGER, caught_up INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ge_fills_item ON ge_fills(acct, item, t);
CREATE TABLE IF NOT EXISTS holdings_live (
    acct TEXT NOT NULL, container TEXT NOT NULL, t INTEGER NOT NULL, items TEXT NOT NULL,
    PRIMARY KEY (acct, container));
CREATE TABLE IF NOT EXISTS loot (
    lid INTEGER PRIMARY KEY AUTOINCREMENT, acct TEXT NOT NULL, t INTEGER NOT NULL, source TEXT,
    kind TEXT, items TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS xp_log (
    acct TEXT NOT NULL, t INTEGER NOT NULL, skill TEXT NOT NULL, xp INTEGER, level INTEGER,
    PRIMARY KEY (acct, skill, t));
CREATE TABLE IF NOT EXISTS flip_ignore (acct TEXT NOT NULL, item INTEGER NOT NULL,
    PRIMARY KEY (acct, item));
"""

BUY_STATES = {"BUYING", "BOUGHT", "CANCELLED_BUY"}
SELL_STATES = {"SELLING", "SOLD", "CANCELLED_SELL"}
ACTIVE = {"BUYING", "SELLING"}


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


def default_folder():
    base = os.path.join(os.path.expanduser("~"), ".runelite")
    new, old = os.path.join(base, "bankstanding"), os.path.join(base, "ge-companion")
    # Early builds of the plugin wrote to ge-companion.
    return old if os.path.isdir(old) and not os.path.isdir(new) else new


# Reading the plugin files ---------------------------------------------------------------

def ingest(db, folder, max_lines=200000):
    """Read new lines from every events file. Returns the number of events stored."""
    if not folder or not os.path.isdir(folder):
        return 0
    count = 0
    for path in sorted(glob.glob(os.path.join(folder, "events-*.jsonl"))):
        row = db.one("SELECT offset FROM ingest_files WHERE path=?", (path,))
        offset = row["offset"] if row else 0
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size < offset:
            offset = 0  # file was replaced
        if size == offset:
            continue
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        # Only whole lines; a line still being written is picked up next time.
        end = data.rfind(b"\n")
        if end < 0:
            continue
        chunk = data[:end + 1]
        events = []
        for raw in chunk.splitlines():
            if not raw.strip():
                continue
            try:
                events.append(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                continue
            if len(events) >= max_lines:
                break
        apply_events(db, events)
        count += len(events)
        db.run("INSERT OR REPLACE INTO ingest_files (path, offset, mtime) VALUES (?,?,?)",
               (path, offset + end + 1, os.path.getmtime(path)))
    return count


def apply_events(db, events):
    for e in events:
        acct = e.get("acct")
        if not acct:
            continue
        t = int((e.get("t") or time.time() * 1000) // 1000)
        _touch_account(db, acct, e.get("name"), t, e)
        kind = e.get("type")
        if kind == "offer":
            apply_offer(db, acct, t, e)
        elif kind == "container":
            db.run("INSERT OR REPLACE INTO holdings_live (acct, container, t, items) VALUES (?,?,?,?)",
                   (acct, e.get("container"), t, json.dumps(e.get("items") or [])))
        elif kind == "loot":
            db.run("INSERT INTO loot (acct, t, source, kind, items) VALUES (?,?,?,?,?)",
                   (acct, t, e.get("source"), e.get("kind"), json.dumps(e.get("items") or [])))
        elif kind == "xp":
            db.run("INSERT OR REPLACE INTO xp_log (acct, t, skill, xp, level) VALUES (?,?,?,?,?)",
                   (acct, t, e.get("skill"), e.get("xp"), e.get("level")))


def _touch_account(db, acct, name, t, e):
    row = db.one("SELECT acct FROM accounts WHERE acct=?", (acct,))
    online = {"login": 1, "logout": 0}.get(e.get("type"))
    if not row:
        db.run("INSERT INTO accounts (acct, name, first_seen, last_seen, world, online) VALUES (?,?,?,?,?,?)",
               (acct, name, t, t, e.get("world"), online or 0))
        return
    sets, args = ["last_seen=MAX(COALESCE(last_seen,0), ?)"], [t]
    if name:
        sets.append("name=?")
        args.append(name)
    if e.get("world"):
        sets.append("world=?")
        args.append(e["world"])
    if online is not None:
        sets.append("online=?")
        args.append(online)
    db.run(f"UPDATE accounts SET {', '.join(sets)} WHERE acct=?", args + [acct])


# GE offers into fills -------------------------------------------------------------------

def _side(state):
    if state in BUY_STATES:
        return "buy"
    if state in SELL_STATES:
        return "sell"
    return None


def fill_from(prev, cur):
    """The part of a trade that happened between two reports of one slot.

    Returns (side, qty, gp, new_offer). An offer is the same one while its side, item, price
    and total quantity stay the same and its done count does not go backwards; anything else
    is a new offer, and whatever it has already done counts as filled now.
    """
    side = _side(cur["state"])
    if side is None:
        return None, 0, 0, False
    same = (prev is not None and _side(prev["state"]) == side and prev["item"] == cur["item"]
            and prev["price"] == cur["price"] and prev["total"] == cur["total"]
            and cur["done"] >= (prev["done"] or 0))
    if same:
        return side, cur["done"] - (prev["done"] or 0), cur["spent"] - (prev["spent"] or 0), False
    return side, cur["done"], cur["spent"], True


def apply_offer(db, acct, t, e):
    cur = {"state": e.get("state"), "item": int(e.get("item") or 0), "price": int(e.get("price") or 0),
           "total": int(e.get("total") or 0), "done": int(e.get("done") or 0), "spent": int(e.get("spent") or 0)}
    slot = int(e.get("slot") or 0)
    prev = db.one("SELECT * FROM ge_offers WHERE acct=? AND slot=?", (acct, slot))
    side, qty, gp, new = fill_from(prev, cur)
    opened = t if (new or not prev) else prev["opened"]
    last_fill = prev["last_fill"] if (prev and not new) else None
    if side and qty > 0:
        # First sight of an offer that had already traded (older than the plugin, or filled
        # while the app was closed) is flagged, since its exact time is unknown.
        caught_up = 1 if (prev is None or new) and cur["done"] > 0 else 0
        db.run("INSERT INTO ge_fills (acct, slot, t, item, side, qty, gp, offer_price, caught_up) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (acct, slot, t, cur["item"], side, qty, gp, cur["price"], caught_up))
        last_fill = t
    db.run("INSERT OR REPLACE INTO ge_offers (acct, slot, t, state, item, price, total, done, spent, opened, "
           "last_fill) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
           (acct, slot, t, cur["state"], cur["item"], cur["price"], cur["total"], cur["done"], cur["spent"],
            opened, last_fill))


# Sell proceeds and tax ------------------------------------------------------------------

def sell_split(gp, qty, offer_price, tax, iid):
    """(gross, tax, net) for a sell fill.

    Every item on a sell fills at the offer price or better, so gross is at least
    offer_price * qty. If the reported gp is below that, it must already be after tax.
    """
    if qty <= 0:
        return gp, 0, gp
    if gp < offer_price * qty:
        net = gp
        each_net = gp / qty
        # Recover the gross price per item from the net (tax is 2% rounded down, capped).
        # A sell never fills below its offer price, so start the search there.
        lo = max(int(each_net), offer_price)
        hi = max(lo, int(each_net * 1.03) + 2)
        while lo < hi:
            mid = (lo + hi) // 2
            if mid - tax(mid, iid) >= each_net:
                hi = mid
            else:
                lo = mid + 1
        gross = lo * qty
        return gross, gross - net, net
    each = gp // qty
    t = tax(each, iid) * qty
    return gp, t, gp - t


# Flips from real trades -----------------------------------------------------------------

def match_flips(fills, tax, ignore=frozenset()):
    """First in, first out matching of each item's buys to its sells, per account.

    Returns (closed, open_lots). Sells with no earlier buy (items from drops or from before
    tracking) are not flips and are skipped.
    """
    lots = {}
    closed = []
    for f in sorted(fills, key=lambda f: (f["t"], f["fid"])):
        key = (f["acct"], f["item"])
        if key in ignore:
            continue
        if f["side"] == "buy":
            lots.setdefault(key, []).append({"qty": f["qty"], "each": f["gp"] / f["qty"] if f["qty"] else 0,
                                             "t": f["t"], "fid": f["fid"], "seed": f.get("seed", False)})
            continue
        gross, t_, net = sell_split(f["gp"], f["qty"], f["offer_price"] or 0, tax, f["item"])
        need = f["qty"]
        queue = lots.get(key, [])
        while need > 0 and queue:
            lot = queue[0]
            take = min(need, lot["qty"])
            share = take / f["qty"]
            closed.append({"acct": f["acct"], "item": f["item"], "qty": take, "buy_each": lot["each"],
                           "sell_each": gross / f["qty"], "tax": t_ * share, "net": net * share,
                           "cost": lot["each"] * take, "profit": net * share - lot["each"] * take,
                           "buy_t": lot["t"], "sell_t": f["t"]})
            lot["qty"] -= take
            need -= take
            if lot["qty"] == 0:
                queue.pop(0)
    open_lots = [dict(lot, acct=k[0], item=k[1]) for k, q in lots.items() for lot in q if lot["qty"] > 0]
    return closed, open_lots


def sync_flips(db, tax, keep_days=365):
    """Rebuild the automatic rows of the flip log from real trades."""
    fills = db.q("SELECT * FROM ge_fills WHERE t >= ?", (int(time.time() - keep_days * 86400),))
    ignore = {(r["acct"], r["item"]) for r in db.q("SELECT acct, item FROM flip_ignore")}
    closed, open_lots = match_flips(fills, tax, ignore)
    rows = []
    for c in closed:
        rows.append((c["item"], int(c["qty"]), int(round(c["buy_each"])), int(round(c["sell_each"])),
                     c["buy_t"], c["sell_t"], None, "auto", c["acct"]))
    for lot in open_lots:
        rows.append((lot["item"], int(lot["qty"]), int(round(lot["each"])), None, lot["t"], None, None,
                     "auto", lot["acct"]))
    with db.lock:
        db.conn.execute("DELETE FROM flips WHERE source='auto'")
        db.conn.executemany("INSERT INTO flips (item_id, qty, buy_price, sell_price, buy_ts, sell_ts, note, "
                            "source, acct) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        db.conn.commit()
    return len(closed), len(open_lots)


# Views ----------------------------------------------------------------------------------

def accounts(db):
    return db.q("SELECT * FROM accounts ORDER BY last_seen DESC")


def slots(db, acct, rows_by_id, mapping, now=None):
    """Your eight GE slots with progress and how each price compares to the market now."""
    now = now or time.time()
    out = []
    for o in db.q("SELECT * FROM ge_offers WHERE acct=? ORDER BY slot", (acct,)):
        m = mapping.get(o["item"], {})
        live = rows_by_id.get(o["item"]) or {}
        side = _side(o["state"])
        r = dict(o, name=m.get("name"), icon=m.get("icon"), side=side,
                 progress=(o["done"] / o["total"]) if o["total"] else 0,
                 market_low=live.get("low"), market_high=live.get("high"))
        note = None
        if o["state"] in ACTIVE and live:
            idle = now - (o["last_fill"] or o["opened"] or o["t"])
            if side == "buy" and live.get("low") and o["price"] < live["low"]:
                gap = live["low"] / o["price"] - 1
                if gap > 0.005:
                    note = f"Below the market by {gap * 100:.1f}%: may not fill"
            elif side == "sell" and live.get("high") and o["price"] > live["high"]:
                gap = o["price"] / live["high"] - 1
                if gap > 0.005:
                    note = f"Above the market by {gap * 100:.1f}%: may not fill"
            r["idle"] = int(idle)
        r["note"] = note
        out.append(r)
    return out


def recent_fills(db, acct=None, limit=100):
    if acct:
        return db.q("SELECT * FROM ge_fills WHERE acct=? ORDER BY t DESC, fid DESC LIMIT ?", (acct, limit))
    return db.q("SELECT * FROM ge_fills ORDER BY t DESC, fid DESC LIMIT ?", (limit,))
