"""Tests for gear and inventory setups: Wiki parsing, fitting to the bank, Inventory Setups export."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import account, config, market, networth, quests, setups  # noqa: E402
from geco.db import DB  # noqa: E402

PAGE = """==Equipment==
<tabber>
Ranged=
{{Recommended equipment
|style = Ranged (crossbow)
|head1 = {{plink|Masori mask (f)}} / {{plink|Void ranger helm}}{{efn|With full void.}}
|head2 = {{plink|Karil's coif}}
|cape1 = {{plink|Ava's assembler}}
|body1 = {{plink|Masori body (f)}}
|body2 = {{plink|Black d'hide body}}
|legs1 = {{plink|Black d'hide chaps}}
|weapon1 = {{plink|Dragon hunter crossbow}}
|special1 = {{plink|Toxic blowpipe}}
}}
|-|
Melee=
{{Recommended equipment
|style = Melee
|weapon1 = {{plink|Dragon hunter lance}}
}}
</tabber>
==Inventory setups==
<tabber>
Ranged (crossbow)=
{{Equipment
|head = Karil's coif
|cape = Ava's assembler
}}
{{Inventory
|1 = Diamond dragon bolts (e)\\500
|2 = {{Cheap food}}
|3 = Shark
}}
{{Rune pouch
|1=Law rune
|2=Dust rune
}}
</tabber>
"""

INFOBOX = """{{Infobox Item
|version1 = Undamaged
|version2 = 100
|name1 = Karil's coif
|name2 = Karil's coif 100
|image1 = [[File:Karil's coif.png]]
|image2 = [[File:Karil's coif.png]]
|id1 = 4732
|id2 = 4928
}}
{{Infobox Bonuses
|slot = head
}}"""

COIF, COIF100, AVA, BODY, CHAPS, DHCB, BOLTS, SHARK, LAW, DUST, PIPE, MASK = (
    4732, 4928, 22109, 2503, 2497, 21012, 21946, 385, 563, 4696, 12926, 27235)
MAPPING = {
    COIF: {"id": COIF, "name": "Karil's coif", "icon": "Karil's coif.png"},
    BODY: {"id": BODY, "name": "Black d'hide body", "icon": "x.png"},
    CHAPS: {"id": CHAPS, "name": "Black d'hide chaps", "icon": "x.png"},
    DHCB: {"id": DHCB, "name": "Dragon hunter crossbow", "icon": "x.png"},
    BOLTS: {"id": BOLTS, "name": "Diamond dragon bolts (e)", "icon": "x.png"},
    SHARK: {"id": SHARK, "name": "Shark", "icon": "x.png"},
    LAW: {"id": LAW, "name": "Law rune", "icon": "x.png"},
    DUST: {"id": DUST, "name": "Dust rune", "icon": "x.png"},
    PIPE: {"id": PIPE, "name": "Toxic blowpipe", "icon": "x.png"},
    MASK: {"id": MASK, "name": "Masori mask (f)", "icon": "x.png"},
}
PRICES = {COIF: 90_000, BODY: 7_000, CHAPS: 3_000, DHCB: 28_000_000, BOLTS: 3_000, SHARK: 900, LAW: 150,
          DUST: 300, PIPE: 5_000_000, MASK: 40_000_000}
WIKI_ITEMS = {"karil's coif": {"id": COIF, "ids": [COIF, COIF100], "name": "Karil's coif", "image": "Karil's coif.png",
                               "slot": "head"},
              "ava's assembler": {"id": AVA, "ids": [AVA], "name": "Ava's assembler", "image": "a.png", "slot": "cape"}}


class FakeEngine:
    def __init__(self):
        self.mapping = MAPPING
        self.tax = market.Tax(config.DEFAULTS, MAPPING)
        self.by_id = {i: {"high": p, "low": p} for i, p in PRICES.items()}


class ParseTests(unittest.TestCase):
    def test_strategy_page(self):
        s = setups.parse_strategy("Vorkath/Strategies", PAGE)
        self.assertEqual(s["boss"], "Vorkath")
        self.assertEqual(len(s["styles"]), 1)  # the one-slot melee swap is not a setup
        st = s["styles"][0]
        self.assertEqual(st["tiers"]["head"], [["Masori mask (f)", "Void ranger helm"], ["Karil's coif"]])
        self.assertEqual(st["special"], [["Toxic blowpipe"]])
        self.assertEqual(st["example"], "Ranged (crossbow)")
        self.assertEqual(st["inv"][:3], [("Diamond dragon bolts (e)", 500), ("Shark", 1), ("Shark", 1)])
        self.assertEqual(st["rp"], [("Law rune", 1), ("Dust rune", 1)])
        self.assertEqual(st["exampleEq"], {"head": "Karil's coif", "cape": "Ava's assembler"})

    def test_positional_inventory_and_cells(self):
        _, pos, named, _ = setups.template_at("{{Inventory|align = right|Coins 10000\\1000000|Lockpick|Stamina potion}}", 0)
        cells = setups.inventory(pos, named)
        self.assertEqual(cells[:3], [("Coins", 1000000), ("Lockpick", 1), ("Stamina potion", 1)])
        self.assertIsNone(cells[3])

    def test_infobox_versions(self):
        info = setups.parse_infobox(INFOBOX, "Karil's coif 100", None)
        self.assertEqual(info["id"], COIF100)
        self.assertEqual(info["ids"], [COIF, COIF100])
        self.assertEqual(info["slot"], "head")
        self.assertEqual(setups.parse_infobox(INFOBOX, "Karil's coif", None)["id"], COIF)
        self.assertEqual(setups.parse_infobox(INFOBOX, "x", "100")["id"], COIF100)

    def test_snapshot(self):
        d = setups.load()
        self.assertGreater(len(d["bosses"]), 15)
        self.assertIn("ava's assembler", d["items"])


class FitTests(unittest.TestCase):
    def setUp(self):
        self.items = setups.Items(FakeEngine(), WIKI_ITEMS)
        self.style = setups.parse_strategy("Vorkath/Strategies", PAGE)["styles"][0]

    def test_degraded_copy_counts_and_upgrade_is_next_tier(self):
        s = setups.from_style("Vorkath", self.style, self.items, {COIF100: 1, AVA: 1}, "url")
        head = s["eq"]["head"]
        self.assertEqual(head["item"]["id"], COIF100)  # the copy you own goes in the setup
        self.assertEqual(head["item"]["tier"], 2)
        self.assertEqual(head["upgrade"]["name"], "Masori mask (f)")
        self.assertEqual(s["eq"]["cape"]["item"]["name"], "Ava's assembler")

    def test_gaps_offer_budget_and_best(self):
        s = setups.from_style("Vorkath", self.style, self.items, {}, "url")
        body = s["eq"]["body"]
        self.assertIsNone(body["item"])
        self.assertEqual(body["budget"]["name"], "Black d'hide body")
        self.assertEqual(body["best"], None)  # Masori body (f) has no ID here
        gap = next(g for g in s["gaps"] if g["slot"] == "body")
        self.assertEqual((gap["name"], gap["price"]), ("Black d'hide body", 7000))
        sup = [g for g in s["gaps"] if g["slot"] == "inventory"]
        self.assertTrue(any(g["name"] == "Diamond dragon bolts (e)" and g["qty"] == 500 for g in sup))

    def test_special_weapon_goes_in_the_inventory(self):
        s = setups.from_style("Vorkath", self.style, self.items, {PIPE: 1}, "url")
        self.assertTrue(any(c and c.get("special") and c["id"] == PIPE for c in s["inv"]))

    def test_wiki_stack_means_any_amount(self):
        s = setups.from_items("method", "Test", "Thieving", [("Shark", 100000)], self.items, {SHARK: 3})
        self.assertEqual((s["inv"][0]["qty"], s["inv"][0]["owned"], s["inv"][0]["stack"]), (1, True, True))
        s = setups.from_items("method", "Test", "Thieving", [("Shark", 100000)], self.items, {})
        gap = s["gaps"][0]
        self.assertTrue(gap["stack"])
        self.assertIsNone(gap["price"])  # not 100,000 sharks' worth

    def test_portable_format(self):
        s = setups.from_style("Vorkath", self.style, self.items, {COIF: 1, SHARK: 50}, "url")
        p = setups.portable(s)
        st = p["setup"]
        self.assertEqual(len(st["inv"]), 28)
        self.assertEqual(len(st["eq"]), 14)
        self.assertEqual(st["eq"][setups.EQ_ORDER.index("head")], {"id": COIF, "f": True})
        # A slot you own nothing for holds the budget pick, so Inventory Setups highlights it.
        self.assertEqual(st["eq"][setups.EQ_ORDER.index("body")]["id"], BODY)
        self.assertEqual(st["inv"][0], {"id": BOLTS, "q": 500, "f": True})
        self.assertEqual(st["rp"], [{"id": LAW}, {"id": DUST}])
        self.assertEqual(st["hc"], "#FFFF0000")
        self.assertIsInstance(st["afi"], dict)
        self.assertEqual(st["name"], "Vorkath: Ranged (crossbow)")
        self.assertIn("Not owned yet", st["notes"])
        self.assertEqual(p["layout"][16], BOLTS)
        self.assertEqual(p["layout"][48], LAW)
        json.dumps(p)


class CustomTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.dir.name, "t.sqlite3"))
        account.init(self.db)
        networth.init(self.db)
        quests.init(self.db)
        setups.init(self.db)
        self.engine = FakeEngine()

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()

    def test_save_build_and_copy(self):
        sid = setups.custom_save(self.db, None, "Slayer", {"eq": {"head": COIF, "bogus": 1}, "inv": [[SHARK, 20]]}, 1)
        account.apply_events(self.db, [{"v": 1, "t": 1000, "type": "container", "acct": "rsprofile--1", "name": "Z",
                                        "container": "bank", "items": [[SHARK, 5]]}])
        s = setups.build(self.db, self.engine, f"mine|{sid}")
        self.assertEqual(s["name"], "Slayer")
        self.assertEqual(s["eq"]["head"]["item"]["id"], COIF)
        self.assertNotIn("bogus", s["eq"])
        self.assertEqual(s["inv"][0]["qty"], 20)
        self.assertEqual(s["inv"][0]["have"], 5)
        self.assertEqual(len(s["inv"]), 28)
        self.assertTrue(any(g["name"] == "Karil's coif" for g in s["gaps"]))  # saved but not owned
        back = setups.to_custom(s)
        self.assertEqual(back["eq"], {"head": COIF})
        self.assertEqual(back["inv"][0], [SHARK, 20])
        setups.custom_save(self.db, sid, "Slayer 2", back, 2)
        self.assertEqual(setups.custom_list(self.db)[0]["name"], "Slayer 2")
        setups.custom_delete(self.db, sid)
        self.assertEqual(setups.custom_list(self.db), [])
        with self.assertRaises(KeyError):
            setups.build(self.db, self.engine, f"mine|{sid}")

    def test_quest_setup(self):
        quests.store(self.db, {"Rope quest": {"name": "Rope quest", "items": [{"name": "Shark", "qty": 3},
                                                                              {"name": "Coins", "qty": 1}],
                                              "req": {"skills": {}, "quests": [], "qp": None}, "qp": 1}})
        s = setups.build(self.db, self.engine, "quest|Rope quest")
        self.assertEqual(s["name"], "Rope quest")
        self.assertEqual([c["name"] for c in s["inv"] if c], ["Shark"])  # "Coins" is generic
        self.assertEqual(s["inv"][0]["qty"], 3)

    def test_export_file(self):
        path = setups.export_file(self.dir.name, "While Guthix Sleeps", [{"setup": {}, "layout": []}])
        self.assertTrue(path.endswith(os.path.join("setups", "While_Guthix_Sleeps.json")))
        with open(path) as f:
            self.assertEqual(json.load(f), [{"setup": {}, "layout": []}])

    def test_search_items(self):
        hits = setups.search_items(self.engine, WIKI_ITEMS, "ava")
        self.assertEqual(hits[0]["name"], "Ava's assembler")
        self.assertEqual(hits[0]["slot"], "cape")
        self.assertEqual(setups.search_items(self.engine, WIKI_ITEMS, "a"), [])


if __name__ == "__main__":
    unittest.main()
