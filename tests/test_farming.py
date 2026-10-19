"""Tests for farming runs: Wiki parsing, yields, crop and teleport choice, routines, run setups."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import account, config, farming, market, setups  # noqa: E402
from geco.db import DB  # noqa: E402

RUNS_PAGE = """== Herb run ==
===Requirements===
*Completion of [[Bone Voyage]] to access [[Fossil Island]].
===Equipment and inventory===
{{Inventory|Spade|Rake}}
===Herb run sequence===
#Start at a bank.
#[[Falador]] patch:
##Use the teleport option on the [[Explorer's ring 2]], [[Explorer's ring 3|3]], or [[Explorer's ring 4|4]] and run north.
##Teleport to [[Falador]] and run south-east.
#[[Kastori]] calquat tree and fruit tree patches:
##Use a [[quetzal whistle]].
#[[Tree Gnome Village (location)|Tree Gnome Village]] patch:
##Run west.
"""

HOPS_TABLE = """{| class="wikitable"
|-
! rowspan="2" |Level
! rowspan="2" |Seed
! rowspan="2" |Protection
! rowspan="2" |Growth time
! rowspan="2" |Crop
! colspan="2" |Experience
|-
!Plant
!Harvest
|-
|3
|[[File:Barley seed.png|link=Barley seed]]<br/>[[Barley seed|Barley]] × 4
|[[File:Compost.png|link=Compost]]<br/>[[Compost]] × 3
|4×10 mins
|[[Barley]]
|8.5
|9.5
|}"""

# A small world: two herbs, two allotments, two flowers, one tree.
CROPS = [
    {"kind": "herb", "seed": "Guam seed", "name": "Guam", "level": 9, "growth": 80, "protection": None,
     "crop": "Grimy guam leaf", "plantXp": 11.0, "harvestXp": 12.5, "yield": [25, 80]},
    {"kind": "herb", "seed": "Gout tuber", "name": "Gout tuber", "level": 29, "growth": 80, "protection": None,
     "crop": None, "plantXp": 105.0, "harvestXp": 45.0, "yield": [39, 80]},
    {"kind": "allotment", "seed": "Potato seed", "name": "Potato", "level": 1, "growth": 40,
     "protection": ["Compost", 2], "crop": "Potato", "plantXp": 8.0, "harvestXp": 9.0, "yield": [101, 180]},
    {"kind": "flower", "seed": "Marigold seed", "name": "Marigold", "level": 2, "growth": 20, "protection": None,
     "crop": "Marigolds", "plantXp": 8.5, "harvestXp": 47.0, "totalXp": 55.5},
    {"kind": "flower", "seed": "Woad seed", "name": "Woad", "level": 25, "growth": 20, "protection": None,
     "crop": "Woad leaf", "plantXp": 20.5, "harvestXp": 21.0, "totalXp": 41.5},
    {"kind": "tree", "seed": "Willow seed", "name": "Willow", "level": 30, "growth": 240,
     "protection": ["Apples(5)", 1], "crop": "Willow logs", "plantXp": 25.0, "checkXp": 1456.5, "totalXp": 1481.5},
]
DISEASE = {"all herbs": {"cycles": 3, "none": 27 / 128, "compost": 14 / 128, "supercompost": 6 / 128,
                         "ultracompost": 3 / 128},
           "maple trees": {"cycles": 7, "none": 13 / 128, "compost": 7 / 128, "supercompost": 3 / 128,
                           "ultracompost": 2 / 128}}
STOPS = {
    "Herb run": [{"place": "Falador", "text": "Falador patch", "kinds": [], "needs": "",
                  "teleports": ["Use the [[Explorer's ring 2]], [[Explorer's ring 3|3]] and run north.",
                                "Teleport to [[Falador]] and run south-east."]},
                 {"place": "Troll Stronghold", "text": "Troll Stronghold patch", "kinds": [], "needs": "",
                  "teleports": ["Use the [[Stony basalt]]."]}],
    "Allotment, flower, and herb run": [{"place": "Catherby", "text": "Catherby patch", "kinds": [], "needs": "",
                                         "teleports": ["Teleport to [[Camelot]] and run east."]}],
    "Tree run": [{"place": "Lumbridge", "text": "Lumbridge patch", "kinds": [], "needs": "",
                  "teleports": ["Teleport to [[Lumbridge]] and run west."]}],
}
NAMES = ["Guam seed", "Grimy guam leaf", "Gout tuber", "Potato seed", "Potato", "Marigold seed", "Marigolds",
         "Woad seed", "Woad leaf", "Willow sapling", "Willow logs", "Apples(5)", "Compost", "Supercompost",
         "Ultracompost", "Explorer's ring 2", "Spade", "Rake", "Seed dibber", "Lumbridge teleport", "Coins",
         "Bottomless compost bucket", "Ring of dueling(8)", "Skills necklace(4)", "Amulet of glory(4)"]
MAPPING = {i + 1: {"id": i + 1, "name": n, "icon": n + ".png"} for i, n in enumerate(NAMES)}
ID = {n: i for i, n in ((k, v["name"]) for k, v in MAPPING.items())}
PRICES = {"Guam seed": 20, "Grimy guam leaf": 30, "Gout tuber": 650_000, "Potato seed": 5, "Potato": 10,
          "Marigold seed": 5, "Marigolds": 40, "Woad seed": 9, "Woad leaf": 110, "Willow sapling": 300,
          "Willow logs": 10, "Apples(5)": 200, "Compost": 50, "Supercompost": 100, "Ultracompost": 400,
          "Explorer's ring 2": 1000, "Spade": 100, "Rake": 100, "Seed dibber": 100, "Lumbridge teleport": 500,
          "Bottomless compost bucket": 2_000_000, "Ring of dueling(8)": 800, "Skills necklace(4)": 12_000}


class FakeEngine:
    def __init__(self, prices=None):
        self.mapping = MAPPING
        self.tax = market.Tax(config.DEFAULTS, MAPPING)
        self.by_id = {ID[n]: {"high": p, "low": p} for n, p in (prices or PRICES).items()}


def make_db(levels=None, quests=None, items=None):
    d = DB(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    account.init(d)
    setups.init(d)
    runs = {t: {"needs": [], "recommend": [], "example": None, "stops": st} for t, st in STOPS.items()}
    d.kv_set("farming_wiki", {"version": farming.VERSION, "fetched": 1, "crops": CROPS, "runs": runs,
                              "birds": [], "birdSeeds": [], "disease": DISEASE, "herbPatches": {}})
    if levels is not None:
        d.run("INSERT INTO accounts (acct, name, first_seen, last_seen) VALUES ('a', 'Me', 1, 2)")
        d.run("INSERT INTO progress (acct, t, qp, combat, levels, levels_t) VALUES ('a', 1, 10, 50, ?, 1)",
              (json.dumps({k: [v, 0] for k, v in levels.items()}),))
        for q, st in (quests or {}).items():
            d.run("INSERT INTO quest_state (acct, name, state, t) VALUES ('a', ?, ?, 1)", (q, st))
        d.run("INSERT INTO holdings_live (acct, container, t, items) VALUES ('a', 'bank', 1, ?)",
              (json.dumps(items or [[ID["Coins"], 1000]]),))
    return d


class ParseTests(unittest.TestCase):
    def test_runs_page(self):
        r = farming.parse_runs(RUNS_PAGE)["Herb run"]
        self.assertEqual([s["place"] for s in r["stops"]], ["Falador", "Kastori", "Tree Gnome Village (location)"])
        # Kinds come from after the place link only: "Tree Gnome Village" is not a tree patch.
        self.assertEqual([s["kinds"] for s in r["stops"]], [[], ["calquat", "fruit"], []])
        self.assertEqual(len(r["stops"][0]["teleports"]), 2)
        self.assertIn("Bone Voyage", r["needs"][0])

    def test_seed_table(self):
        c = farming.parse_seeds("hops_seeds", HOPS_TABLE)[0]
        self.assertEqual((c["kind"], c["name"], c["level"], c["growth"]), ("hops", "Barley", 3, 40))
        self.assertEqual(c["protection"], ["Compost", 3])
        self.assertEqual(c["seedQty"], 4)
        self.assertEqual((c["plantXp"], c["harvestXp"]), (8.5, 9.5))

    def test_yield_template(self):
        self.assertEqual(farming.parse_yield("{{Farming yield calculator|low=39|high=80|name=Ranarr}}"), (39, 80))


class ModelTests(unittest.TestCase):
    def test_chance_to_save(self):
        self.assertAlmostEqual(farming.chance_to_save(39, 80, 1), 40 / 256)
        self.assertAlmostEqual(farming.chance_to_save(39, 80, 99), 81 / 256)
        self.assertGreater(farming.chance_to_save(39, 80, 99, 0.1), farming.chance_to_save(39, 80, 99))
        self.assertAlmostEqual(farming.expected_harvest(6, 0.25), 8.0)

    def test_missing_yield_borrows_nearest_level(self):
        crop = {"kind": "herb", "level": 30}
        self.assertEqual(farming.crop_yield(crop, CROPS), [39, 80])

    def test_stale_high_price_is_ignored(self):
        prices = dict(PRICES)
        e = FakeEngine(prices)
        e.by_id[ID["Woad leaf"]] = {"high": 48_984, "low": 112}
        ctx = farming.Context(make_db({"Farming": 50}), e)
        self.assertEqual(ctx.price("Woad leaf"), 112)


class PlanTests(unittest.TestCase):
    def test_xp_mode_skips_absurd_cost(self):
        # Gout tuber gives more XP but costs 650k: Most XP does not pay over XP_GP gp per extra XP.
        ctx = farming.Context(make_db({"Farming": 50}), FakeEngine())
        best, alts = farming.choose_crop("herb", ctx, "xp")
        self.assertEqual(best["crop"], "Guam")
        self.assertEqual(best["compost"], "ultracompost")  # more lives, and cheap per extra XP
        self.assertEqual({a["crop"] for a in alts}, {"Guam", "Gout tuber"})

    def test_disease_free_patch_needs_no_compost_for_survival(self):
        ctx = farming.Context(make_db({"Farming": 50}), FakeEngine())
        plain = farming.crop_metrics(CROPS[0], ctx, "xp")
        free = farming.crop_metrics(CROPS[0], ctx, "xp", {"diseaseFree": True})
        self.assertLess(plain["survival"], 1)
        self.assertEqual(free["survival"], 1)
        self.assertGreater(free["xp"], plain["xp"])

    def test_tree_pays_gardener(self):
        ctx = farming.Context(make_db({"Farming": 50}), FakeEngine())
        m = farming.crop_metrics(CROPS[-1], ctx, "xp")
        self.assertTrue(m["pay"])  # 200 gp of apples beats a 7 cycle disease risk on a 300 gp sapling
        self.assertEqual(m["seed"], "Willow sapling")
        self.assertEqual(m["survival"], 1)

    def test_flower_guards_allotment(self):
        ctx = farming.Context(make_db({"Farming": 50}), FakeEngine())
        pair = farming.choose_allotment(ctx, "value")
        self.assertEqual(pair["flower"]["crop"], "Marigold")  # guards potatoes
        self.assertTrue(pair["allotment"]["guarded"])
        self.assertFalse(pair["allotment"]["pay"])
        self.assertEqual(pair["allotment"]["survival"], 1)

    def test_teleport_alternatives_and_fallback(self):
        e = FakeEngine()
        ctx = farming.Context(make_db({"Farming": 50, "Magic": 10}, items=[[ID["Explorer's ring 2"], 1]]), e)
        opts = STOPS["Herb run"][0]["teleports"]
        t = farming.pick_teleport(opts, ctx)
        self.assertEqual((t["index"], t["bring"], t["buy"]), (0, ["Explorer's ring 2"], []))
        # Magic too low for the Lumbridge spell: the tablet is bought instead.
        t = farming.pick_teleport(STOPS["Tree run"][0]["teleports"], ctx)
        self.assertEqual(t["buy"], ["Lumbridge teleport"])

    def test_quest_locks_stop(self):
        d = make_db({"Farming": 50}, {"My Arm's Big Adventure": 1})
        r = farming.plan_run("herb", farming.Context(d, FakeEngine()), "xp")
        troll = next(s for s in r["stops"] if s["place"] == "Troll Stronghold")
        self.assertFalse(troll["usable"])
        self.assertIn("My Arm's Big Adventure", troll["needs"])
        self.assertEqual(r["patches"], 1)

    def test_plan_all_and_routine(self):
        p = farming.plan_all(make_db({"Farming": 50}), FakeEngine(), "xp")
        self.assertEqual(p["levels"]["Farming"], 50)
        groups = {g["group"]: g["runs"] for g in p["routine"]["groups"]}
        self.assertEqual(groups["Herbs and allotments"], ["allotment"])  # it includes the herb patch
        self.assertIn("Trees", groups)
        self.assertGreater(p["routine"]["perDay"]["xp"], 0)
        allot = next(r for r in p["runs"] if r["key"] == "allotment")
        self.assertEqual(sorted(s["kind"] for s in allot["stops"]), ["allotment", "flower", "herb"])
        self.assertEqual(allot["patches"], 4)

    def test_unknown_account_assumes_everything(self):
        p = farming.plan_all(make_db(), FakeEngine(), "value")
        self.assertFalse(p["accountKnown"])
        herb = next(r for r in p["runs"] if r["key"] == "herb")
        self.assertEqual(herb["patches"], 2)

    def test_run_setup(self):
        d = make_db({"Farming": 50}, items=[[ID["Explorer's ring 2"], 1], [ID["Coins"], 5000]])
        s, r = farming.run_setup(d, FakeEngine(), "herb", "xp")
        inv = [c["name"] for c in s["inv"] if c]
        self.assertEqual(inv[:3], ["Spade", "Rake", "Seed dibber"])
        self.assertIn("Guam seed", inv)
        self.assertEqual(s["eq"]["ring"]["item"]["name"], "Explorer's ring 2")  # worn, and owned
        port = setups.portable(s)
        self.assertEqual(len(port["setup"]["inv"]), 28)
        s2 = setups.build(d, FakeEngine(), "run|tree|value")
        self.assertIn("Coins", [c["name"] for c in s2["inv"] if c])  # 200 a tree for the gardener to chop


if __name__ == "__main__":
    unittest.main()
