"""Tests for the low and high cycle finder."""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import config, cycles, market  # noqa: E402

DAY = 86400
TAX = market.Tax(config.DEFAULTS, {})


def hourly(days, price_fn, start=1_790_000_000 // DAY * DAY):
    rows = []
    for k in range(days * 24):
        ts = start + k * 3600
        p = price_fn(ts)
        rows.append({"ts": ts, "ah": int(p * 1.004), "al": int(p * 0.996), "hv": 5000, "lv": 5000})
    return rows


class DailyCycleTests(unittest.TestCase):
    def test_finds_a_real_daily_cycle(self):
        rnd = random.Random(1)
        # Cheapest around 04:00 UTC, dearest around 16:00 UTC, 3% either way.
        rows = hourly(21, lambda ts: 1000 * (1 + 0.03 * -math.cos(2 * math.pi * ((ts % DAY) / 3600 - 4) / 24))
                      * (1 + rnd.gauss(0, 0.002)))
        r = cycles.analyze_daily(rows, TAX, 1, now=rows[-1]["ts"] + 1800, share=0.2, limit=10_000)
        self.assertTrue(r["ok"])
        self.assertTrue(r["passed"], r["test"])
        self.assertIn(r["low"], (2, 3, 4))  # block of 3 hours centered near 04:00
        self.assertIn((r["low"] + r["gap"]) % 24, (14, 15, 16))
        self.assertGreater(r["profit"], 0)
        self.assertGreater(r["test"]["edge"], 0.01)
        self.assertEqual(r["qty"], 3000)  # 20% of three hours of 5,000 instant sells

    def test_noise_does_not_pass(self):
        rnd = random.Random(2)
        level = [1000.0]

        def walk(ts):
            level[0] *= 1 + rnd.gauss(0, 0.004)
            return level[0]
        rows = hourly(21, walk)
        r = cycles.analyze_daily(rows, TAX, 1, now=rows[-1]["ts"])
        self.assertFalse(r.get("passed"), r.get("test"))

    def test_needs_history(self):
        self.assertFalse(cycles.analyze_daily(hourly(5, lambda ts: 1000), TAX, 1)["ok"])

    def test_timing(self):
        t = cycles._timing_daily(2, 14, 1_790_000_000 // DAY * DAY + 3 * 3600)
        self.assertTrue(t["inBuy"])
        self.assertEqual(t["nextSellIn"], 11 * 3600)


class WeeklyCycleTests(unittest.TestCase):
    def test_finds_a_weekday_pattern(self):
        rnd = random.Random(3)
        start = 1_790_000_000 // DAY * DAY
        rows = []
        for d in range(7 * 30):
            ts = start + d * DAY
            wd = (ts // DAY + 3) % 7  # 0 = Monday
            p = 1000 * (1 + {0: -0.03, 5: 0.03}.get(wd, 0)) * (1 + rnd.gauss(0, 0.003))
            rows.append({"ts": ts, "ah": int(p * 1.004), "al": int(p * 0.996), "hv": 50_000, "lv": 50_000})
        r = cycles.analyze_weekly(rows, TAX, 1, now=rows[-1]["ts"])
        self.assertTrue(r["passed"], r["test"])
        self.assertEqual((r["low"], r["high"]), (0, 5))


if __name__ == "__main__":
    unittest.main()
