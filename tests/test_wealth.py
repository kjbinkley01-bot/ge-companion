"""Tests for fill rates, the offer coach, targets, the pump guard, goals, edge, push and server security."""
import http.client
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_account import ACCT, SHARK, WHIP, Base, FakeEngine, ev, offer  # noqa: E402

from geco import (  # noqa: E402
    account, coach, config, edge, extras, fills, guard, networth, news, push, targets, wealth,
)


class FillRateTests(Base):
    def test_share_measured_from_an_offer_and_market_volume(self):
        t0 = int(time.time()) // 300 * 300 - 3600
        self.write([offer(t0, 0, "BUYING", SHARK, 1000, 1000, 0, 0),
                    offer(t0 + 900, 0, "BUYING", SHARK, 1000, 1000, 400, 400_000),
                    offer(t0 + 1800, 0, "BOUGHT", SHARK, 1000, 1000, 1000, 1_000_000)])
        account.ingest(self.db, self.folder)
        log = self.db.q("SELECT * FROM ge_offer_log")
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["opened"], log[0]["closed"], log[0]["done"]), (t0, t0 + 1800, 1000))
        # Six 5 minute windows of 2,000 instant sells each at about the offer price: 12,000 sold.
        self.db.store_window("m5", t0, {})  # make sure the table exists
        for k in range(6):
            self.db.run("INSERT OR REPLACE INTO m5 (id, ts, ah, hv, al, lv) VALUES (?,?,?,?,?,?)",
                        (SHARK, t0 + k * 300, 1030, 500, 1005, 2000))
        m = fills.measure(self.db)
        self.assertEqual(len(m), 1)
        self.assertAlmostEqual(m[0]["share"], 1000 / 12000, places=3)
        self.assertTrue(m[0]["near"])
        r = fills.rates(self.db)
        self.assertIsNone(r["overall"])  # one offer is not enough
        pick = fills.share_for({"overall": 0.1, "items": {SHARK: {"share": 0.3, "n": 4}}}, 0.2)
        self.assertEqual(pick(SHARK), (0.3, "item"))
        self.assertEqual(pick(WHIP), (0.1, "you"))
        self.assertEqual(fills.share_for(None, 0.2)(WHIP), (0.2, "setting"))


class CoachTests(Base):
    def test_stale_buy_and_underwater(self):
        now = time.time()
        self.write([offer(now - 7200, 0, "BUYING", SHARK, 900, 5000, 0, 0)])
        account.ingest(self.db, self.folder)
        eng = FakeEngine({SHARK: (1000, 980), WHIP: (700_000, 690_000)})
        for r in eng.rows:
            r["lv24"] = r["hv24"] = 240_000
        eng.fill_rates = None
        self.db.run("INSERT INTO flips (item_id, qty, buy_price, buy_ts, source, acct) VALUES (?,?,?,?,?,?)",
                    (WHIP, 2, 800_000, int(now - 86400), "auto", ACCT))
        items = coach.check(self.db, eng, {"fill_share": 0.2}, now)
        kinds = {c["kind"]: c for c in items}
        self.assertIn("stale_buy", kinds)
        self.assertGreater(kinds["stale_buy"]["suggest"], 980)
        self.assertIn("underwater", kinds)
        self.assertLess(kinds["underwater"]["loss"], 0)


class TargetTests(Base):
    def setUp(self):
        super().setUp()
        targets.init(self.db)

    def test_ladder_splits_evenly(self):
        self.assertEqual(targets.ladder(10, 1000, [0.05, 0.10, 0.15]), [(4, 1050), (3, 1100), (3, 1150)])
        self.assertEqual(targets.ladder(4, 1000, [0.1], side="buy"), [(4, 900)])

    def test_hit_notifies_once(self):
        self.db.run("INSERT INTO targets (item_id, side, price, qty, created) VALUES (?,?,?,?,?)", (SHARK, "sell", 1000, 50, 1))
        eng = FakeEngine({SHARK: (1010, 990)})
        got = []
        self.assertEqual(targets.check(self.db, eng, lambda m, i: got.append(m)), 1)
        self.assertEqual(targets.check(self.db, eng, lambda m, i: got.append(m)), 0)
        self.assertIn("sell target", got[0])


class GuardTests(unittest.TestCase):
    def test_pump_signs_add_up(self):
        calm = {"high": 1000, "low": 990, "chg24h": 0.01, "vol24": 50000, "vol1h": 2000, "buyPressure": 0.5}
        self.assertEqual(guard.assess(calm, 0.0)[0], 0)
        pump = {"high": 13000, "low": 11000, "chg24h": 0.6, "vol24": 900, "vol1h": 400, "buyPressure": 0.95}
        score, why = guard.assess(pump, 0.01, (7000, 9000))
        self.assertGreaterEqual(score, 70)
        self.assertEqual(guard.level(score), "strong")
        self.assertTrue(any("24h" in w for w in why))


class WealthTests(Base):
    def test_projection(self):
        self.assertEqual(wealth.project(100, 100, 0.01, 0), 0)
        self.assertEqual(wealth.project(100, 121, 0.1, 0), 2)
        self.assertEqual(wealth.project(100, 130, 0.0, 10), 3)
        self.assertIsNone(wealth.project(100, 200, -0.1, 0))

    def test_sessions_from_login_and_gaps(self):
        t = time.time() - 10000
        self.write([ev(t, "login"), ev(t + 600, "xp", skill="Attack", xp=1, level=1),
                    ev(t + 1200, "logout"), ev(t + 5000, "xp", skill="Attack", xp=2, level=1),
                    ev(t + 5600, "xp", skill="Attack", xp=3, level=1)])
        account.ingest(self.db, self.folder)
        self.assertAlmostEqual(account.hours_played(self.db, int(t - 1)), (1200 + 600) / 3600, places=3)


class EdgeTests(Base):
    def test_grouping_and_tags_survive_rebuilds(self):
        rows = [{"profit": 100, "cost": 1000, "hours": 2, "roi": 0.1, "tag": "Flip"},
                {"profit": -50, "cost": 1000, "hours": 1, "roi": -0.05, "tag": "Flip"},
                {"profit": 30, "cost": 500, "hours": 10, "roi": 0.06, "tag": None}]
        g = {x["key"]: x for x in edge._group(rows, lambda r: r["tag"] or "Untagged")}
        self.assertEqual(g["Flip"]["trades"], 2)
        self.assertEqual(g["Flip"]["winRate"], 0.5)
        self.assertEqual(g["Flip"]["profit"], 50)
        now = time.time()
        self.write([offer(now - 900, 0, "BUYING", SHARK, 1000, 10, 0, 0),
                    offer(now - 800, 0, "BOUGHT", SHARK, 1000, 10, 10, 10_000)])
        account.ingest(self.db, self.folder)
        tax = FakeEngine({}).tax
        account.sync_flips(self.db, tax)
        row = self.db.one("SELECT * FROM flips WHERE source='auto'")
        edge.set_tag(self.db, row, "Merch")
        account.sync_flips(self.db, tax)
        self.assertEqual(self.db.one("SELECT tag FROM flips WHERE source='auto'")["tag"], "Merch")


class PushTests(unittest.TestCase):
    def test_enabled_needs_a_service_and_the_event(self):
        self.assertFalse(push.enabled({}, "alerts"))
        cfg = {"push_ntfy_topic": "x", "push_events": {"fills": False, "alerts": True}}
        self.assertTrue(push.enabled(cfg, "alerts"))
        self.assertFalse(push.enabled(cfg, "fills"))


class ExtrasTests(Base):
    def setUp(self):
        super().setUp()
        extras.init(self.db)

    def test_custom_categories_and_clues(self):
        with self.assertRaises(ValueError):
            extras.add_category(self.db, "One", [SHARK])
        extras.add_category(self.db, "Mine", [SHARK, WHIP, SHARK])
        self.assertEqual(extras.custom_categories(self.db)[0]["items"], sorted([SHARK, WHIP]))
        now = time.time()
        self.write([ev(now - 60, "loot", source="Clue Scroll (Hard)", kind="EVENT", items=[[WHIP, 1]]),
                    ev(now - 30, "loot", source="Zulrah", kind="NPC", items=[[SHARK, 5]])])
        account.ingest(self.db, self.folder)
        eng = FakeEngine({WHIP: (800_000, 790_000), SHARK: (1000, 990)})
        tiers = extras.clues(self.db, eng, networth.Valuer(eng))
        self.assertEqual([t["tier"] for t in tiers], ["Hard"])
        self.assertEqual(tiers[0]["caskets"], 1)


class ServerSecurityTests(Base):
    """The local server refuses foreign Host headers and cross site writes."""

    def setUp(self):
        super().setUp()
        from geco import server

        class E:
            lock = threading.Lock()
            mapping, rows, by_id, status = {}, [], {}, {}

        cfg = dict(config.DEFAULTS)
        self.httpd = server.serve(server.App(cfg, E(), self.db, None), 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, method, path, headers):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=b"{}" if method == "POST" else None, headers=headers)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def get_json(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        body = json.loads(r.read())
        c.close()
        return body

    def test_recent_orders_and_sparks(self):
        now = time.time()
        self.write([offer(now - 900, 0, "BUYING", SHARK, 1000, 10, 0, 0),
                    offer(now - 800, 0, "BOUGHT", SHARK, 1000, 10, 10, 10_000),
                    offer(now - 700, 1, "SELLING", WHIP, 900_000, 2, 0, 0),
                    offer(now - 600, 2, "BUYING", SHARK, 990, 50, 0, 0),
                    offer(now - 500, 2, "BUYING", SHARK, 990, 50, 20, 19_800),
                    offer(now - 400, 2, "CANCELLED_BUY", SHARK, 990, 50, 20, 19_800)])
        account.ingest(self.db, self.folder)
        orders = self.get_json("/api/account/offers")["orders"]
        self.assertEqual(orders[0]["status"], "Working")
        self.assertEqual((orders[0]["item"], orders[0]["side"]), (WHIP, "sell"))
        self.assertEqual({o["status"] for o in orders[1:]}, {"Filled", "Partial"})
        self.db.store_window("h1", int(now) // 3600 * 3600 - 3600, {SHARK: {"avgHighPrice": 1010, "avgLowPrice": 990, "highPriceVolume": 5, "lowPriceVolume": 5}})
        sp = self.get_json(f"/api/sparks?ids={SHARK},{WHIP}")["sparks"]
        self.assertEqual(sp[str(SHARK)][0][1], 1000)

    def test_host_and_origin_checks(self):
        ok_host = {"Host": f"127.0.0.1:{self.port}"}
        self.assertNotEqual(self.req("GET", "/api/unknown", ok_host), 403)
        self.assertEqual(self.req("GET", "/api/settings", {"Host": "evil.example"}), 403)
        self.assertEqual(self.req("POST", "/api/watchlist", dict(ok_host, Origin="https://evil.example",
                                                                 **{"Content-Type": "application/json"})), 403)


class PanelTests(Base):
    """The RuneLite side panel's one request."""

    def test_panel_payload(self):
        from geco import panel
        targets.init(self.db)
        now = time.time()
        self.write([ev(now - 4000, "login"),
                    ev(now - 3900, "container", container="inventory", items=[[995, 3_000_000], [SHARK, 100]]),
                    offer(now - 3800, 1, "BUYING", SHARK, 990, 100, 0, 0),
                    offer(now - 3700, 1, "BOUGHT", SHARK, 990, 100, 100, 99_000),
                    offer(now - 3600, 0, "BUYING", SHARK, 950, 1000, 0, 0)])
        account.ingest(self.db, self.folder)
        eng = FakeEngine({SHARK: (1100, 980), WHIP: (750_000, 690_000)})
        eng.lock = threading.Lock()
        eng.status = {"last_latest": now}
        for r in eng.rows:
            r.update(profit=r["high"] - eng.tax(r["high"], r["id"]) - r["low"], vol24=50_000, adj4h=1000, lv24=1000, hv24=1000)
        eng.fill_rates = None

        class App:
            pass
        app = App()
        app.engine, app.db, app.cfg = eng, self.db, dict(config.DEFAULTS)
        d = panel.build(app, acct=ACCT, item=SHARK, slot_items=[SHARK], now=now)
        self.assertEqual(d["header"]["acctName"], "Zezima")
        self.assertTrue(d["header"]["live"])
        self.assertEqual(d["prices"][str(SHARK)]["low"], 980)
        self.assertEqual(d["slotNotes"]["0"]["kind"], "stale_buy")
        self.assertEqual(d["item"]["held"], 200)  # 100 in the inventory plus 100 bought, waiting in the GE
        self.assertGreater(d["item"]["breakeven"], 990)
        self.assertEqual([r["id"] for r in d["ideas"]["rows"]], [SHARK])  # the whip costs more than the cash on hand
        self.assertEqual(d["notifications"], [])  # first call only learns the latest id
        self.assertIn(str(SHARK), d["costs"])
        self.assertEqual(panel.build(app, acct="unknown", now=now)["header"]["acctName"], "All accounts")


class DesktopTests(unittest.TestCase):
    def test_running_and_autostart_command(self):
        from geco import desktop
        self.assertFalse(desktop.running(1, timeout=0.2))
        cmd = desktop.autostart_command()
        self.assertIn("run.py", cmd)
        self.assertTrue(cmd.endswith("--background"))
        self.assertTrue(os.path.exists(desktop.ICON))


if __name__ == "__main__":
    unittest.main()
