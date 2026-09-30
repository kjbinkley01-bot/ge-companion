"""Tests for performance attribution, news tagging, the event study, indicators and risk."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_account import ACCT, SHARK, Base, FakeEngine, ev, offer  # noqa: E402

from geco import account, events, indicators, networth, news, risk  # noqa: E402

CFG = {"networth_value": "sell"}


class AttributionTests(Base):
    def setUp(self):
        super().setUp()
        news.init(self.db)

    def step(self, eng, t):
        account.ingest(self.db, self.folder)
        networth.record(self.db, eng, CFG, now=t)

    def test_changes_split_into_market_trading_and_loot(self):
        t = int(time.time()) - 4000
        self.write([ev(t - 10, "container", container="bank", items=[[995, 1_000_000], [SHARK, 100]])])
        self.step(FakeEngine({SHARK: (1000, 990)}), t)          # shark worth 980 after tax
        self.step(FakeEngine({SHARK: (1100, 1090)}), t + 600)   # worth 1078: market +9,800
        # Loot 50 sharks, then buy 100 on the GE at 900 each (worth 1078: trading +17,800).
        self.write([ev(t + 700, "loot", source="Zulrah", kind="NPC", items=[[SHARK, 50]]),
                    offer(t + 800, 0, "BUYING", SHARK, 900, 100, 0, 0),
                    offer(t + 900, 0, "BOUGHT", SHARK, 900, 100, 100, 90_000),
                    ev(t + 950, "container", container="bank", items=[[995, 910_000], [SHARK, 250]]),
                    ev(t + 950, "container", container="ge_collect_0", items=[])])
        self.step(FakeEngine({SHARK: (1100, 1090)}), t + 1200)
        p = networth.performance(self.db, ACCT, now=t + 1300)
        self.assertTrue(p["ok"])
        self.assertEqual(p["parts"]["mkt"], 100 * (1078 - 980))
        self.assertEqual(p["parts"]["loot"], 50 * 1078)
        self.assertEqual(p["parts"]["trade"], 100 * (1078 - 900))
        self.assertEqual(p["parts"]["other"], 0)
        self.assertEqual(p["change"], p["parts"]["mkt"] + p["parts"]["loot"] + p["parts"]["trade"])
        # The time weighted return ignores loot: only the market move and the trade count.
        start = 1_000_000 + 100 * 980
        r1 = 100 * (1078 - 980) / start
        r2 = 100 * (1078 - 900) / (start + 100 * (1078 - 980))
        self.assertAlmostEqual(p["returnPct"], (1 + r1) * (1 + r2) - 1, places=6)


class NewsTests(unittest.TestCase):
    MAPPING = {1: {"name": "Abyssal whip"}, 2: {"name": "Dragon bones"}, 3: {"name": "Shark"},
               4: {"name": "Nature rune"}}

    def test_tagging_links_and_names(self):
        tg = news.Tagger(self.MAPPING)
        found = tg.tag("New drops: [[Abyssal whip|whips]] and [[Shark]]. Dragon bones now give more XP. "
                       "A shark swims by. [[Nature_rune#Uses]]")
        self.assertEqual(set(found), {1, 2, 3, 4})
        # Single word names only match as links, not in plain text.
        self.assertNotIn(3, tg.tag("A shark swims by."))

    def test_dates_and_summary(self):
        self.assertEqual(news._parse_date("30 September 2026"), 1790726400)
        self.assertIsNone(news._parse_date("soon"))
        text = "{{Update|date=1 May 2026}}\n[[File:x.png|right]]\n''The [[Grand Exchange]] is going grander, with bigger trades!''\n==Changes=="
        self.assertEqual(news._summary(text), "The Grand Exchange is going grander, with bigger trades!")


class EventTests(unittest.TestCase):
    def prices(self, item, mkt):
        px = events.Prices.__new__(events.Prices)
        px.grid = [i * 86400 for i in range(len(item))]
        px.pos = {t: i for i, t in enumerate(px.grid)}
        px.vals = {7: item}
        px.mkt = mkt
        return px

    def test_abnormal_move_removes_the_market(self):
        item = [100.0] * 10 + [110.0] * 10
        mkt = [100.0] * 10 + [105.0] * 10
        px = self.prices(item, mkt)
        # Post on day 10: item +10%, market +5%, so the post's move is +5%.
        self.assertAlmostEqual(px.abnormal(7, 10 * 86400, -1, 7), 0.05)
        self.assertIsNone(px.abnormal(7, 18 * 86400, -1, 7))  # the window runs past today

    def test_stats_and_verdict(self):
        s = events._stats([0.02] * 20 + [0.03] * 20)
        self.assertEqual(s["n"], 40)
        self.assertEqual(events._verdict(s), "rises")
        self.assertEqual(events._verdict(events._stats([0.01, -0.01] * 20)), "no clear direction")
        self.assertEqual(events._verdict(events._stats([0.05] * 5)), "too few")


class IndicatorTests(unittest.TestCase):
    def test_moving_averages_and_rsi(self):
        v = [float(i) for i in range(1, 31)]
        self.assertEqual(indicators.sma(v, 3)[2], 2.0)
        self.assertIsNone(indicators.sma(v, 3)[1])
        self.assertAlmostEqual(indicators.ema([10.0] * 20, 5)[-1], 10.0)
        r = indicators.rsi(v)
        self.assertEqual(r[-1], 100.0)  # only gains
        down = indicators.rsi(list(reversed(v)))
        self.assertLess(down[-1], 1.0)

    def test_signals_fire_once_a_week(self):
        # A long decline then a sharp recovery: golden cross, 90 day low, then a 90 day high.
        p = [200.0 - i for i in range(100)] + [100.0 + 4 * i for i in range(60)]
        sig = indicators._signals(p)
        self.assertTrue(sig["low90"])
        self.assertTrue(sig["golden"])
        self.assertTrue(sig["high90"])
        for idx in sig.values():
            self.assertTrue(all(b - a >= 7 for a, b in zip(idx, idx[1:])))


class RiskTests(unittest.TestCase):
    def test_grid_drawdown_and_percentile(self):
        now = 100 * 86400 + 10
        series = {1: [(t * 86400, 100.0 + t) for t in range(80, 101, 2)]}
        grid, vals = risk._grid(series, 20, now)
        self.assertEqual(len(grid), 21)
        self.assertEqual(vals[1][1], 180.0)   # day 81 forward filled from day 80
        dd, at = risk._drawdown([1.0, 1.2, 0.9, 1.1])
        self.assertAlmostEqual(dd, 0.9 / 1.2 - 1)
        self.assertEqual(at, 2)
        self.assertEqual(risk._pctl([5, 1, 4, 2, 3], 0.0), 1)


if __name__ == "__main__":
    unittest.main()
