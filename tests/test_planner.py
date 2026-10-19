"""Tests for the quest parser, quest time estimates, account quest state and the goal planner."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import account, config, market, networth, planner, quests, questtime, training  # noqa: E402
from geco.db import DB  # noqa: E402
from geco.skills import xp_for_level  # noqa: E402

NAMES = {quests.norm(n): n for n in ["Dragon Slayer I", "Dragon Slayer II", "Legends' Quest", "Dream Mentor",
                                     "Barbarian Training", "Lunar Diplomacy", "The Fremennik Trials",
                                     "Tears of Guthix", "Defender of Varrock", "While Guthix Sleeps"]}

# Shaped like the Wiki's Quest details requirements for While Guthix Sleeps (trimmed).
WGS_REQ = """* {{SCP|Quest|180}}
* {{SCP|Thieving|72}}
* {{SCP|Magic|67}}
* {{SCP|Agility|66}} {{Boostable|no}}
* {{SCP|Farming|65}}
* Ability to enter the [[Warriors' Guild]] ({{SCP|Attack|99}} or {{SCP|Strength|99}}, or a combined 130)
* Completion of the following quests:
** [[Defender of Varrock]]
*** [[Shield of Arrav]]
** [[Dream Mentor]]
*** [[Lunar Diplomacy]]
**** [[The Fremennik Trials]]
** [[Tears of Guthix]]"""

# Shaped like Dragon Slayer II: the prerequisite tree sits under a header one level down.
DS2_REQ = """* {{SCP|Quest|200}}
* {{SCP|Magic|75}}
* {{SCP|Smithing|70}}
* Completion of the following quests:
** [[Legends' Quest]]
*** [[Heroes' Quest]]
** [[Dream Mentor]]
*** [[Lunar Diplomacy]]
** [[Dragon Slayer I]]
* Partial completion of [[Barbarian Training]]"""

ITEMS = """* [[Coins]]
* 3 [[Bronze nails]] (at least 12 recommended)
* At least 12 [[Steel nails]]
* [[Hammer]]
* {{plinkt|Saw}}"""


class ParserTests(unittest.TestCase):
    def test_wgs_requirements(self):
        r = quests.parse_requirements(WGS_REQ, NAMES)
        self.assertEqual(r["qp"], 180)
        self.assertEqual(r["skills"]["Thieving"]["level"], 72)
        self.assertTrue(r["skills"]["Thieving"]["boostable"])
        self.assertFalse(r["skills"]["Agility"]["boostable"])
        # The Warriors' Guild line is not read as 99 Attack and 99 Strength.
        self.assertNotIn("Attack", r["skills"])
        self.assertNotIn("Strength", r["skills"])
        self.assertEqual([o["kind"] for o in r["other"]], ["warriors_guild"])
        # Only the direct prerequisites, not the ones nested under them.
        self.assertEqual(r["quests"], ["Defender of Varrock", "Dream Mentor", "Tears of Guthix"])

    def test_ds2_header_depth(self):
        r = quests.parse_requirements(DS2_REQ, NAMES)
        self.assertEqual(r["qp"], 200)
        self.assertEqual(set(r["skills"]), {"Magic", "Smithing"})
        self.assertEqual(r["quests"], ["Legends' Quest", "Dream Mentor", "Dragon Slayer I", "Barbarian Training"])

    def test_items_quantities(self):
        got = [(i["name"], i["qty"]) for i in quests.parse_items(ITEMS)]
        self.assertEqual(got, [("Coins", 1), ("Bronze nails", 3), ("Steel nails", 12), ("Hammer", 1)])

    def test_norm_matches_runelite_names(self):
        self.assertEqual(quests.norm("The Fremennik Trials"), quests.norm("Fremennik Trials"))
        self.assertEqual(quests.norm("Recipe for Disaster (quest)"), quests.norm("Recipe for Disaster"))

    def test_parse_quest(self):
        text = ("{{Quest details\n|start = Talk to [[Ivy Sophista]] in [[Taverley]].\n|difficulty = Grandmaster\n"
                "|length = Very Long\n|requirements =\n" + WGS_REQ + "\n|items =\n" + ITEMS + "\n}}\n"
                "{{Quest rewards|qp = 5}}\n==Walkthrough==\n* Talk to Ivy.\n* Kill the boss.\n")
        q = quests.parse_quest("While Guthix Sleeps", text, NAMES)
        self.assertEqual(q["qp"], 5)
        self.assertEqual(q["difficulty"], "Grandmaster")
        self.assertEqual(q["startPlace"], "Taverley")
        self.assertEqual(q["guide"]["steps"], 2)
        self.assertEqual(q["req"]["qp"], 180)

    def test_snapshot_loads(self):
        Q = quests.load_snapshot()
        self.assertGreater(len(Q), 150)
        self.assertIn("While Guthix Sleeps", Q)


class QuestTimeTests(unittest.TestCase):
    def test_rating(self):
        mid, lo, hi = questtime.rating("Long")
        self.assertAlmostEqual(mid, 75)
        self.assertEqual((lo, hi), (45, 120))
        mid, lo, hi = questtime.rating("Short - Medium")
        self.assertTrue(18 < mid < 35)
        self.assertEqual((lo, hi), (8, 60))
        self.assertIsNone(questtime.rating(""))

    def test_estimate_stays_in_rating_range(self):
        Q = quests.load_snapshot()
        for name in ("While Guthix Sleeps", "Tears of Guthix", "Dragon Slayer II"):
            q = Q[name]
            e = questtime.estimate(q, Q)
            r = questtime.rating(q["length"])
            self.assertGreaterEqual(e["minutes"], r[1] - 1)
            self.assertLessEqual(e["minutes"], r[2] + 1)
            self.assertLess(e["low"], e["minutes"])
            self.assertGreater(e["high"], e["minutes"])
            slow = questtime.estimate(q, Q, pace=2.0)
            self.assertAlmostEqual(slow["minutes"], e["minutes"] * 2, delta=2)


def quest(name, qp=1, length="Short", prereqs=(), skills=None, qp_req=None, items=(), place=None, diff="Novice"):
    return {"name": name, "difficulty": diff, "length": length, "start": "", "startPlace": place, "members": True,
            "qp": qp, "req": {"skills": {s: {"level": lv, "boostable": b} for s, (lv, b) in (skills or {}).items()},
                              "qp": qp_req, "combat": None, "quests": list(prereqs), "other": []},
            "items": [{"name": n, "qty": q, "line": n} for n, q in items], "recommended": [], "guide": {}}


ROPE, POT = 954, 3032
MAPPING = {ROPE: {"id": ROPE, "name": "Rope", "limit": 100}, POT: {"id": POT, "name": "Agility potion(4)", "limit": 100}}


class FakeEngine:
    def __init__(self):
        self.mapping = MAPPING
        self.tax = market.Tax(config.DEFAULTS, MAPPING)
        self.by_id = {ROPE: {"id": ROPE, "high": 150, "low": 140}, POT: {"id": POT, "high": 1000, "low": 900}}


ACCT = "rsprofile--1"


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.dir.name, "t.sqlite3"))
        account.init(self.db)
        networth.init(self.db)
        quests.init(self.db)
        Q = {
            "Start": quest("Start", place="Lumbridge"),
            "Middle": quest("Middle", prereqs=["Start"], skills={"Agility": (50, True)}, items=[("Rope", 1)]),
            "Other": quest("Other", place="Lumbridge", length="Very Short"),
            "Goal": quest("Goal", qp=3, length="Long", prereqs=["Middle", "Other"], qp_req=6,
                          skills={"Thieving": (60, False)}, items=[("Rope", 2)], diff="Grandmaster"),
            "Filler": quest("Filler", qp=2, length="Very Short"),
            "Done": quest("Done", qp=1),
            "Locked": quest("Locked", qp=5, length="Very Short", skills={"Mining": (99, True)}),
        }
        quests.store(self.db, Q)
        levels = {"Agility": [47, xp_for_level(47)], "Thieving": [55, xp_for_level(55)], "Mining": [40, xp_for_level(40)]}
        account.apply_events(self.db, [
            {"v": 1, "t": 1000, "type": "quests", "acct": ACCT, "name": "Zezima",
             "states": {"Done": 2, "Start": 0, "Middle": 0, "Other": 0, "Goal": 0, "Filler": 0, "Locked": 0}, "qp": 1},
            {"v": 1, "t": 1000, "type": "levels", "acct": ACCT, "name": "Zezima", "levels": levels, "combat": 60},
        ])
        self.engine = FakeEngine()

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()

    def plan(self, **goal):
        return planner.build(self.db, self.engine, config.DEFAULTS, goal, acct=ACCT)

    def test_quest_plan(self):
        p = self.plan(type="quest", name="Goal")
        qs = [t["quest"] for t in p["tasks"] if t["type"] == "quest"]
        # Prerequisites before the quests that need them, the goal last, and quest point filler
        # chosen from quests the account can already do (Locked needs 99 Mining).
        self.assertEqual(qs[-1], "Goal")
        self.assertLess(qs.index("Start"), qs.index("Middle"))
        self.assertIn("Filler", qs)
        self.assertNotIn("Locked", qs)
        self.assertNotIn("Done", qs)
        self.assertEqual(p["qp"]["filler"], ["Filler"])
        self.assertTrue(p["qp"]["enough"])
        # Same start area keeps Start and Other together.
        self.assertEqual(abs(qs.index("Start") - qs.index("Other")), 1)

    def test_skills_boosts_and_shopping(self):
        p = self.plan(type="quest", name="Goal")
        kinds = {t["skill"]: t for t in p["tasks"] if t["type"] in ("skill", "boost")}
        # Agility 47 to 50 is boostable and an agility potion (+3) is the smallest boost that covers it;
        # Thieving is not boostable.
        self.assertEqual(kinds["Agility"]["type"], "boost")
        self.assertEqual(kinds["Thieving"]["type"], "skill")
        self.assertGreater(kinds["Thieving"]["hours"], 0)
        order = [t.get("quest") or t.get("skill") for t in p["tasks"]]
        self.assertLess(order.index("Agility"), order.index("Middle"))
        self.assertLess(order.index("Thieving"), order.index("Goal"))
        shop = {s["name"]: s for s in p["shopping"]}
        self.assertEqual(shop["Rope"]["qty"], 3)  # quantities over one add up as consumables
        self.assertEqual(kinds["Agility"]["boost"], "Agility potion")
        self.assertEqual(shop["Agility potion(4)"]["qty"], 1)
        self.assertEqual(p["shoppingCost"], 3 * 150 + 1000)

    def test_finished_goal_is_done(self):
        p = self.plan(type="quest", name="Done")
        self.assertTrue(p["done"])
        self.assertEqual(p["tasks"], [])

    def test_skill_goal(self):
        p = self.plan(type="skill", skill="Thieving", level=60)
        self.assertEqual(len(p["tasks"]), 1)
        self.assertEqual(p["tasks"][0]["xp"], xp_for_level(60) - xp_for_level(55))

    def test_unknown_goal(self):
        with self.assertRaises(ValueError):
            self.plan(type="quest", name="Not a quest")

    def test_active_goal(self):
        planner.set_active(self.db, {"type": "quest", "name": "Goal"})
        self.assertEqual(planner.active(self.db)["name"], "Goal")


class QuestStateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.dir.name, "t.sqlite3"))
        account.init(self.db)

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()

    def states(self, t, **s):
        account.apply_quests(self.db, ACCT, t, {"states": s, "qp": 10})

    def test_transitions_are_timed_but_not_the_first_snapshot(self):
        self.states(100, A=2, B=0, C=1)
        self.states(200, A=2, B=1, C=2)
        self.states(900, A=2, B=2, C=2)
        times = {r["name"]: (r["started"], r["finished"]) for r in account.quest_times(self.db, ACCT)}
        self.assertEqual(times, {"B": (200, 900)})  # C was already started when first seen
        p = account.progress(self.db, ACCT)
        self.assertEqual(p["qp"], 10)
        self.assertEqual(p["quests"], {"A": 2, "B": 2, "C": 2})


class TrainingTests(unittest.TestCase):
    def test_plan_skill_picks_fastest_and_measured_rate(self):
        def price(name, side="buy"):
            return None
        p = training.plan_skill("Agility", 63, xp_for_level(63), 66, price)
        self.assertEqual(p["method"], "Seers' Village rooftop course")
        self.assertEqual(p["rateSource"], "table")
        mine = training.plan_skill("Agility", 63, xp_for_level(63), 66, price, measured={"xph": 60_000})
        self.assertEqual(mine["rateSource"], "yours")
        self.assertLess(mine["hours"], p["hours"])
        self.assertIsNone(training.plan_skill("Agility", 70, xp_for_level(70), 66, price))

    def test_cost_per_xp(self):
        prices = {"Nature rune": 100}
        m = [x for x in training.METHODS["Magic"] if x[0] == "High Level Alchemy"][0]
        self.assertAlmostEqual(training.gp_per_xp(m, lambda n, s="buy": prices.get(n)), 100 / 65)

    def test_best_boost(self):
        self.assertEqual(training.best_boost("Agility", 4)[0], "Summer pie")
        self.assertEqual(training.best_boost("Agility", 2)[0], "Agility potion")
        self.assertIsNone(training.best_boost("Agility", 9))



GUIDE = """{{External|rs}}
Intro text about training.

==Fastest experience==
===Levels 20\u201347: Brimhaven Agility Arena===
Players can expect around 45,000\u201350,000 experience per hour here.

===Levels 47\u201362: Wilderness Agility Course===
{{Inventory|align=right
|Knife |Summer pie |Stamina potion(4)
}}
Players can gain around 50,000\u201360,000 experience per hour.

===Levels 62\u201399: Hallowed Sepulchre===
To access the Hallowed Sepulchre, players must have completed [[Sins of the Father]].
{{Var|base|1000}}
{| class="wikitable"
! {{SCP|Agility}} level !! XP/hour
|-
| 62 || {{formatnum:{{#expr:{{#var:base}}*60}}}}
|-
| 72 || 75,000
|-
| 82 || -1
|}

==Other methods==
===Levels 40\u201399: Rooftop course===
This area requires partial completion of [[Regicide]]; it is optional. Around 40k xp/hr.

==References==
{{Reflist}}
"""

BOOSTS = """{| class="wikitable"
! colspan="2" |Boost
!Level<br/>increase
!Visibility
!Other info
|-
|{{plinkt|Agility potion}}
|3
|Visible
|{{NA}}
|-
|{{plinkt|Spicy stew}}
|±0-5
|Visible
|Random
|-
|{{plinkt|Super attack}}
|5-19
|Visible
|
|-
|{{plinkt|Hidden thing}}
|4
|Invisible
|
|}"""


class SkillGuideTests(unittest.TestCase):
    def setUp(self):
        from geco import skillguides
        self.G = skillguides
        self.names = {quests.norm(n): n for n in ["Sins of the Father", "Regicide"]}

    def test_expand(self):
        out = self.G.expand("{{#vardefine:a|{{#expr:{{GEP|Rope}}*2}}}}{{Coins|{{#var:a}}/4}} {{SCP|Magic|70}}",
                            lambda n: 10)
        self.assertEqual(out, "5 {{SCP|Magic|70}}")
        self.assertEqual(self.G._expr("2780*50"), 139000)
        self.assertEqual(self.G._expr("(10-4)/3 round 1"), 2)

    def test_parse_guide(self):
        ms = {m["name"]: m for m in self.G.parse_guide("Agility", "Agility training", GUIDE, self.names)}
        self.assertEqual(set(ms), {"Brimhaven Agility Arena", "Wilderness Agility Course", "Hallowed Sepulchre",
                                   "Rooftop course"})
        b = ms["Brimhaven Agility Arena"]
        self.assertEqual((b["lo"], b["hi"], b["typical"]), (20, 47, 47500))
        self.assertTrue(b["fastest"])
        self.assertIn("Summer pie", ms["Wilderness Agility Course"]["items"])
        h = ms["Hallowed Sepulchre"]
        self.assertEqual(h["curve"], [[62, 60000], [72, 75000]])  # the -1 row is not a rate
        self.assertEqual(h["req"]["quests"], [{"quest": "Sins of the Father", "partial": False}])
        self.assertIn("%E2%80%93", h["url"])  # the Wiki's anchor keeps its dash
        r = ms["Rooftop course"]
        self.assertEqual(r["req"]["quests"], [])  # one place among several, so only a note
        self.assertEqual(r["typical"], 40000)

    def test_parse_boosts(self):
        b = {x["item"]: x for x in self.G.parse_boosts("Agility", BOOSTS)}
        self.assertEqual(set(b), {"Agility potion", "Super attack"})
        self.assertEqual(self.G.boost_at(b["Super attack"], 1), 5)
        self.assertEqual(self.G.boost_at(b["Super attack"], 99), 19)

    def test_plan_switches_and_skips_locked(self):
        guides = {"methods": {"Agility": self.G.parse_guide("Agility", "Agility training", GUIDE, self.names)},
                  "boosts": {}}
        p = training.plan_skill("Agility", 45, xp_for_level(45), 70, lambda n, s="buy": None, guides=guides,
                                quest_state=lambda q: 2)
        got = [(s["method"], s["from"], s["to"]) for s in p["segments"]]
        self.assertEqual(got, [("Brimhaven Agility Arena", 45, 47), ("Wilderness Agility Course", 47, 62),
                               ("Hallowed Sepulchre", 62, 70)])
        self.assertEqual(p["rateSource"], "wiki")
        locked = training.plan_skill("Agility", 65, xp_for_level(65), 70, lambda n, s="buy": None, guides=guides,
                                     quest_state=lambda q: 0)
        self.assertNotIn("Hallowed Sepulchre", [s["method"] for s in locked["segments"]])
        opt = {o["method"]: o for o in locked["options"]}
        self.assertEqual(opt["Hallowed Sepulchre"]["needs"], ["Sins of the Father"])

    def test_wiki_boost(self):
        guides = {"boosts": {"Agility": [{"item": "Agility potion", "lo": 3, "hi": 3, "note": ""},
                                         {"item": "Summer pie", "lo": 5, "hi": 5, "note": ""},
                                         {"item": "Agility mix", "lo": 3, "hi": 3, "note": "Requires Barbarian Training"}]}}
        prices = {"Agility potion": 900, "Summer pie": 400, "Agility mix": 10}
        b = training.best_boost("Agility", 3, 60, lambda n, s="buy": prices.get(n), guides)
        self.assertEqual(b, ("Summer pie", 5, "Summer pie"))  # the cheapest that covers it, no unlocks

    def test_snapshot(self):
        g = self.G.load()
        self.assertGreater(sum(len(v) for v in g["methods"].values()), 100)
        self.assertIn("Agility", g["boosts"])



class QuestXpTests(unittest.TestCase):
    """Quest XP rewards count toward training, and quests are added when their XP is quicker."""

    def test_parse_rewards(self):
        r = quests.parse_rewards("""* {{SCP|Thieving|80,000|link=yes}} [[experience]]
* {{SCP|Hunter|50,000}} [[Hunter]] experience
*4,650 [[Experience]] in your choice of two skills: {{SCP|Agility}} [[Agility]], or {{SCP|Thieving}} [[Thieving]].
* An antique lamp rewarding 5,000 XP in any skill of at least level 30
* Four magic lamps offering 20,000 {{SCP|Slayer}} [[Slayer]] XP and 5,000 {{SCP|Magic}} [[Magic]] XP
* [[Duradel's notes]] grants {{SCP|Slayer}} experience equal to 15x the player's Slayer level""")
        self.assertEqual(r["xp"], {"Thieving": 80000, "Hunter": 50000, "Slayer": 20000, "Magic": 5000})
        self.assertEqual(r["choices"], [{"xp": 4650, "count": 2, "skills": ["Agility", "Thieving"]},
                                        {"xp": 5000, "count": 1, "skills": ["any"]}])
        self.assertEqual(len(r["notes"]), 1)

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.dir.name, "t.sqlite3"))
        account.init(self.db)
        networth.init(self.db)
        quests.init(self.db)

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()

    def plan(self, Q, lv, goal):
        quests.store(self.db, Q)
        states = {n: 0 for n in Q}
        account.apply_events(self.db, [
            {"v": 1, "t": 1000, "type": "quests", "acct": ACCT, "name": "Z", "states": states, "qp": 10},
            {"v": 1, "t": 1000, "type": "levels", "acct": ACCT, "name": "Z",
             "levels": {k: [v, xp_for_level(v)] for k, v in lv.items()}, "combat": 60}])
        return planner.build(self.db, FakeEngine(), config.DEFAULTS, goal, acct=ACCT)

    def rewards(self, xp=None, choices=None):
        return {"xp": xp or {}, "choices": choices or [], "notes": []}

    def test_plan_quest_xp_counts_and_lamp_goes_where_needed(self):
        pre = quest("Pre", length="Short")
        pre["rewards"] = self.rewards({"Thieving": 1000}, [{"xp": 50000, "count": 1, "skills": ["any"]}])
        goal = quest("Goal", prereqs=["Pre"], skills={"Thieving": (54, False), "Agility": (50, False)})
        p = self.plan({"Pre": pre, "Goal": goal}, {"Thieving": 50, "Agility": 49}, {"type": "quest", "name": "Goal"})
        tasks = {t.get("skill") or t.get("quest"): t for t in p["tasks"]}
        # 50,000 lamp XP saves more on Thieving (50 to 54 needs about 49.5k) than Agility (one level).
        self.assertEqual(tasks["Pre"]["xpReward"]["picks"], [{"skill": "Thieving", "xp": 50000, "count": 1}])
        self.assertNotIn("Thieving", tasks)  # covered by quest XP
        self.assertTrue(next(r for r in p["requirements"] if r["skill"] == "Thieving")["byQuestXp"])
        self.assertIn("Agility", tasks)

    def test_quest_added_for_xp_only_when_quicker(self):
        goal = quest("Goal", skills={"Thieving": (40, False)})
        big = quest("Big reward", length="Very Short")
        big["rewards"] = self.rewards({"Thieving": 30000})
        tiny = quest("Tiny reward", length="Very Long")
        tiny["rewards"] = self.rewards({"Thieving": 500})
        p = self.plan({"Goal": goal, "Big reward": big, "Tiny reward": tiny}, {"Thieving": 20},
                      {"type": "quest", "name": "Goal"})
        qs = [t["quest"] for t in p["tasks"] if t["type"] == "quest"]
        self.assertEqual(qs[0], "Big reward")
        self.assertNotIn("Tiny reward", qs)
        first = next(t for t in p["tasks"] if t["type"] == "quest")
        self.assertTrue(first["forXp"])
        self.assertGreater(first["xpReward"]["saves"], first["hours"])


if __name__ == "__main__":
    unittest.main()
