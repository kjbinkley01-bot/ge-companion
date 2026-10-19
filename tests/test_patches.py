"""Tests for patch tracking (decoding RuneLite's farming varbits, growth prediction, alerts, pace)
and the Slayer task log."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import account, patches, slayer  # noqa: E402
from geco.db import DB  # noqa: E402

T0 = 1_792_000_200  # a time just after a 20 minute farming tick boundary


def make_db():
    d = DB(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    account.init(d)
    return d


def farm(region, slots, t, birds=None):
    vals = [0] * 14
    for i, v in slots.items():
        vals[i] = v
    x, y = (region >> 8) * 64 + 32, (region & 255) * 64 + 32
    e = {"acct": "a", "name": "Me", "type": "farm", "t": t * 1000, "region": region, "x": x, "y": y, "plane": 0,
         "vals": vals}
    if birds:
        e["birdhouses"] = birds
    return e


class DecodeTests(unittest.TestCase):
    def test_herb_values(self):
        self.assertEqual(patches.decode("HERB", 32), ("RANARR", "GROWING", 0))
        self.assertEqual(patches.decode("HERB", 36), ("RANARR", "HARVESTABLE", 2))
        self.assertEqual(patches.decode("HERB", 3), ("WEEDS", "GROWING", 0))
        self.assertIsNone(patches.decode("NOPE", 1))

    def test_growth_follows_farming_ticks(self):
        p = patches.predict("HERB", 32, T0, T0 + 3600)
        self.assertEqual((p["name"], p["state"], p["stages"]), ("Ranarr", "GROWING", 5))
        self.assertEqual(p["stage"], 3)
        # Four more 20 minute ticks after the one at or before planting.
        self.assertEqual(p["doneAt"], T0 - T0 % 1200 + 4 * 1200)

    def test_tick_offset(self):
        # With a learned offset of 7 minutes, ticks fall 7 minutes before the plain boundaries.
        shifted = patches.tick_time(20, 1, T0, (7, 40))
        self.assertEqual((shifted + 7 * 60) % 1200, 0)
        self.assertTrue(T0 < shifted <= T0 + 1200)
        self.assertEqual(patches.tick_time(20, 1, T0, (7, 5)), patches.tick_time(20, 1, T0))  # too imprecise

    def test_region_bounds(self):
        self.assertEqual([r["name"] for r in patches.regions_for(12083, 3000, 3300, 0)], ["Falador"])
        self.assertEqual([r["name"] for r in patches.regions_for(12083, 3000, 3200, 0)], ["Port Sarim"])
        self.assertEqual(patches.regions_for(14651, 3700, 3800, 1), [])  # Fossil Island upstairs


class TrackingTests(unittest.TestCase):
    def test_ingest_status_and_notice(self):
        d = make_db()
        events = [farm(11062, {3: 3}, T0 - 30), farm(11062, {3: 32}, T0),  # Catherby herb: weeded, ranarr
                  farm(10548, {3: 32}, T0 + 78), farm(14651, {}, T0 + 100, birds=[6, 6, 3, 0])]
        account.apply_events(d, events)
        st = patches.status(d, "a", now=T0 + 1800)
        herbs = next(g for g in st["groups"] if g["kind"] == "herb")
        self.assertEqual((herbs["planted"], herbs["ready"], herbs["allReady"]), (2, 0, False))
        birds = next(g for g in st["groups"] if g["kind"] == "birdhouse")
        self.assertEqual(birds["planted"], 3)  # seeded houses have values divisible by 3; 0 is an empty space
        later = T0 + 3 * 3600
        st = patches.status(d, "a", now=later)
        self.assertTrue(next(g for g in st["groups"] if g["kind"] == "herb")["allReady"])
        notes = patches.due_notices(d, [{"acct": "a", "name": "Me"}], now=later, window=10 * 3600)
        self.assertTrue(any(m.startswith("Herbs ready: 2 patches") for _, m in notes))
        self.assertTrue(any(m.startswith("Bird houses ready") for _, m in notes))

    def test_bird_houses_lagging_varps_ignored(self):
        d = make_db()
        account.apply_events(d, [farm(14651, {}, T0, birds=[6, 6, 6, 6]), farm(14651, {}, T0 + 5, birds=[0, 0, 0, 0])])
        vals = [r["value"] for r in d.q("SELECT value FROM bird_house WHERE acct='a' ORDER BY space")]
        self.assertEqual(vals, [6, 6, 6, 6])

    def test_pace_from_plantings(self):
        d = make_db()
        regions = [10548, 11062, 12083, 14391, 6967, 4922]
        ev = []
        for k, r in enumerate(regions):
            idx = 4 if r == 4922 else 3
            ev += [farm(r, {idx: 3}, T0 + k * 90 - 20), farm(r, {idx: 32}, T0 + k * 90)]
        account.apply_events(d, ev)
        p = patches.pace(d, "a", now=T0 + 3600)
        self.assertEqual(p["patches"], {"minutes": 1.5, "n": 5})
        self.assertNotIn("trees", p)


class SlayerLogTests(unittest.TestCase):
    def test_task_log(self):
        d = make_db()

        def ev(t, count, task, original):
            return {"acct": "a", "name": "Me", "type": "slayer", "t": t * 1000, "count": count, "task": task,
                    "original": original, "points": 100, "streak": 9, "blocked": []}
        account.apply_events(d, [ev(T0, 150, "Abyssal demons", 150), ev(T0 + 600, 90, "Abyssal demons", 150),
                                 ev(T0 + 2400, 0, None, 150), ev(T0 + 2500, 120, "Dust devils", 120),
                                 ev(T0 + 2600, 30, "Kalphite", 30)])  # dust devils skipped
        rows = d.q("SELECT task, finished, done FROM slayer_task ORDER BY assigned")
        self.assertEqual([(r["task"], r["finished"]) for r in rows], [("Abyssal demons", 1), ("Dust devils", 0), ("Kalphite", 0)])
        self.assertIsNone(rows[-1]["done"])
        self.assertEqual(slayer.state(d, "a")["task"], "Kalphite")
        self.assertEqual([h["task"] for h in slayer.history(d, "a", now=T0 + 3000)], ["Abyssal demons"])


if __name__ == "__main__":
    unittest.main()
