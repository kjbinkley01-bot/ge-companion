"""Unit tests for GE Companion's math. Standard library only:

    python -m unittest discover -s tests
"""
import json
import math
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geco import analytics, categories, config, features, forecast, market, recipes  # noqa: E402
from geco.db import DB, wprice  # noqa: E402

MAPPING = {
    1: {"id": 1, "name": "Widget", "limit": 100, "highalch": 0, "members": True},
    2: {"id": 2, "name": "Old school bond", "limit": 100, "highalch": 0, "members": False},
    3: {"id": 3, "name": "Part A", "limit": 8, "members": True},
    4: {"id": 4, "name": "Part B", "limit": 8, "members": True},
    5: {"id": 5, "name": "Gadget set", "limit": 8, "members": True},
}


def tax():
    return market.Tax(config.DEFAULTS, MAPPING)


class TempDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.dir.name, "t.sqlite3"))

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()


class TaxTests(unittest.TestCase):
    def test_rules(self):
        t = tax()
        self.assertEqual(t(49), 0)          # under 50 gp pays nothing
        self.assertEqual(t(50), 1)
        self.assertEqual(t(1000), 20)
        self.assertEqual(t(999), 19)        # rounds down
        self.assertEqual(t(1_000_000_000), 5_000_000)  # capped
        self.assertEqual(t(10_000_000, 2), 0)          # exempt by name

    def test_breakeven_is_minimal(self):
        t = tax()
        for buy in (1, 49, 50, 99, 1000, 12345, 815_000, 300_000_000):
            s = t.breakeven(buy)
            self.assertGreaterEqual(s - t(s), buy)
            self.assertLess((s - 1) - t(s - 1), buy)


class FillAndMarketTests(unittest.TestCase):
    def test_fill_uses_sides_and_share(self):
        buy, sell, qty, hrs = market.fill_model(100, lv24=2400, hv24=4800, lv1h=0, hv1h=0, share=0.5)
        self.assertEqual(buy, 50)       # 0.5 * 2400 / 24
        self.assertEqual(sell, 100)
        self.assertEqual(qty, 100)      # min(limit, 4h at the slower side = 200)
        self.assertAlmostEqual(hrs, 2.0)

    def test_fill_falls_back_to_last_hour(self):
        _, _, qty, _ = market.fill_model(None, 0, 0, lv1h=10, hv1h=40, share=0.2)
        self.assertEqual(qty, 8)        # 0.2 * 10 * 4

    def _row(self, latest, m5=None, windows=0, cfg=None):
        c = dict(config.DEFAULTS, **(cfg or {}))
        rows = market.build_market({1: MAPPING[1]}, {1: latest}, {}, {1: {"hv24": 2400, "lv24": 2400, "vol24": 4800}},
                                   tax(), c, m5, windows, now=latest["highTime"] + 10)
        return rows[0]

    def test_trap_when_prices_far_apart_in_time(self):
        now = 1_000_000
        r = self._row({"high": 1100, "low": 1000, "highTime": now, "lowTime": now - 3600})
        self.assertTrue(r["trap"])
        r = self._row({"high": 1100, "low": 1000, "highTime": now, "lowTime": now - 60})
        self.assertFalse(r["trap"])

    def test_stability_and_adjusted(self):
        now = 1_000_000
        m5 = {1: {"traded": 12, "both": 12, "held": 9, "held_raw": 12, "avg_margin": 40.0,
                  "avg_margin_raw": 60.0, "cv": 0.01, "hv": 1, "lv": 1}}
        r = self._row({"high": 1100, "low": 1000, "highTime": now, "lowTime": now - 60}, m5, 12)
        self.assertAlmostEqual(r["stability"], 0.75)
        self.assertEqual(r["profit"], 1100 - 22 - 1000)
        self.assertEqual(r["adj4h"], int(r["est4h"] * 0.75))

    def test_trap_when_history_shows_no_margin(self):
        now = 1_000_000
        m5 = {1: {"traded": 12, "both": 12, "held": 0, "held_raw": 0, "avg_margin": -5.0,
                  "avg_margin_raw": 0.0, "cv": 0.01, "hv": 1, "lv": 1}}
        r = self._row({"high": 1100, "low": 1000, "highTime": now, "lowTime": now - 60}, m5, 12)
        self.assertTrue(r["trap"])


class DecantTests(unittest.TestCase):
    def test_skips_empty_and_jewellery(self):
        mapping = {10: {"name": "Waterskin(0)", "limit": 100}, 11: {"name": "Waterskin(1)", "limit": 100},
                   12: {"name": "Waterskin(4)", "limit": 100},
                   20: {"name": "Prayer potion(1)", "limit": 2000}, 21: {"name": "Prayer potion(4)", "limit": 2000},
                   30: {"name": "Amulet of glory(1)", "limit": 10}, 31: {"name": "Amulet of glory(4)", "limit": 10}}
        latest = {i: {"high": 1000 * (1 + i % 10), "low": 900 * (1 + i % 10)} for i in mapping}
        latest[20] = {"high": 2000, "low": 1500}
        latest[21] = {"high": 10000, "low": 9500}
        out = market.decant_opportunities(mapping, latest, market.Tax(config.DEFAULTS, mapping))
        self.assertEqual([o["name"] for o in out], ["Prayer potion"])
        self.assertEqual(out[0]["best"]["dose"], 1)
        self.assertEqual(out[0]["best"]["profitPer4"], 10000 - 200 - 1500 * 4)


class AlertTests(unittest.TestCase):
    ROW = {"name": "Widget", "low": 900, "high": 1000, "profit": 80, "roi": 0.089, "chg1h": -0.1,
           "vol1h": 500, "stability": 0.8, "signal": "dump"}

    def test_single_and_multi_conditions(self):
        a = {"kind": "price_below", "threshold": 950, "extra": None}
        self.assertIn("instant-sell hit 900", market.check_alert(a, self.ROW))
        a["extra"] = json.dumps([{"kind": "drop_pct", "threshold": 5}, {"kind": "stability_above", "threshold": 70}])
        msg = market.check_alert(a, self.ROW)
        self.assertIn("fell 10.0%", msg)
        self.assertIn("80%", msg)
        a["extra"] = json.dumps([{"kind": "rise_pct", "threshold": 5}])
        self.assertIsNone(market.check_alert(a, self.ROW))

    def test_dump_and_volume(self):
        self.assertIsNotNone(market.check_alert({"kind": "dump", "threshold": 0}, self.ROW))
        self.assertIsNone(market.check_alert({"kind": "vol_above", "threshold": 501}, self.ROW))


class LimitTests(TempDB):
    def test_windows(self):
        w = features.limit_windows([(0, 10), (3600, 5), (4 * 3600 + 1, 7), (5 * 3600, 1)])
        self.assertEqual(w, [[0, 15], [4 * 3600 + 1, 8]])

    def test_buy_limits_and_resets(self):
        now = int(time.time())
        # Window from 5h ago has closed; a new one started 1h ago.
        for ts, q in ((now - 5 * 3600, 30), (now - 3 * 3600, 20), (now - 3600, 40)):
            self.db.run("INSERT INTO flips (item_id, qty, buy_price, buy_ts) VALUES (1, ?, 10, ?)", (q, ts))
        lim = features.buy_limits(self.db, MAPPING, now)[1]
        self.assertEqual(lim["used"], 40)
        self.assertEqual(lim["left"], 60)
        self.assertEqual(lim["resetAt"], now - 3600 + 4 * 3600)
        resets = features.limit_resets(self.db, now - 2 * 3600, now)
        self.assertEqual(resets, [(1, 50, now - 5 * 3600 + 4 * 3600)])


class HistoryStatsTests(TempDB):
    def _reference(self, series, n):
        """The v1 pure Python calculation the SQL replaced."""
        def rng(end, hours):
            num = den = 0
            for k in range(hours):
                row = series.get(end - k * 3600)
                p = wprice(row) if row else None
                if p is not None:
                    v = row["hv"] + row["lv"]
                    num += p * v
                    den += v
            return num / den if den else None
        vols = [(series[n - k * 3600]["hv"] + series[n - k * 3600]["lv"]) if (n - k * 3600) in series else 0
                for k in range(24)]
        return {"vol24": sum(vols), "avg_hourly_prev": sum(vols[1:]) / 23, "p_now": rng(n, 1),
                "p_1h": rng(n - 3600, 1), "p_6h": rng(n - 6 * 3600, 2), "p_24h": rng(n - 24 * 3600, 3),
                "p_7d": rng(n - 7 * 86400, 6)}

    def test_sql_matches_reference(self):
        n = 1_700_000_000 // 3600 * 3600
        series = {}
        for k in range(0, 8 * 24 + 6):
            if k % 7 == 3:
                continue  # gaps
            ts = n - k * 3600
            ah = 1000 + (k * 37) % 91 if k % 11 else None
            row = {"ah": ah, "hv": 10 + k % 5, "al": 950 + (k * 13) % 41, "lv": 5 + k % 3}
            series[ts] = row
            self.db.store_window("h1", ts, {"1": {"avgHighPrice": row["ah"], "highPriceVolume": row["hv"],
                                                  "avgLowPrice": row["al"], "lowPriceVolume": row["lv"]}})
        stats, newest = self.db.hourly_stats(n)
        self.assertEqual(newest, n)
        ref = self._reference(series, n)
        for k, v in ref.items():
            if v is None:
                self.assertIsNone(stats[1][k], k)
            else:
                self.assertAlmostEqual(stats[1][k], v, places=6, msg=k)

    def test_m5_stability(self):
        base = 1_700_000_000 // 300 * 300
        for k in range(10):
            ah, al = (1100, 1000) if k < 6 else (1010, 1000)  # 2% tax kills the last four margins
            self.db.store_window("m5", base + k * 300, {"1": {"avgHighPrice": ah, "highPriceVolume": 3,
                                                              "avgLowPrice": al, "lowPriceVolume": 3}})
        st = self.db.m5_stats(base, 200, 5_000_000)[1]
        self.assertEqual(st["held"], 6)
        self.assertEqual(st["held_raw"], 10)
        self.assertEqual(self.db.snapshot_count("m5", base), 10)


class RecipeTests(unittest.TestCase):
    def pricer(self, latest):
        return recipes.Pricer(MAPPING, latest, tax(), stats={i: {"vol24": 1000} for i in MAPPING},
                              stale_s=1200, now=10_000)

    def test_evaluate_prices_and_limits(self):
        latest = {1: {"high": 1000, "highTime": 9_990, "low": 900, "lowTime": 9_990},
                  3: {"high": 500, "highTime": 9_990, "low": 400, "lowTime": 9_990}}
        rec = recipes.R("Make widget", "Crafting", 1, 10, [("Part A", 2)], [("Widget", 1)], 100, coins=5)
        ev = recipes.evaluate(rec, self.pricer(latest), patient=True)
        self.assertEqual(ev["cost"], 2 * 400 + 5)
        self.assertEqual(ev["revenue"], 1000 - 20)
        self.assertEqual(ev["profit"], 980 - 805)
        self.assertEqual(ev["limitPerHour"], 1.0)   # 8 parts per 4h, 2 per action
        self.assertTrue(ev["limited"])
        self.assertAlmostEqual(ev["gpHr"], ev["profit"] * 1.0)
        self.assertFalse(ev["stale"])

    def test_stale_and_unknown(self):
        latest = {1: {"high": 1000, "highTime": 1, "low": 900, "lowTime": 1},
                  3: {"high": 500, "highTime": 9_990, "low": 400, "lowTime": 9_990}}
        rec = recipes.R("X", "Crafting", 1, 0, [("Part A", 1)], [("Widget", 1)], 100)
        self.assertTrue(recipes.evaluate(rec, self.pricer(latest))["stale"])
        rec = recipes.R("Y", "Crafting", 1, 0, [("No such item", 1)], [("Widget", 1)], 100)
        self.assertIsNone(recipes.evaluate(rec, self.pricer(latest)))

    def test_all_builtin_recipes_are_well_formed(self):
        for r in recipes.PROCESSING:
            self.assertTrue(r["inputs"], r["name"])
            self.assertGreater(r["per_hour"], 0, r["name"])
        names = [s for s, _ in recipes.SETS]
        self.assertEqual(len(names), len(set(names)))


class AnalyticsTests(TempDB):
    def test_index_base_and_cap(self):
        grid = [0, 3600, 7200]
        data = {
            1: [{"bt": 0, "num": 100 * 1000, "den": 1000}, {"bt": 7200, "num": 110 * 1000, "den": 1000}],
            2: [{"bt": 0, "num": 10 * 10, "den": 10}, {"bt": 3600, "num": 20 * 10, "den": 10}],
        }
        series, members = analytics.compute_index(data, grid, cap_share=0.5)
        # Two items with a 50% cap can only be weighted equally.
        self.assertEqual(series[0]["v"], 100.0)
        # End: (0.5 * 1.1 + 0.5 * 2.0) * 100.
        self.assertAlmostEqual(series[-1]["v"], 155.0, places=3)
        # Gaps forward fill: at 3600 item 1 is still at its base.
        self.assertAlmostEqual(series[1]["v"], 150.0, places=3)

    def test_seasonality_finds_cheap_hour(self):
        now = time.time()
        start = int(now // 86400 * 86400 - 10 * 86400)
        for ts in range(start, int(now) - 3600, 3600):
            hr = time.localtime(ts).tm_hour
            p = 1000 - (30 if hr == 4 else 0) + (30 if hr == 16 else 0)
            self.db.store_window("h1", ts, {"1": {"avgHighPrice": p, "highPriceVolume": 5,
                                                  "avgLowPrice": p, "lowPriceVolume": 5}})
        s = analytics.seasonality(self.db, 1, days=10, now=now)
        self.assertEqual(s["bestBuyHour"], 4)
        self.assertEqual(s["bestSellHour"], 16)

    def test_backtest_dip(self):
        now = time.time()
        n = int(now // 3600 * 3600)
        start = n - 4 * 86400
        rows = {}
        for ts in range(start, n, 3600):
            k = (ts - start) // 3600
            p = 1000
            if 50 <= k <= 52:
                p = 800   # the dip lasts a few hours
            elif k > 52:
                p = 1100  # then recovers
            rows[ts] = p
            self.db.store_window("h1", ts, {"1": {"avgHighPrice": p + 20, "highPriceVolume": 50,
                                                  "avgLowPrice": p, "lowPriceVolume": 50}})
        by_id = {1: {"id": 1, "name": "Widget", "high": 1120, "low": 1100, "vol24": 2400, "members": True}}
        r = analytics.backtest(self.db, MAPPING, by_id, tax(),
                               {"strategy": "dip", "days": 3, "threshold": 10, "hold": 2, "lookback": 24,
                                "minVol": 0}, now=now)
        self.assertEqual(r["summary"]["trades"], 1)
        t = r["trades"][0]
        self.assertEqual(t["buy"], 800)             # next hour's instant-sell average
        self.assertEqual(t["sell"], 1120)           # instant-buy average after the 2 hour hold
        self.assertAlmostEqual(t["ret"], (1120 - 22) / 800 - 1)


class CapTests(unittest.TestCase):
    def test_cap_holds_in_final_weights(self):
        ws = [1000, 50, 40, 30, 20, 10, 5]
        out = analytics.cap_weights(ws, 0.2)
        total = sum(out)
        self.assertLessEqual(max(out) / total, 0.2 + 1e-9)
        # The top three end up equal at 32.5 (= 0.2 of the new total); the rest are untouched.
        for got, want in zip(out, [32.5, 32.5, 32.5, 30, 20, 10, 5]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(analytics.cap_weights([5, 5, 5], 0.2), [1.0, 1.0, 1.0])  # cap impossible
        self.assertEqual(analytics.cap_weights([10, 9, 8, 7, 6, 5], 0.5), [10, 9, 8, 7, 6, 5])  # no cap needed


class FlipAndBossTests(TempDB):
    class Eng:
        def __init__(self):
            self.mapping = MAPPING
            self.by_id = {1: {"high": 1000, "low": 900}}
            self.tax = tax()

    def test_flip_summary_equity_and_drawdown(self):
        rows = [
            {"fid": 1, "open": False, "profit": 100, "sell_ts": 10, "buy_ts": 1, "item_id": 1, "name": "W",
             "roi": 0.1, "taxEach": 1, "qty": 1, "cost": 1000, "hours": 0.5},
            {"fid": 2, "open": False, "profit": -300, "sell_ts": 20, "buy_ts": 11, "item_id": 1, "name": "W",
             "roi": -0.3, "taxEach": 1, "qty": 1, "cost": 1000, "hours": 5},
            {"fid": 3, "open": False, "profit": 50, "sell_ts": 30, "buy_ts": 21, "item_id": 1, "name": "W",
             "roi": 0.05, "taxEach": 1, "qty": 1, "cost": 1000, "hours": 30},
        ]
        s = features.flip_summary(rows)
        self.assertEqual([p["v"] for p in s["equity"]], [100, -200, -150])
        self.assertEqual(s["maxDrawdown"], 300)
        self.assertEqual(s["realized"], -150)
        self.assertEqual([h["flips"] for h in s["holdTimes"]], [1, 0, 1, 0, 1, 0])

    def test_dry_streak(self):
        snap = {"skills": [], "activities": [{"name": "Zulrah", "score": 1000}]}
        self.db.add_hiscore_snap("Me", "normal", snap)
        self.db.run("INSERT INTO chase (player, mode, boss, item_name, rate_n, start_kc) VALUES "
                    "('me','normal','Zulrah','Fang',100,500)")
        self.db.run("INSERT INTO drops (player, mode, boss, item_name, kc, ts) VALUES "
                    "('me','normal','Zulrah','Fang',800,1)")
        c = features.boss_view(self.db, self.Eng(), "Me", "normal")["chases"][0]
        self.assertEqual(c["dry"], 200)
        self.assertAlmostEqual(c["chanceDry"], 0.99 ** 200)

    def test_parse_holdings(self):
        by_name = {"abyssal whip": 1, "shark": 2, "nature rune": 3}
        parsed, bad = features.parse_holdings(
            "2 x Abyssal whip\nShark, 500, 950\nNature rune 10k\nshark\nFoo 3\n", by_name)
        self.assertEqual(parsed[0], {"item_id": 1, "qty": 2, "cost_each": None})
        self.assertEqual(parsed[1], {"item_id": 2, "qty": 500, "cost_each": 950})
        self.assertEqual(parsed[2]["qty"], 10000)
        self.assertEqual(parsed[3]["qty"], 1)
        self.assertEqual(bad, ["Foo 3"])


class ForecastTests(TempDB):
    def _store(self, days, price_fn, hv=240, lv=240):
        now = time.time()
        start = int(now // 86400 * 86400 - days * 86400)
        for ts in range(start, start + days * 86400, 3600):
            p = price_fn((ts - start) / 86400.0)
            self.db.store_window("h1", ts, {"1": {"avgHighPrice": p * 1.05, "highPriceVolume": hv // 24,
                                                  "avgLowPrice": p, "lowPriceVolume": lv // 24}})
        return now

    def test_daily_series_and_margin(self):
        self._store(5, lambda d: 1000)
        days = forecast.daily_series(self.db.history("h1", 1, 0), tax(), 1)
        self.assertEqual(len(days), 5)
        self.assertAlmostEqual(days[0]["roi"], (1050 - 21 - 1000) / 1000)
        self.assertEqual(days[0]["held"], 1.0)

    def test_trend_detected_and_checked(self):
        now = self._store(30, lambda d: 1000 * (1.01 ** d))
        row = {"high": 1400, "low": 1340, "roi": 0.02}
        f = forecast.item_forecast(self.db, 1, row, MAPPING, tax(), horizon=10, share=0.5, windows=2, now=now)
        self.assertTrue(f["ok"])
        self.assertTrue(f["summary"]["useTrend"])
        # Rising about 1% a day; the damped trend projects less than a straight line (10.5%).
        self.assertGreater(f["summary"]["priceChange"], 0.03)
        self.assertLess(f["summary"]["priceChange"], 0.105)
        self.assertLess(f["summary"]["check"]["model"], f["summary"]["check"]["naive"])

    def test_flat_market_profit_matches_demand_and_margin(self):
        now = self._store(20, lambda d: 1000 + 5 * math.sin(d * 7))
        row = {"high": 1050, "low": 1000, "roi": 0.029}
        f = forecast.item_forecast(self.db, 1, row, MAPPING, tax(), horizon=10, share=0.5, windows=2, now=now)
        s = f["summary"]
        self.assertEqual(s["dailyQty"], 120)        # min(limit 100 x 2 windows, 50% of 240 a side)
        # Margin stays about 2.95% after tax on the live mid price (1025), 120 a day for 10 days.
        self.assertAlmostEqual(s["profit"], 0.0295 * 1025 * 120 * 10, delta=500)
        self.assertLessEqual(s["p10"], s["p50"])
        self.assertLessEqual(s["p50"], s["p90"])

    def test_live_margin_fades(self):
        m = {"histRoi": 0.01, "liveRoi": 0.05}
        self.assertAlmostEqual(forecast._roi_on_day(m, 0), 0.05)
        self.assertAlmostEqual(forecast._roi_on_day(m, 1), 0.03)
        self.assertAlmostEqual(forecast._roi_on_day(m, 10), 0.01, places=3)

    def test_trap_live_margin_ignored_and_no_chasing(self):
        now = self._store(10, lambda d: 1000, hv=240, lv=240)
        # History has a steady positive margin; a trapped live row must not change the forecast.
        clean = forecast.item_forecast(self.db, 1, {"high": 1050, "low": 1000, "roi": 0.03}, MAPPING, tax(),
                                       horizon=5, now=now)
        trap = forecast.item_forecast(self.db, 1, {"high": 1050, "low": 1000, "roi": 0.5, "trap": True},
                                      MAPPING, tax(), horizon=5, now=now)
        self.assertLess(trap["summary"]["profit"], clean["summary"]["profit"] * 1.2)
        # A negative usual margin: the simulated trader does not keep flipping into losses.
        self.db.conn.execute("DELETE FROM h1")
        now = self._store(10, lambda d: 1000 - 0.1 * d)
        self.db.conn.execute("UPDATE h1 SET ah = al + 5")
        f = forecast.item_forecast(self.db, 1, {"high": 1005, "low": 1000, "roi": -0.015}, MAPPING, tax(),
                                   horizon=10, now=now)
        self.assertEqual(f["summary"]["p50"], 0)
        self.assertEqual(f["summary"]["lossChance"], 0)

    def test_needs_history(self):
        now = self._store(2, lambda d: 1000)
        f = forecast.item_forecast(self.db, 1, None, MAPPING, tax(), now=now)
        self.assertFalse(f["ok"])


class CategoryTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(categories.classify("Nature rune"), "runes")
        self.assertIsNone(categories.classify("Rune platebody"))
        self.assertEqual(categories.classify("Prayer potion(4)"), "potions")
        self.assertNotEqual(categories.classify("Amulet of glory(4)"), "potions")
        self.assertEqual(categories.classify("Grimy ranarr weed"), "herbs")
        self.assertEqual(categories.classify("Runite bar"), "ores")
        self.assertEqual(categories.classify("Magic logs"), "logs")


class PearsonTest(unittest.TestCase):
    def test_pearson(self):
        self.assertAlmostEqual(analytics._pearson([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertAlmostEqual(analytics._pearson([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(analytics._pearson([1, 1, 1], [1, 2, 3]))
        self.assertTrue(math.isfinite(analytics._pearson([1, 2, 4, 3], [1, 3, 2, 5])))


if __name__ == "__main__":
    unittest.main()
