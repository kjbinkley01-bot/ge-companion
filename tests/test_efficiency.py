"""Tests for the dailies page, the best unlocks advisor and the Slayer advisor."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geco import dailies, slayer, unlocks  # noqa: E402
import test_farming as tf  # noqa: E402

DAY = 86400
NOW = 1_792_000_000 + 5 * 3600  # 05:00 UTC

DIARIES = {"Ardougne": [1, 0, 0, 0], "Varrock": [1, 1, 0, 0], "Kandarin": [0, 0, 0, 0],
           "Lumbridge & Draynor": [1, 0, 0, 0], "Kourend & Kebos": [0, 0, 0, 0], "Western Provinces": [0, 0, 0, 0],
           "Wilderness": [0, 0, 0, 0], "Desert": [0, 0, 0, 0], "Falador": [0, 0, 0, 0], "Fremennik": [0, 0, 0, 0],
           "Karamja": [0, 0, 0, 0], "Morytania": [0, 0, 0, 0]}


def engine(extra=None):
    names = dict(tf.PRICES, **{"Battlestaff": 7400, "Bucket of sand": 120, "Nature rune": 140, "Pure essence": 4})
    names.update(extra or {})
    mapping = {i + 1: {"id": i + 1, "name": n} for i, n in enumerate(sorted(names))}
    e = tf.FakeEngine()
    e.mapping = mapping
    ids = {m["name"]: i for i, m in mapping.items()}
    e.by_id = {ids[n]: {"high": p, "low": p} for n, p in names.items()}
    from geco import config, market
    e.tax = market.Tax(config.DEFAULTS, mapping)
    return e


class DailiesTests(unittest.TestCase):
    def setUp(self):
        self.db = tf.make_db({"Farming": 50, "Magic": 50}, {"The Hand in the Sand": 2, "Tears of Guthix": 2})
        self.db.kv_set("diaries:a", {"diaries": DIARIES})

    def test_amounts_by_diary_and_value(self):
        p = dailies.plan(self.db, engine(), now=NOW)
        rows = {r["key"]: r for r in p["tasks"]}
        st = rows["battlestaves"]
        self.assertEqual(st["qty"], 30)  # medium Varrock
        self.assertEqual(st["value"], 30 * (7400 - 148 - 7000))  # sold after tax, bought at 7,000
        self.assertEqual(st["next"]["tier"], "hard")
        self.assertEqual(rows["sand"]["value"], 84 * (120 - 2))
        self.assertFalse(rows["essence"]["available"])  # Cromperty needs the medium Ardougne diary
        self.assertIn("medium Ardougne diary", rows["essence"]["needs"])
        self.assertTrue(rows["runes"]["varies"])
        self.assertFalse(rows["miscellania"]["available"])

    def test_done_resets_at_midnight_utc_and_weekly(self):
        dailies.mark(self.db, "a", "battlestaves", True, now=NOW)
        dailies.mark(self.db, "a", "tears", True, now=NOW)
        rows = {r["key"]: r for r in dailies.plan(self.db, engine(), now=NOW + 3600)["tasks"]}
        self.assertTrue(rows["battlestaves"]["done"])
        self.assertEqual(rows["battlestaves"]["resets"], NOW // DAY * DAY + DAY)
        tomorrow = {r["key"]: r for r in dailies.plan(self.db, engine(), now=NOW + DAY)["tasks"]}
        self.assertFalse(tomorrow["battlestaves"]["done"])
        self.assertTrue(tomorrow["tears"]["done"])  # a week, not a day
        week = {r["key"]: r for r in dailies.plan(self.db, engine(), now=NOW + 7 * DAY)["tasks"]}
        self.assertFalse(week["tears"]["done"])

    def test_trying_an_unlock(self):
        d = dict(DIARIES, Varrock=[1, 1, 1, 0])
        rows = {r["key"]: r for r in dailies.plan(self.db, engine(), now=NOW, diaries=d)["tasks"]}
        self.assertEqual(rows["battlestaves"]["qty"], 60)


class UnlockTests(unittest.TestCase):
    def test_quest_that_opens_a_patch_and_a_diary_tier(self):
        db = tf.make_db({"Farming": 50, "Magic": 50}, {"My Arm's Big Adventure": 0, "The Hand in the Sand": 2})
        db.kv_set("diaries:a", {"diaries": DIARIES})
        unlocks._cache.clear()
        r = unlocks.rank(db, engine(), mode="value")
        rows = {x["label"]: x for x in r["rows"]}
        # The quest opens the Troll Stronghold herb patch on the copy of the account.
        from geco import farming
        ctx = farming.Context(db, engine())
        cand = next(c for c in unlocks.candidates(ctx, DIARIES) if c[1] == "My Arm's Big Adventure")
        trial = ctx.clone()
        cand[2](trial, DIARIES)
        self.assertEqual(farming.plan_run("herb", ctx, "value")["patches"], 1)
        self.assertEqual(farming.plan_run("herb", trial, "value")["patches"], 2)
        self.assertEqual(farming.plan_run("herb", ctx, "value")["patches"], 1)  # the account itself is untouched
        varrock = rows["Hard Varrock diary"]  # 30 more battlestaves a day
        self.assertAlmostEqual(varrock["gp"], 7 * 30 * (7400 - 148 - 7000))
        self.assertEqual(r["rows"], sorted(r["rows"], key=lambda x: (-x["gp"], x["minutes"])))


ASSIGN = """{| class="wikitable"
!Monster
!Amount
!Unlock requirement
!Weight
|-
|[[Slayer task/Abyssal demons|Abyssal Demons]]
|130-200
|data-sort-value="8585"|<span class="scp" data-skill="Slayer" data-level="85">85</span>, completion of [[Priest in Peril]] or [[Fairytale II - Cure a Queen]]
|12
|-
|[[Slayer task/Aviansie|Aviansie]]
|120-200
|Unlocked the ''Watch the birdie'' ability via spending 80 [[Slayer reward points]]
|8
|}"""


class SlayerTests(unittest.TestCase):
    def test_parse_assignments(self):
        rows = slayer.parse_assignments(ASSIGN, ["Priest in Peril", "Fairytale II - Cure a Queen"])
        self.assertEqual([(r["task"], r["min"], r["max"], r["weight"]) for r in rows],
                         [("Abyssal demons", 130, 200, 12), ("Aviansie", 120, 200, 8)])
        self.assertEqual(rows[0]["req"]["levels"], {"Slayer": 85})
        self.assertEqual(rows[0]["req"]["quests"], [["Priest in Peril", "Fairytale II - Cure a Queen"]])
        self.assertEqual(rows[1]["req"]["unlock"], "Watch the birdie")

    def test_points_and_slots(self):
        # Duradel: 15 a task, 75 every 10th, 225 every 50th, 375 every 100th, 525 every 250th, 750 every 1,000th.
        self.assertAlmostEqual(slayer.avg_points("Duradel", {}), (15 * 1000 + 60 * 100 + 150 * 20 + 150 * 10 + 150 * 4 + 225) / 1000)
        self.assertGreater(slayer.avg_points("Nieve", {"Western Provinces": [1, 1, 1, 1]}), slayer.avg_points("Nieve", {}))
        self.assertEqual(slayer.block_slots(212, {}), 4)
        self.assertEqual(slayer.block_slots(320, {"Lumbridge & Draynor": [1, 1, 1, 1]}), 7)

    def test_optimise_blocks_heavy_bad_tasks_and_pays_for_skips(self):
        rows = [{"task": "Good", "weight": 10, "xp": 30000, "minutes": 60, "rating": 2, "force": None},
                {"task": "Fine", "weight": 10, "xp": 25000, "minutes": 60, "rating": 2, "force": None},
                {"task": "Slow heavy", "weight": 12, "xp": 10000, "minutes": 60, "rating": 2, "force": None},
                {"task": "Slow light", "weight": 3, "xp": 12000, "minutes": 60, "rating": 2, "force": None}]
        p = slayer.optimise(rows, slots=1, points=20, cancel=30, goal="xp")
        self.assertEqual(p["blocks"], ["Slow heavy"])
        self.assertEqual(p["skips"], ["Slow light"])
        self.assertGreaterEqual(p["balance"], 0)
        poor = slayer.optimise(rows, slots=1, points=1, cancel=30, goal="xp")  # can't pay for skips
        self.assertEqual(poor["skips"], [])
        forced = slayer.optimise([dict(r, force="do") if r["task"] == "Slow heavy" else r for r in rows],
                                 slots=1, points=20, cancel=30, goal="xp")
        self.assertNotIn("Slow heavy", forced["blocks"] + forced["skips"])

    def test_advise_uses_your_levels(self):
        db = tf.make_db({"Slayer": 70}, {"Priest in Peril": 2})
        db.run("UPDATE progress SET combat=110, qp=150 WHERE acct='a'")
        data = {"version": slayer.VERSION, "masters": {"Duradel": slayer.parse_assignments(ASSIGN, ["Priest in Peril"])},
                "monsters": {"Abyssal demons": {"hp": 150, "xp": 150}, "Aviansie": {"hp": 70, "xp": 70}}}
        db.kv_set("slayer_wiki", data)
        a = slayer.advise(db, engine())
        dur = next(m for m in a["masters"] if m["master"] == "Duradel")
        self.assertEqual([t["task"] for t in dur["lockedTasks"]], ["Abyssal demons", "Aviansie"])
        self.assertIn("85 Slayer", dur["lockedTasks"][0]["needs"])
        self.assertEqual(a["slots"], 3)
        slayer.save_prefs(db, "a", {"unlocks": ["Watch the birdie"]})
        dur = next(m for m in slayer.advise(db, engine())["masters"] if m["master"] == "Duradel")
        self.assertEqual([t["task"] for t in dur["tasks"]], ["Aviansie"])


if __name__ == "__main__":
    unittest.main()
