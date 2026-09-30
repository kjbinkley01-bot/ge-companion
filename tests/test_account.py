"""Tests for the live account pipeline: plugin files, fills, flips, net worth, advice."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import account, advice, config, market, networth  # noqa: E402
from geco.db import DB  # noqa: E402

WHIP, SHARK, TBOW = 4151, 385, 20997
MAPPING = {
    WHIP: {"id": WHIP, "name": "Abyssal whip", "limit": 70, "highalch": 72000, "members": True},
    SHARK: {"id": SHARK, "name": "Shark", "limit": 13000, "highalch": 180, "members": True},
    TBOW: {"id": TBOW, "name": "Twisted bow", "limit": 8, "highalch": 720000, "members": True},
}
ACCT = "rsprofile--1"


class FakeEngine:
    def __init__(self, prices):
        self.mapping = MAPPING
        self.tax = market.Tax(config.DEFAULTS, MAPPING)
        self.rows = [{"id": i, "name": MAPPING[i]["name"], "high": h, "low": lo} for i, (h, lo) in prices.items()]
        self.by_id = {r["id"]: r for r in self.rows}


def ev(t, kind, **f):
    e = {"v": 1, "t": int(t * 1000), "type": kind, "acct": ACCT, "name": "Zezima"}
    e.update(f)
    return e


def offer(t, slot, state, item, price, total, done, spent):
    return ev(t, "offer", slot=slot, state=state, item=item, price=price, total=total, done=done, spent=spent)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.dir.name, "t.sqlite3"))
        account.init(self.db)
        networth.init(self.db)
        self.folder = os.path.join(self.dir.name, "events")
        os.makedirs(self.folder)

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()

    def write(self, events, name="events-2026-09.jsonl"):
        with open(os.path.join(self.folder, name), "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")


class FillTests(unittest.TestCase):
    def test_same_offer_gives_the_difference(self):
        prev = {"state": "BUYING", "item": 1, "price": 10, "total": 100, "done": 20, "spent": 200}
        cur = dict(prev, done=50, spent=480)
        self.assertEqual(account.fill_from(prev, cur), ("buy", 30, 280, False))

    def test_changed_offer_counts_as_new(self):
        prev = {"state": "BOUGHT", "item": 1, "price": 10, "total": 100, "done": 100, "spent": 1000}
        cur = {"state": "SELLING", "item": 1, "price": 12, "total": 100, "done": 5, "spent": 60}
        self.assertEqual(account.fill_from(prev, cur), ("sell", 5, 60, True))
        # Done going backwards means a fresh offer with the same numbers.
        again = {"state": "BUYING", "item": 1, "price": 10, "total": 100, "done": 3, "spent": 30}
        self.assertEqual(account.fill_from(dict(prev, state="BUYING"), again)[3], True)

    def test_empty_slot_is_no_fill(self):
        self.assertEqual(account.fill_from(None, {"state": "EMPTY", "item": 0, "price": 0, "total": 0,
                                                   "done": 0, "spent": 0})[1], 0)

    def test_sell_split_gross_and_net(self):
        tax = market.Tax(config.DEFAULTS, MAPPING)
        gross, t, net = account.sell_split(1000 * 10, 10, 1000, tax, WHIP)
        self.assertEqual((gross, t, net), (10000, 200, 9800))
        # Reported below offer price times quantity: already after tax.
        gross, t, net = account.sell_split(9800, 10, 1000, tax, WHIP)
        self.assertEqual(net, 9800)
        self.assertEqual(gross, 10000)


class IngestTests(Base):
    def test_offsets_skip_partial_lines_and_old_events(self):
        now = time.time()
        path = os.path.join(self.folder, "events-2026-09.jsonl")
        self.write([offer(now - 100, 0, "BUYING", SHARK, 1000, 100, 0, 0)])
        line = json.dumps(offer(now - 50, 0, "BUYING", SHARK, 1000, 100, 40, 39000)) + "\n"
        with open(path, "a") as f:
            f.write(line[:15])  # the plugin is still writing this line
        self.assertEqual(account.ingest(self.db, self.folder), 1)
        with open(path, "a") as f:
            f.write(line[15:])
        self.assertEqual(account.ingest(self.db, self.folder), 1)
        self.assertEqual(account.ingest(self.db, self.folder), 0)
        fills = self.db.q("SELECT * FROM ge_fills")
        self.assertEqual([(f["qty"], f["gp"]) for f in fills], [(40, 39000)])

    def test_flips_from_trades_and_ignore(self):
        now = time.time()
        self.write([
            offer(now - 900, 0, "BUYING", WHIP, 800000, 2, 0, 0),
            offer(now - 800, 0, "BOUGHT", WHIP, 800000, 2, 2, 1590000),
            offer(now - 700, 0, "EMPTY", 0, 0, 0, 0, 0),
            offer(now - 600, 1, "SELLING", WHIP, 850000, 1, 0, 0),
            offer(now - 500, 1, "SOLD", WHIP, 850000, 1, 1, 850000),
        ])
        account.ingest(self.db, self.folder)
        tax = market.Tax(config.DEFAULTS, MAPPING)
        closed, open_lots = account.sync_flips(self.db, tax)
        self.assertEqual((closed, open_lots), (1, 1))
        rows = self.db.q("SELECT * FROM flips WHERE source='auto' ORDER BY fid")
        self.assertEqual(rows[0]["buy_price"], 795000)
        self.assertEqual(rows[0]["sell_price"], 850000)
        self.assertIsNone(rows[1]["sell_price"])
        self.db.run("INSERT INTO flip_ignore (acct, item) VALUES (?,?)", (ACCT, WHIP))
        account.sync_flips(self.db, tax)
        self.assertEqual(self.db.q("SELECT * FROM flips WHERE source='auto'"), [])

    def test_sells_without_a_buy_are_not_flips(self):
        tax = market.Tax(config.DEFAULTS, MAPPING)
        fills = [{"fid": 1, "acct": ACCT, "item": WHIP, "side": "sell", "qty": 1, "gp": 800000,
                  "offer_price": 800000, "t": 1}]
        closed, lots = account.match_flips(fills, tax)
        self.assertEqual((closed, lots), ([], []))


class NetWorthTests(Base):
    def test_escrow_buy_and_sell(self):
        offers = [
            {"slot": 0, "t": 10, "state": "BUYING", "item": SHARK, "price": 1000, "total": 100, "done": 30, "spent": 29000},
            {"slot": 1, "t": 10, "state": "SELLING", "item": WHIP, "price": 900000, "total": 2, "done": 1, "spent": 900000},
        ]
        slots, est = networth.ge_escrow(offers, {})
        self.assertTrue(est)
        self.assertEqual(slots[0], {995: 70000 + 1000, SHARK: 30})  # reserved coins, refund, bought items
        self.assertEqual(slots[1], {WHIP: 1, 995: 900000})
        # A collection box seen after the latest change is used as is.
        slots, est = networth.ge_escrow(offers[:1], {0: {"t": 11, "items": [[SHARK, 30]]}})
        self.assertFalse(est)
        self.assertEqual(slots[0], {995: 70000, SHARK: 30})

    def test_account_view_values_everything(self):
        now = time.time()
        self.write([
            ev(now - 60, "container", container="bank", items=[[995, 1000000], [TBOW, 1], [13204, 5]]),
            ev(now - 60, "container", container="inventory", items=[[SHARK, 100], [99999, 1]]),
            offer(now - 30, 0, "BUYING", SHARK, 1000, 100, 0, 0),
        ])
        account.ingest(self.db, self.folder)
        eng = FakeEngine({TBOW: (1_000_000_000, 990_000_000), SHARK: (1000, 990)})
        v = networth.account_view(self.db, eng, ACCT, {"networth_value": "sell"})
        cash = 1_000_000 + 5000 + 100 * 1000  # coins, platinum tokens, coins reserved in the buy offer
        tbow = 1_000_000_000 - 5_000_000  # tax capped at 5m
        shark = 100 * (1000 - 20)
        self.assertEqual(v["cash"], cash)
        self.assertAlmostEqual(v["total"], cash + tbow + shark)
        self.assertEqual([u["id"] for u in v["untradeable"]], [99999])
        self.assertEqual(v["holdings"][0]["category"], "Weapons")
        networth.record(self.db, eng, {"networth_value": "sell"})
        self.assertEqual(len(networth.history(self.db, ACCT)), 1)
        self.assertEqual(len(networth.history(self.db, None)), 1)

    def test_starting_cost_for_items_already_held(self):
        now = time.time()
        cfg = {"networth_value": "sell"}
        self.write([ev(now - 600, "container", container="bank", items=[[995, 5000], [SHARK, 100], [TBOW, 1]])])
        account.ingest(self.db, self.folder)
        eng = FakeEngine({TBOW: (1_000_000_000, 990_000_000), SHARK: (1000, 990)})
        networth.record(self.db, eng, cfg)
        v = networth.account_view(self.db, eng, ACCT, cfg)
        shark = next(h for h in v["holdings"] if h["id"] == SHARK)
        self.assertEqual((shark["costEach"], shark["pnl"], shark["costFrom"]), (980, 0, "first seen"))
        self.assertIsNotNone(v["costSince"])
        # Prices move: profit or loss counts from the starting cost.
        eng2 = FakeEngine({TBOW: (1_100_000_000, 1_090_000_000), SHARK: (1100, 1090)})
        v = networth.account_view(self.db, eng2, ACCT, cfg)
        self.assertEqual(next(h for h in v["holdings"] if h["id"] == SHARK)["pnl"], 100 * (1078 - 980))
        self.assertEqual(next(h for h in v["holdings"] if h["id"] == TBOW)["pnl"], 100_000_000)
        # A later GE buy is added at what was paid, and the starting cost is not seeded again.
        self.write([offer(now - 60, 0, "BUYING", SHARK, 900, 100, 0, 0),
                    offer(now - 30, 0, "BOUGHT", SHARK, 900, 100, 100, 90000),
                    ev(now - 20, "container", container="bank", items=[[995, 5000], [SHARK, 200], [TBOW, 1]]),
                    ev(now - 20, "container", container="ge_collect_0", items=[])])
        account.ingest(self.db, self.folder)
        networth.record(self.db, eng2, cfg)
        v = networth.account_view(self.db, eng2, ACCT, cfg)
        shark = next(h for h in v["holdings"] if h["id"] == SHARK)
        self.assertEqual(shark["costEach"], (980 * 100 + 900 * 100) / 200)
        self.assertEqual(shark["costFrom"], "mixed")
        self.assertEqual(len(self.db.q("SELECT * FROM cost_seed")), 2)
        # More loot later is not given a new starting cost...
        self.write([ev(now - 10, "container", container="bank", items=[[995, 5000], [SHARK, 250], [TBOW, 1]])])
        account.ingest(self.db, self.folder)
        networth.record(self.db, eng2, cfg)
        self.assertEqual(len(self.db.q("SELECT * FROM cost_seed")), 2)
        # ...but the first sight of another storage tops up what it adds.
        self.write([ev(now - 5, "container", container="seed_vault", items=[[TBOW, 1]])])
        account.ingest(self.db, self.folder)
        networth.record(self.db, eng2, cfg, now=now + 1)
        self.assertEqual(self.db.q("SELECT qty FROM cost_seed WHERE item=? ORDER BY t", (TBOW,))[-1]["qty"], 1)
        self.assertEqual(len(self.db.q("SELECT * FROM cost_seed WHERE item=?", (SHARK,))), 2)
        # Selling on the GE uses up the oldest units first, and no flip is made from a starting cost.
        self.assertEqual(account.match_flips(self.db.q("SELECT * FROM ge_fills"), eng.tax)[0], [])

    def test_categories(self):
        self.assertEqual(networth.category("Bandos chestplate"), "Armour")
        self.assertEqual(networth.category("Dharok's greataxe"), "Weapons")
        self.assertEqual(networth.category("Berserker ring"), "Jewellery")
        self.assertEqual(networth.category("Nature rune"), "Runes")


class AdviceTests(unittest.TestCase):
    def test_plan_flips_respects_cash_and_limits(self):
        rows = [{"id": 1, "name": "A", "profit": 100, "stability": 1.0, "vol24": 5000, "low": 1000, "high": 1200,
                 "estQty": 50},
                {"id": 2, "name": "B", "profit": 10, "stability": 1.0, "vol24": 5000, "low": 100, "high": 120,
                 "estQty": 1000, "trap": True}]
        picks = advice.plan_flips(rows, 20000, 8, {1: {"left": 10}})
        self.assertEqual([(p["id"], p["qty"]) for p in picks], [(1, 10)])

    def test_offer_notes_and_concentration(self):
        eng = FakeEngine({TBOW: (1_000_000_000, 990_000_000)})
        eng.pricer = lambda: type("P", (), {"iid": staticmethod(lambda n: None)})()
        view = {"total": 1e9, "cash": 0, "holdings": [{"id": TBOW, "name": "Twisted bow", "qty": 1, "each": 1e9,
                                                       "value": 1e9, "how": "sell", "share": 1.0, "highalch": 0}],
                "coverage": [{"key": "bank", "ok": False, "age": None}], "geEstimated": False}
        slots = {ACCT: [{"state": "BUYING", "note": "Below the market by 5.0%: may not fill", "name": "Shark",
                         "side": "buy", "price": 900, "item": SHARK, "market_low": 950, "market_high": 1000}]}
        import geco.advice as adv
        orig = adv.recipes.money_making
        adv.recipes.money_making = lambda pricer: []
        try:
            recs = advice.recommend(view, eng, slots)
        finally:
            adv.recipes.money_making = orig
        kinds = [r["kind"] for r in recs]
        self.assertEqual(kinds[0], "coverage")
        self.assertIn("offer", kinds)
        self.assertIn("concentration", kinds)


if __name__ == "__main__":
    unittest.main()
