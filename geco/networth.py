"""Account net worth: everything an account owns, valued live, with history.

Sources (from the RuneLite plugin):
  bank, inventory, equipment, looting bag, seed vault, rune pouch, Death's storage
  the Grand Exchange: coins reserved in buy offers, items listed in sell offers, and
  anything waiting in the eight collection boxes
  manual additions from the Portfolio tab (for storage the plugin cannot see)

Valuation:
  sell   (default) what each item would fetch listed at the current instant-buy price,
         after the 2% GE tax. The realistic "cash out" value.
  market the mid price with no tax, closer to what item price sites show.
Coins and platinum tokens count at face value. Untradeable items are not in the Wiki's price
data at all, so they are listed but count as 0.
"""
import json
import time

from . import account, categories

COINS = 995
PLATINUM = 13204
CONTAINER_LABELS = {
    "bank": "Bank", "inventory": "Inventory", "equipment": "Equipment", "looting_bag": "Looting bag",
    "seed_vault": "Seed vault", "rune_pouch": "Rune pouch", "death_storage": "Death's storage",
    "ge": "Grand Exchange", "manual": "Manual additions",
}
# Containers the coverage check expects to have seen for a complete picture.
EXPECTED = ["bank", "inventory", "equipment", "ge"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_worth (
    acct TEXT NOT NULL, ts INTEGER NOT NULL, total INTEGER, cash INTEGER, items INTEGER, ge INTEGER,
    PRIMARY KEY (acct, ts));
CREATE TABLE IF NOT EXISTS cost_seed (
    acct TEXT NOT NULL, item INTEGER NOT NULL, t INTEGER NOT NULL, qty INTEGER NOT NULL, each REAL NOT NULL,
    PRIMARY KEY (acct, item, t));
"""


# Cumulative return attribution columns (gp since tracking began, and a time weighted
# return index that ignores income): see record().
ATTRIB_COLS = [("mkt", "INTEGER"), ("trade", "INTEGER"), ("loot", "INTEGER"), ("other", "INTEGER"),
               ("twr", "REAL")]


def init(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        have = {r[1] for r in db.conn.execute("PRAGMA table_info(account_worth)")}
        for col, typ in ATTRIB_COLS:
            if col not in have:
                db.conn.execute(f"ALTER TABLE account_worth ADD COLUMN {col} {typ}")
        db.conn.commit()


WEAPON_WORDS = ("bow", "sword", "whip", "scimitar", "axe", "mace", "staff", "blowpipe", "dagger", "spear",
                "halberd", "hammer", "maul", "bludgeon", "rapier", "trident", "scythe", "wand", "claws",
                "sceptre", "longsword", "godsword", "hasta", "flail", "blade", "tentacle", "ballista")
ARMOUR_WORDS = ("helm", "platebody", "platelegs", "plateskirt", "chestplate", "tassets", "shield", "boots",
                "gloves", "body", "legs", "hat", "robe", "cape", "coif", "chaps", "vambraces", "top",
                "bottom", "defender", "hood", "mask", "chainbody", "skirt", "cuisse", "hauberk", "greaves",
                "gauntlets", "bracers", "armour", "tiara", "crown")


def category(name):
    """Portfolio category: the market index groups, then gear, then everything else."""
    cat = categories.classify(name)
    if cat:
        return categories.LABELS[cat]
    n = name.lower()
    if n == "old school bond":
        return "Bonds"
    if " set" in n and ("armour" in n or n.endswith(" set")):
        return "Armour"
    words = n.replace("'", "").replace("(", " ").split()
    if any(w in categories.JEWELLERY_WORDS for w in words) or n.startswith("amulet") or n.endswith(" ring"):
        return "Jewellery"
    if any(w.endswith(WEAPON_WORDS) for w in words):
        return "Weapons"
    if any(w.endswith(ARMOUR_WORDS) for w in words):
        return "Armour"
    return "Other items"


# Holdings -------------------------------------------------------------------------------

def _add(bag, iid, qty):
    if iid and qty:
        bag[iid] = bag.get(iid, 0) + qty


def ge_escrow(offers, boxes):
    """What sits in the Grand Exchange for one account, by slot.

    Active buy offers reserve coins for the part not yet bought; active sell offers hold the
    unsold items. What has already traded (and refunds from cancelled or cheaper fills) waits
    in the slot's collection box. When the box was seen after the offer's latest change it is
    used as is; otherwise its contents are estimated from the offer.
    """
    out = {}
    estimated = False
    for o in offers:
        bag = {}
        state, price, total, done, spent = o["state"], o["price"] or 0, o["total"] or 0, o["done"] or 0, o["spent"] or 0
        if state == "EMPTY":
            continue
        if state == "BUYING":
            _add(bag, COINS, price * (total - done))
        elif state == "SELLING":
            _add(bag, o["item"], total - done)
        box = boxes.get(o["slot"])
        if box is not None and box["t"] >= o["t"]:
            for iid, qty in box["items"]:
                _add(bag, iid, qty)
        else:
            estimated = estimated or state not in ("BUYING", "SELLING") or done > 0
            if state in ("BUYING", "BOUGHT", "CANCELLED_BUY"):
                _add(bag, o["item"], done)
                _add(bag, COINS, max(0, price * done - spent))  # refund when bought cheaper
                if state == "CANCELLED_BUY":
                    _add(bag, COINS, price * (total - done))
            else:
                _add(bag, COINS, spent)
                if state == "CANCELLED_SELL":
                    _add(bag, o["item"], total - done)
        out[o["slot"]] = bag
    return out, estimated


def raw_holdings(db, acct):
    """{container: {item: qty}} and when each container was last seen."""
    rows = db.q("SELECT * FROM holdings_live WHERE acct=?", (acct,))
    containers, seen, boxes = {}, {}, {}
    for r in rows:
        items = json.loads(r["items"])
        name = r["container"]
        if name.startswith("ge_collect_"):
            boxes[int(name.rsplit("_", 1)[1])] = {"t": r["t"], "items": items}
            continue
        bag = {}
        for iid, qty in items:
            _add(bag, iid, qty)
        containers[name] = bag
        seen[name] = r["t"]
    offers = db.q("SELECT * FROM ge_offers WHERE acct=?", (acct,))
    slots, estimated = ge_escrow(offers, boxes)
    ge = {}
    for bag in slots.values():
        for iid, qty in bag.items():
            _add(ge, iid, qty)
    containers["ge"] = ge
    if offers:
        seen["ge"] = max(o["t"] for o in offers)
    return containers, seen, estimated


# Valuation ------------------------------------------------------------------------------

class Valuer:
    def __init__(self, engine, mode="sell"):
        self.by_id = engine.by_id
        self.mapping = engine.mapping
        self.tax = engine.tax
        self.mode = mode

    def each(self, iid):
        """(value per item, gross per item, how it was priced)."""
        if iid == COINS:
            return 1, 1, "cash"
        if iid == PLATINUM:
            return 1000, 1000, "cash"
        m = self.mapping.get(iid)
        if m is None:
            return 0, 0, "untradeable"
        r = self.by_id.get(iid) or {}
        hi, lo = r.get("high"), r.get("low")
        if self.mode == "market" and hi and lo:
            mid = (hi + lo) / 2.0
            return mid, mid, "market"
        p = hi or lo
        if p:
            return p - self.tax(p, iid), p, "sell"
        ha = m.get("highalch")
        if ha:
            return ha, ha, "alch (no GE price)"
        return 0, 0, "no price"


def _basis(db, acct, engine):
    """Average cost of the units still held, per item (first in, first out).

    Units come from real GE buys, plus starting costs: items first seen with no known cost
    get the market value on the day they were first seen (see seed_costs).
    """
    fills = list(db.q("SELECT * FROM ge_fills WHERE acct=?", (acct,)))
    for sd in db.q("SELECT * FROM cost_seed WHERE acct=?", (acct,)):
        fills.append({"fid": -1, "acct": acct, "item": sd["item"], "side": "buy", "qty": sd["qty"],
                      "gp": sd["each"] * sd["qty"], "t": sd["t"], "offer_price": None, "seed": True})
    _, lots = account.match_flips(fills, engine.tax)
    out = {}
    for lot in lots:
        b = out.setdefault(lot["item"], {"qty": 0, "cost": 0.0, "seedQty": 0, "seedT": None})
        b["qty"] += lot["qty"]
        b["cost"] += lot["qty"] * lot["each"]
        if lot.get("seed"):
            b["seedQty"] += lot["qty"]
            b["seedT"] = lot["t"]
    return out


def seed_costs(db, engine, acct, containers, now=None):
    """Give held items with no known cost a starting cost: today's value, once per item.

    Only the units not already covered by GE buys are seeded, so profit or loss on older
    items counts from the day tracking began. Returns how many items were seeded.
    """
    now = int(now or time.time())
    valuer = Valuer(engine, "sell")
    held = {}
    for cname, bag in containers.items():
        if cname == "manual":
            continue
        for iid, qty in bag.items():
            _add(held, iid, qty)
    basis = _basis(db, acct, engine)
    # Each item is seeded once, except that the first sight of a storage (usually the bank,
    # opened after the inventory was already seen) tops up what it adds.
    key = "seeded_containers:" + acct
    before = set(db.kv_get(key, []) or [])
    now_seen = {c for c in containers if c != "manual" and containers[c]}
    new_storage = bool(now_seen - before)
    done = set() if new_storage else {r["item"] for r in db.q("SELECT item FROM cost_seed WHERE acct=?", (acct,))}
    rows = []
    for iid, qty in held.items():
        if iid in (COINS, PLATINUM) or iid in done:
            continue
        uncovered = qty - basis.get(iid, {}).get("qty", 0)
        each, _, how = valuer.each(iid)
        if uncovered > 0 and how == "sell" and each > 0:
            rows.append((acct, iid, now, uncovered, float(each)))
    if rows:
        db.many("INSERT OR IGNORE INTO cost_seed (acct, item, t, qty, each) VALUES (?,?,?,?,?)", rows)
    if new_storage:
        db.kv_set(key, sorted(before | now_seen))
    return len(rows)


def account_view(db, engine, acct, cfg, include_manual=False):
    """Full valuation of one account: totals, holdings, containers, allocation, coverage."""
    valuer = Valuer(engine, cfg.get("networth_value", "sell"))
    containers, seen, estimated = raw_holdings(db, acct) if acct else ({}, {}, False)
    if include_manual:
        manual = {}
        for h in db.q("SELECT item_id, qty FROM holdings"):
            _add(manual, h["item_id"], h["qty"])
        coins = int(db.kv_get("coins", 0) or 0)
        _add(manual, COINS, coins)
        if manual:
            containers["manual"] = manual
    basis = _basis(db, acct, engine) if acct else {}
    items = {}
    by_container = []
    for cname, bag in containers.items():
        cval = 0.0
        for iid, qty in bag.items():
            v, g, how = valuer.each(iid)
            it = items.setdefault(iid, {"id": iid, "qty": 0, "where": {}, "how": how, "each": v, "gross": g})
            it["qty"] += qty
            it["where"][cname] = it["where"].get(cname, 0) + qty
            cval += v * qty
        by_container.append({"key": cname, "label": CONTAINER_LABELS.get(cname, cname), "value": cval,
                             "items": len(bag), "seen": seen.get(cname)})
    holdings, cash, untradeable = [], 0.0, []
    total = 0.0
    for iid, it in items.items():
        m = engine.mapping.get(iid, {})
        live = engine.by_id.get(iid) or {}
        value = it["each"] * it["qty"]
        row = {"id": iid, "name": m.get("name") or ("Coins" if iid == COINS else f"Item {iid}"),
               "icon": m.get("icon"), "qty": it["qty"], "each": it["each"], "gross": it["gross"],
               "value": value, "how": it["how"], "where": it["where"],
               "chg24h": live.get("chg24h"), "chg7d": live.get("chg7d"), "members": m.get("members"),
               "limit": m.get("limit"), "highalch": m.get("highalch")}
        c24 = live.get("chg24h")
        row["chg24gp"] = (value * c24 / (1 + c24)) if (c24 is not None and c24 > -1 and it["how"] != "cash") else 0.0
        b = basis.get(iid)
        if b and b["qty"] > 0:
            q = min(b["qty"], it["qty"])
            avg = b["cost"] / b["qty"]
            row["costEach"] = avg
            row["basisQty"] = q
            row["basisCost"] = avg * q
            row["pnl"] = (it["each"] - avg) * q
            row["costFrom"] = ("first seen" if b["seedQty"] >= b["qty"] else
                               "trades" if not b["seedQty"] else "mixed")
            row["seededAt"] = b["seedT"]
        if it["how"] == "cash":
            cash += value
            row["category"] = "Cash"
        elif it["how"] == "untradeable":
            untradeable.append(row)
            continue
        else:
            row["category"] = category(row["name"])
        total += value
        holdings.append(row)
    holdings.sort(key=lambda r: -r["value"])
    for r in holdings:
        r["share"] = (r["value"] / total) if total else 0
    alloc = {}
    for r in holdings:
        a = alloc.setdefault(r["category"], {"category": r["category"], "value": 0.0, "items": 0})
        a["value"] += r["value"]
        a["items"] += 1
    allocation = sorted(alloc.values(), key=lambda a: -a["value"])
    for a in allocation:
        a["share"] = (a["value"] / total) if total else 0
    by_container.sort(key=lambda c: -c["value"])
    now = time.time()
    coverage = []
    for key in EXPECTED + [k for k in seen if k not in EXPECTED]:
        t = seen.get(key)
        coverage.append({"key": key, "label": CONTAINER_LABELS.get(key, key), "seen": t,
                         "age": (now - t) if t else None, "ok": t is not None})
    ge_value = sum(c["value"] for c in by_container if c["key"] == "ge")
    return {
        "acct": acct, "total": total, "cash": cash, "items": total - cash, "ge": ge_value,
        "holdings": holdings, "containers": by_container, "allocation": allocation,
        "untradeable": sorted(untradeable, key=lambda r: -r["qty"]),
        "coverage": coverage, "geEstimated": estimated,
        "chg24market": sum(r["chg24gp"] for r in holdings),
        "pnl": sum(r.get("pnl") or 0 for r in holdings),
        "costSince": min([r["seededAt"] for r in holdings if r.get("seededAt")] or [None]) if any(
            r.get("seededAt") for r in holdings) else None,
        "costCovered": sum(r["value"] for r in holdings if r.get("basisQty")),
        "mode": valuer.mode,
    }


def combined_view(db, engine, cfg):
    """All accounts together (plus manual additions)."""
    views = [account_view(db, engine, a["acct"], cfg) for a in account.accounts(db)]
    manual = account_view(db, engine, None, cfg, include_manual=cfg.get("networth_include_manual", True))
    parts = views + ([manual] if manual["holdings"] else [])
    merged = {}
    for v in parts:
        for h in v["holdings"]:
            m = merged.get(h["id"])
            if not m:
                merged[h["id"]] = dict(h, where=dict(h["where"]))
                continue
            m["qty"] += h["qty"]
            m["value"] += h["value"]
            m["chg24gp"] += h["chg24gp"]
            if h.get("pnl") is not None:
                m["pnl"] = (m.get("pnl") or 0) + h["pnl"]
                m["basisQty"] = (m.get("basisQty") or 0) + h["basisQty"]
                m["basisCost"] = (m.get("basisCost") or 0) + h["basisCost"]
                m["costEach"] = m["basisCost"] / m["basisQty"] if m["basisQty"] else None
                if m.get("costFrom") != h["costFrom"]:
                    m["costFrom"] = "mixed" if m.get("costFrom") else h["costFrom"]
                m["seededAt"] = min(x for x in (m.get("seededAt"), h.get("seededAt")) if x) if (
                    m.get("seededAt") or h.get("seededAt")) else None
            for k, q in h["where"].items():
                m["where"][k] = m["where"].get(k, 0) + q
    holdings = sorted(merged.values(), key=lambda r: -r["value"])
    total = sum(h["value"] for h in holdings)
    for h in holdings:
        h["share"] = (h["value"] / total) if total else 0
    alloc = {}
    for h in holdings:
        a = alloc.setdefault(h["category"], {"category": h["category"], "value": 0.0, "items": 0})
        a["value"] += h["value"]
        a["items"] += 1
    containers = {}
    for v in parts:
        for c in v["containers"]:
            x = containers.setdefault(c["key"], dict(c, value=0.0, items=0))
            x["value"] += c["value"]
            x["items"] += c["items"]
    return {
        "acct": None, "total": total, "cash": sum(v["cash"] for v in parts),
        "items": total - sum(v["cash"] for v in parts), "ge": sum(v["ge"] for v in parts),
        "holdings": holdings, "allocation": sorted([dict(a, share=a["value"] / total if total else 0)
                                                    for a in alloc.values()], key=lambda a: -a["value"]),
        "containers": sorted(containers.values(), key=lambda c: -c["value"]),
        "untradeable": [u for v in parts for u in v["untradeable"]],
        "coverage": [dict(c, acctName=next((a["name"] for a in account.accounts(db) if a["acct"] == v["acct"]), ""))
                     for v in views for c in v["coverage"]],
        "geEstimated": any(v["geEstimated"] for v in parts),
        "chg24market": sum(v["chg24market"] for v in parts), "pnl": sum(v["pnl"] for v in parts),
        "costSince": min([v["costSince"] for v in parts if v.get("costSince")] or [None]),
        "costCovered": sum(v.get("costCovered") or 0 for v in parts),
        "mode": cfg.get("networth_value", "sell"), "accounts": len(views),
        "manualIncluded": bool(manual["holdings"]),
    }


# History --------------------------------------------------------------------------------

def attribute(db, engine, cfg, key, view, now):
    """Split the change since the last recording into where it came from.

      mkt    price moves on what was held at the last recording
      trade  GE trades: the gap between what you paid or received and the item's value
      loot   drops from the Loot Tracker, at today's value
      other  everything else: skilling, alching, spending, eating, player trades, deaths

    Returns cumulative totals since tracking began, plus a time weighted return index
    (twr) that only moves with mkt and trade, so adding loot or cash does not count as
    performance. The snapshot needed for the next step is kept in the kv table.
    """
    valuer = Valuer(engine, cfg.get("networth_value", "sell"))
    snap_key = "worth_snap:" + key
    snap = db.kv_get(snap_key)
    last = db.one("SELECT mkt, trade, loot, other, twr FROM account_worth WHERE acct=? AND ts<=? "
                  "ORDER BY ts DESC LIMIT 1", (key, now))
    cum = {k: (last[k] if last and last[k] is not None else 0) for k in ("mkt", "trade", "loot", "other")}
    twr = last["twr"] if last and last["twr"] else 1.0
    items_now = {str(h["id"]): [h["qty"], h["each"]] for h in view["holdings"]}
    if snap and snap.get("ts", 0) < now:
        t0 = snap["ts"]
        mkt = 0.0
        for iid, (qty, each0) in snap["items"].items():
            each1 = items_now[iid][1] if iid in items_now else valuer.each(int(iid))[0]
            mkt += qty * (each1 - each0)
        acct_clause, args = ("", [t0, now]) if key == "*" else (" AND acct=?", [t0, now, key])
        trade = 0.0
        for f in db.q("SELECT * FROM ge_fills WHERE t>? AND t<=?" + acct_clause, args):
            each = valuer.each(f["item"])[0]
            if f["side"] == "buy":
                trade += f["qty"] * each - f["gp"]
            else:
                _, _, net = account.sell_split(f["gp"], f["qty"], f["offer_price"] or 0, engine.tax, f["item"])
                trade += net - f["qty"] * each
        loot = 0.0
        for row in db.q("SELECT items FROM loot WHERE t>? AND t<=?" + acct_clause, args):
            for iid, qty in json.loads(row["items"]):
                loot += qty * valuer.each(iid)[0]
        other = view["total"] - snap["total"] - mkt - trade - loot
        if snap["total"] > 0:
            twr *= 1 + (mkt + trade) / snap["total"]
        cum["mkt"] += mkt
        cum["trade"] += trade
        cum["loot"] += loot
        cum["other"] += other
    db.kv_set(snap_key, {"ts": now, "total": view["total"], "items": items_now})
    return {k: int(round(v)) for k, v in cum.items()}, twr


def record(db, engine, cfg, now=None, bucket=300):
    """Save each account's and the combined net worth (at most one point per 5 minutes)."""
    now = int(now or time.time())
    ts = now // bucket * bucket
    rows = []

    def add(key, v):
        cum, twr = attribute(db, engine, cfg, key, v, now)
        rows.append((key, ts, int(v["total"]), int(v["cash"]), int(v["items"]), int(v["ge"]),
                     cum["mkt"], cum["trade"], cum["loot"], cum["other"], twr))

    for a in account.accounts(db):
        seed_costs(db, engine, a["acct"], raw_holdings(db, a["acct"])[0], now)
        v = account_view(db, engine, a["acct"], cfg)
        if v["holdings"]:
            add(a["acct"], v)
    c = combined_view(db, engine, cfg)
    if c["holdings"]:
        add("*", c)
    if rows:
        db.many("INSERT OR REPLACE INTO account_worth (acct, ts, total, cash, items, ge, mkt, trade, loot, other, "
                "twr) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def performance(db, acct, days=None, now=None):
    """Attribution and time weighted return over a period, from the recorded history."""
    key = acct or "*"
    now = now or time.time()
    since = int(now - days * 86400) if days else 0
    rows = db.q("SELECT ts, total, mkt, trade, loot, other, twr FROM account_worth WHERE acct=? AND ts>=? "
                "ORDER BY ts", (key, since))
    if days:
        before = db.one("SELECT ts, total, mkt, trade, loot, other, twr FROM account_worth WHERE acct=? AND ts<? "
                        "ORDER BY ts DESC LIMIT 1", (key, since))
        if before:
            rows = [before] + rows
    if len(rows) < 2:
        return {"ok": False, "points": len(rows)}
    a, b = rows[0], rows[-1]
    g = lambda r, k: r[k] or 0  # noqa: E731
    t0 = a["twr"] or 1.0
    series = [{"t": r["ts"], "v": (r["twr"] or 1.0) / t0 - 1} for r in rows]
    parts = {k: g(b, k) - g(a, k) for k in ("mkt", "trade", "loot", "other")}
    return {"ok": True, "from": a["ts"], "to": b["ts"], "start": a["total"], "end": b["total"],
            "change": b["total"] - a["total"], "parts": parts,
            "returnPct": (b["twr"] or 1.0) / t0 - 1, "series": series}


def seed_demo(db, engine, cfg, days=14):
    """Demo mode only: fill recorded history from the backcast so the chart has a line."""
    if db.one("SELECT 1 AS x FROM account_worth WHERE ts < ? LIMIT 1", (int(time.time() - 2 * 86400),)):
        return 1
    rows = []
    for key, view in [("*", combined_view(db, engine, cfg))] + [
            (a["acct"], account_view(db, engine, a["acct"], cfg)) for a in account.accounts(db)]:
        pts = [p for p in backcast(db, engine, view, days + 1) if p["ts"] < time.time() - 3600]
        if key == "*" and len(pts) < days - 2:
            return 0  # price history still loading
        for i, p in enumerate(pts):
            # Cash grows a little each day, as it would from trading.
            extra = int(view["cash"] * 0.004 * (i - len(pts)))
            total = p["total"] + extra
            if i == 0:
                cum, twr, prev = {"mkt": 0, "trade": 0}, 1.0, total
            else:
                step_trade = int(view["cash"] * 0.004)
                step_mkt = total - prev - step_trade
                twr *= 1 + (step_mkt + step_trade) / prev
                cum["mkt"] += step_mkt
                cum["trade"] += step_trade
                prev = total
            rows.append((key, p["ts"], total, int(view["cash"]) + extra, p["total"] - int(view["cash"]), 0,
                         cum["mkt"], cum["trade"], 0, 0, twr))
    if rows:
        db.many("INSERT OR REPLACE INTO account_worth (acct, ts, total, cash, items, ge, mkt, trade, loot, other, twr) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def history(db, acct, days=None):
    key = acct or "*"
    since = int(time.time() - days * 86400) if days else 0
    return db.q("SELECT ts, total, cash, items, ge FROM account_worth WHERE acct=? AND ts>=? ORDER BY ts",
                (key, since))


def changes(db, acct, total_now):
    """Change in net worth over 24 hours, 7 days, 30 days and since tracking began."""
    key = acct or "*"
    out = {}
    for label, secs in (("d1", 86400), ("d7", 7 * 86400), ("d30", 30 * 86400)):
        r = db.one("SELECT total, ts FROM account_worth WHERE acct=? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                   (key, int(time.time() - secs)))
        if r and r["total"]:
            out[label] = {"gp": total_now - r["total"], "pct": total_now / r["total"] - 1, "from": r["ts"]}
    first = db.one("SELECT total, ts FROM account_worth WHERE acct=? ORDER BY ts LIMIT 1", (key,))
    if first and first["total"]:
        out["all"] = {"gp": total_now - first["total"], "pct": total_now / first["total"] - 1, "from": first["ts"]}
    return out


def _daily_prices(db, engine, ids, days):
    """{item: [(day ts, price)]} from saved hourly history, with imported daily history before it."""
    from . import forecast
    now = time.time()
    since = int(now - days * 86400)
    hist_d = db.history_many(ids, since, table="d1")
    # Hourly rows are only needed after the imported daily history ends (usually a day or
    # two); reading a year of hourly rows for hundreds of items would be slow.
    last_d1 = (db.one("SELECT MAX(ts) AS t FROM d1") or {}).get("t")
    h_since = max(since, int(last_d1) - 86400) if last_d1 and last_d1 > since else since
    hist_h = db.history_many(ids, h_since)
    out = {}
    for iid in ids:
        hourly = forecast.daily_series(hist_h.get(iid, []), engine.tax, iid)
        first = hourly[0]["t"] if hourly else now
        series = [d for d in forecast.daily_from_d1(hist_d.get(iid, []), engine.tax, iid) if d["t"] < first] + hourly
        pts = [(int(d["t"]) // 86400 * 86400, d["price"]) for d in series if d.get("price")]
        if pts:
            out[iid] = pts
    return out


def backcast(db, engine, view, days=90, max_items=250):
    """What today's holdings would have been worth on each past day.

    This is not your real history (you held different things then); it shows how the
    items you hold now have moved, so the chart has context before tracking began. Each
    item's path is scaled so the last day matches its value today.
    """
    items = [h for h in view["holdings"] if h["how"] not in ("cash", "untradeable") and h["value"] > 0]
    items = items[:max_items]
    series = _daily_prices(db, engine, [h["id"] for h in items], days)
    if not series:
        return []
    day_set = sorted({t for pts in series.values() for t, _ in pts})
    total_by_day = {t: float(view["cash"]) for t in day_set}
    for h in items:
        pts = series.get(h["id"])
        if not pts:
            for t in day_set:
                total_by_day[t] += h["value"]
            continue
        last = pts[-1][1]
        by_t = dict(pts)
        price = None
        for t in day_set:
            price = by_t.get(t, price)
            p = price if price is not None else pts[0][1]
            total_by_day[t] += h["value"] * p / last
    return [{"ts": t, "total": int(v)} for t, v in sorted(total_by_day.items())]


def item_records(db, engine, ids, horizon=90):
    """Each item's own record over past `horizon` day periods (share that rose, median change)."""
    from . import hold
    series = _daily_prices(db, engine, ids, 800)
    out = {}
    for iid, pts in series.items():
        if len(pts) < horizon + 42:
            continue
        _, prices, _ = hold.daily_grid([{"t": t, "price": p, "hv": 0, "lv": 0} for t, p in pts])
        rec = hold.own_history(prices, horizon)
        if rec:
            out[iid] = rec
    return out
