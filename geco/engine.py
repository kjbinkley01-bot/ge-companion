"""Background poller and in-memory market state."""
import json
import logging
import os
import threading
import time

from . import account, analytics, brief, coach, edge, extras, fills, news, push, targets, wealth, features, forecast, forecast_eval, hold, market, networth, recipes
from .config import DATA_DIR

log = logging.getLogger("geco")


class Engine:
    def __init__(self, cfg, client, db):
        self.cfg = cfg
        self.client = client
        self.db = db
        self.lock = threading.RLock()
        self.mapping = {}
        self.latest = {}
        self.h1_data = {}
        self.stats = {}
        self.m5stats = {}
        self.m5_windows = 0
        self.rows = []
        self.by_id = {}
        self.tax = market.Tax(cfg, {})
        self._cooldowns = {}  # (alert id, item id) -> last fired, for watchlist-wide alerts
        self._limit_check = time.time()
        self.status = {"started": int(time.time()), "last_latest": None, "last_5m": None,
                       "last_1h": None, "backfill": "pending", "errors": [],
                       "import": "idle", "tune": "idle", "holdTrain": "idle"}
        self._stop = threading.Event()
        self._import_lock = threading.Lock()
        self.forecast_params = forecast.load_params(DATA_DIR)
        self.params_version = 0
        self.hold_model = hold.load(DATA_DIR)
        account.init(db)
        networth.init(db)
        news.init(db)
        for mod in (wealth, edge, targets, extras):
            mod.init(db)
        self.demo_feed = None
        self.fill_rates = None
        self._notified = {}
        self._last_coach = 0
        self._rates_dirty = False
        self._demo_seeded = False
        self.live = {"events": 0, "last": None, "lastIngest": None, "folder": None}
        self._worth_at = 0

    # Status helpers -------------------------------------------------------------
    def _err(self, where, e):
        msg = f"{time.strftime('%H:%M:%S')} {where}: {e}"
        log.warning(msg)
        with self.lock:
            self.status["errors"] = (self.status["errors"] + [msg])[-8:]

    # Loading --------------------------------------------------------------------
    def load_mapping(self, force=False):
        name = "mapping_demo.json" if getattr(self.client, "is_demo", False) else "mapping.json"
        path = os.path.join(DATA_DIR, name)
        data = None
        fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < 12 * 3600
        if not force and fresh:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = None
        if data is None:
            try:
                data = self.client.mapping()
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception as e:  # fall back to a stale copy if we have one
                self._err("mapping", e)
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    raise
        with self.lock:
            self.mapping = {int(m["id"]): m for m in data}
            self.tax = market.Tax(self.cfg, self.mapping)
        if self.latest:
            self.rebuild()

    def refresh_latest(self):
        raw = self.client.latest()
        with self.lock:
            self.latest = {int(k): v for k, v in raw.items()}
            self.status["last_latest"] = int(time.time())
        self.rebuild()
        self.evaluate_alerts()

    def refresh_1h(self, ts=None):
        resp = self.client.one_hour(ts)
        ts = resp.get("timestamp") or ts or (int(time.time()) // 3600 * 3600 - 3600)
        data = {int(k): v for k, v in resp["data"].items()}
        if ts:
            self.db.store_window("h1", ts, resp["data"])
        return ts, data

    def refresh_5m(self):
        resp = self.client.five_min()
        ts = resp.get("timestamp") or (int(time.time()) // 300 * 300 - 300)
        if not self.db.have_snapshot("m5", ts):
            self.db.store_window("m5", ts, resp["data"])
        with self.lock:
            self.status["last_5m"] = int(time.time())
        self.refresh_m5_stats()
        self.rebuild()

    def refresh_m5_stats(self):
        """Margin stability and volatility over the last few hours of 5 minute windows."""
        hours = float(self.cfg.get("stability_hours", 6))
        since = int(time.time() - hours * 3600)
        stats = self.db.m5_stats(since, self.tax.bp, self.tax.cap)
        windows = self.db.snapshot_count("m5", since)
        with self.lock:
            self.m5stats = stats
            self.m5_windows = windows

    def refresh_stats(self):
        now_hour = int(time.time()) // 3600 * 3600
        stats, _ = self.db.hourly_stats(now_hour)
        with self.lock:
            self.stats = stats

    def backfill(self):
        """Fill gaps in saved history using the bulk window endpoints (all items per request).

        Hourly windows go newest first so the last day is usable quickly, then the 5 minute
        windows the margin stability score needs. Requests are spaced a second apart.
        """
        demo = getattr(self.client, "is_demo", False)
        hours = max(1, min(720, int(self.cfg.get("backfill_hours", 24))))
        if demo:
            hours = max(hours, 14 * 24)  # synthetic data is free, so show the full analytics
        pause = 0.0 if demo else 1.0  # be gentle with the Wiki
        now = int(time.time())
        now_hour = now // 3600 * 3600
        # A few windows past `hours` so the 24h change has a base on a fresh install.
        missing = [now_hour - k * 3600 for k in range(1, hours + 5)
                   if not self.db.have_snapshot("h1", now_hour - k * 3600)]
        m5_hours = float(self.cfg.get("stability_hours", 6))
        now_5 = now // 300 * 300
        missing5 = [now_5 - k * 300 for k in range(2, int(m5_hours * 12) + 2)
                    if not self.db.have_snapshot("m5", now_5 - k * 300)]
        jobs = [("h1", ts) for ts in sorted(missing, reverse=True)] + \
               [("m5", ts) for ts in sorted(missing5, reverse=True)]
        total = len(jobs)
        with self.lock:
            self.status["backfill"] = f"0/{total}" if jobs else "done"
        for i, (kind, ts) in enumerate(jobs):
            if self._stop.is_set():
                return
            try:
                if kind == "h1":
                    self.refresh_1h(ts)
                else:
                    resp = self.client.five_min(ts)
                    self.db.store_window("m5", resp.get("timestamp") or ts, resp["data"])
            except Exception as e:
                self._err("backfill", e)
            with self.lock:
                self.status["backfill"] = f"{i + 1}/{total}"
            if kind == "h1" and (i + 1) % 24 == 0:
                self.refresh_stats()
                self.rebuild()
            if pause:
                time.sleep(pause)
        with self.lock:
            self.status["backfill"] = "done"
        self.refresh_fill_rates()
        self.refresh_stats()
        self.refresh_m5_stats()
        self.rebuild()
        analytics.clear_cache()
        self.import_daily()

    def import_daily(self, days=None):
        """Import daily market history from the Wiki's bulk /24h windows (all items per request).

        Only missing days are fetched, newest first, one request a second, so after the first
        run this is one request a day. Used by the forecast model and its backtest.
        """
        days = int(days if days is not None else self.cfg.get("history_import_days", 365))
        days = max(0, min(1095, days))
        if not days or not self._import_lock.acquire(blocking=False):
            return
        try:
            demo = getattr(self.client, "is_demo", False)
            if demo:
                days = min(days, 200)
            today = int(time.time()) // 86400 * 86400
            missing = [today - k * 86400 for k in range(1, days + 1)
                       if not self.db.have_snapshot("d1", today - k * 86400)]
            for i, ts in enumerate(missing):
                if self._stop.is_set():
                    return
                with self.lock:
                    self.status["import"] = f"{i}/{len(missing)} days"
                try:
                    resp = self.client.one_day(ts)
                    self.db.store_window("d1", resp.get("timestamp") or ts, resp.get("data") or {})
                except Exception as e:
                    self._err("history import", e)
                if not demo:
                    time.sleep(1.0)  # be gentle with the Wiki
            if missing:
                analytics.clear_cache()
        finally:
            with self.lock:
                self.status["import"] = "done"
            self._import_lock.release()

    def tune_forecast(self, horizon=30):
        """Backtest the forecast on imported history and adopt the tuned settings."""
        with self.lock:
            if self.status["tune"].startswith("running"):
                return False
            self.status["tune"] = "running: starting"

        def say(msg):
            with self.lock:
                self.status["tune"] = "running: " + msg

        def job():
            try:
                share = float(self.cfg.get("fill_share", 0.2))
                report = forecast_eval.run(self.db, self.mapping, self.tax, horizon=horizon, share=share,
                                           progress=say, start=self.forecast_params)
                forecast_eval.save(report, DATA_DIR)
                with self.lock:
                    self.forecast_params = forecast.load_params(DATA_DIR)
                    self.params_version += 1
                    self.status["tune"] = "done"
                analytics.clear_cache()
            except Exception as e:
                self._err("forecast tuning", e)
                with self.lock:
                    self.status["tune"] = f"failed: {e}"
        threading.Thread(target=job, daemon=True, name="tune").start()
        return True

    def train_hold(self):
        """Retrain the holding outlook on your imported history (walk-forward tested first)."""
        with self.lock:
            if self.status["holdTrain"].startswith("running"):
                return False
            self.status["holdTrain"] = "running: starting"

        def say(msg):
            with self.lock:
                self.status["holdTrain"] = "running: " + msg

        def job():
            try:
                bundle = hold.train(self.db, self.tax, progress=say)
                hold.save(bundle, DATA_DIR)
                with self.lock:
                    self.hold_model = hold.load(DATA_DIR)
                    self.params_version += 1
                    self.status["holdTrain"] = "done"
                analytics.clear_cache()
            except Exception as e:
                self._err("holding model", e)
                with self.lock:
                    self.status["holdTrain"] = f"failed: {e}"
        threading.Thread(target=job, daemon=True, name="hold-train").start()
        return True

    def rebuild(self):
        with self.lock:
            share_fn = None
            if self.cfg.get("fill_share_measured", True) and self.fill_rates:
                share_fn = fills.share_for(self.fill_rates, max(0.01, min(1.0, float(self.cfg.get("fill_share", 0.2)))))
            rows = market.build_market(self.mapping, self.latest, self.h1_data, self.stats,
                                       self.tax, self.cfg, self.m5stats, self.m5_windows, share_fn=share_fn)
            self.rows = rows
            self.by_id = {r["id"]: r for r in rows}

    def pricer(self):
        """Live price lookups by item name, for the recipe engine."""
        with self.lock:
            return recipes.Pricer(self.mapping, self.latest, self.tax, self.stats,
                                  self.cfg.get("stale_minutes", 20) * 60)

    def evaluate_alerts(self):
        now = int(time.time())
        cooldown = int(self.cfg.get("alert_cooldown_minutes", 30)) * 60
        watch = None
        for a in self.db.q("SELECT * FROM alerts WHERE enabled=1"):
            if a["item_id"]:
                if a["last_fired"] and now - a["last_fired"] < cooldown:
                    continue
                targets = [a["item_id"]]
            else:
                # Item 0 means "any item on the watchlist", with a cooldown per item.
                if watch is None:
                    watch = [r["id"] for r in self.db.q("SELECT id FROM watchlist")]
                targets = [i for i in watch
                           if now - self._cooldowns.get((a["aid"], i), 0) >= cooldown]
            fired = False
            for iid in targets:
                msg = market.check_alert(a, self.by_id.get(iid))
                if not msg:
                    continue
                self.notify(msg, iid, event="alerts", alert_id=a["aid"], title="Price alert")
                self._cooldowns[(a["aid"], iid)] = now
                fired = True
                if a["once"]:
                    break
            if not fired:
                continue
            if a["once"]:
                self.db.run("UPDATE alerts SET last_fired=?, enabled=0 WHERE aid=?", (now, a["aid"]))
            else:
                self.db.run("UPDATE alerts SET last_fired=? WHERE aid=?", (now, a["aid"]))
        self.check_limit_resets(now)

    def check_limit_resets(self, now=None):
        """Tell the user when a buy limit window from the flip log has reset."""
        now = now or int(time.time())
        since, self._limit_check = self._limit_check, now
        if not self.cfg.get("notify_limit_reset", True):
            return
        for iid, used, _ in features.limit_resets(self.db, since, now):
            name = self.mapping.get(iid, {}).get("name", f"Item {iid}")
            self.notify(f"{name} buy limit has reset (you bought {used:,} last window)", iid, event="limits",
                        title="Buy limit reset")

    def notify(self, message, item_id=None, event=None, key=None, cooldown=0, alert_id=None, title="Bankstanding"):
        """Add an in-app notification and send it to your phone if that event is switched on."""
        now = time.time()
        if key is not None:
            if now - self._notified.get(key, 0) < cooldown:
                return False
            self._notified[key] = now
        self.db.run("INSERT INTO notifications (ts, alert_id, item_id, message) VALUES (?,?,?,?)",
                    (int(now), alert_id, item_id, message))
        if event:
            push.send(self.cfg, title, message, event=event)
        return True

    def live_checks(self, since):
        """After new plugin events: finished offers, then the offer coach (both notify)."""
        for o in self.db.q("SELECT * FROM ge_offer_log WHERE closed>? AND state IN ('BOUGHT', 'SOLD') "
                           "AND caught_up=0", (since,)):
            self._rates_dirty = True
            name = (self.mapping.get(o["item"]) or {}).get("name") or f"Item {o['item']}"
            verb = "Bought" if o["side"] == "buy" else "Sold"
            self.notify(f"{verb} {o['done']:,} {name} at {o['price']:,} (slot {o['slot'] + 1})", o["item"],
                        event="fills", key=("done", o["acct"], o["slot"], o["opened"]), cooldown=10 ** 9,
                        title="GE offer complete")

    def target_checks(self):
        try:
            targets.check(self.db, self, lambda msg, iid: self.notify(msg, iid, event="alerts", title="Price target"))
        except Exception as e:
            self._err("price targets", e)

    def coach_checks(self):
        for c in coach.check(self.db, self, self.cfg):
            if c["severity"] != "warn":
                continue
            key = ("coach", c["kind"], c.get("acct"), c.get("slot"), c["item"], c.get("price"))
            self.notify(c["title"] + ". " + c["detail"], c["item"], event="coach", key=key, cooldown=6 * 3600,
                        title="Offer coach")

    def statement_check(self, now=None):
        """Send the daily bank statement to your phone at the chosen time (once a day)."""
        if not push.enabled(self.cfg, "statement"):
            return
        now = now or time.time()
        hh, mm = (self.cfg.get("statement_time") or "08:00").split(":")
        lt = time.localtime(now)
        due = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(hh), int(mm), 0, 0, 0, -1))
        today = time.strftime("%Y-%m-%d", lt)
        if now < due or self.db.kv_get("statement_sent") == today:
            return
        self.db.kv_set("statement_sent", today)
        st = brief.statement(self.db, self, self.cfg, since=now - 86400, now=now)
        push.send(self.cfg, "Your bank statement", brief.text(st), event="statement")

    def refresh_fill_rates(self):
        """Measure your real fill share from logged GE offers (used by the flip model)."""
        try:
            self.fill_rates = fills.rates(self.db)
        except Exception as e:
            self._err("fill rates", e)

    def hourly_jobs(self):
        """Net worth snapshot, yesterday's daily history window and a daily database backup."""
        threading.Thread(target=self.import_daily, daemon=True, name="import").start()
        self.refresh_fill_rates()
        try:
            features.networth_snapshot(self.db, self)
        except Exception as e:
            self._err("networth", e)
        days = int(self.cfg.get("auto_backup_days", 7))
        if days <= 0 or getattr(self.client, "is_demo", False):
            return
        folder = os.path.join(DATA_DIR, "backups")
        try:
            newest = max((os.path.getmtime(os.path.join(folder, f)) for f in os.listdir(folder)), default=0) \
                if os.path.isdir(folder) else 0
            if time.time() - newest > 86400:
                self.db.backup(folder, keep=days)
        except Exception as e:
            self._err("backup", e)

    # Threads --------------------------------------------------------------------
    def start(self):
        self.load_mapping()
        try:
            ts, data = self.refresh_1h()
            with self.lock:
                self.h1_data = data
                self.status["last_1h"] = int(time.time())
        except Exception as e:
            self._err("1h", e)
        self.refresh_stats()
        try:
            self.refresh_m5_stats()
        except Exception as e:
            self._err("5m stats", e)
        try:
            self.refresh_latest()
        except Exception as e:
            self._err("latest", e)
        if getattr(self.client, "is_demo", False):
            try:
                from .demo_feed import DemoFeed
                self.demo_feed = DemoFeed(os.path.join(DATA_DIR, "demo-runelite"), self.mapping, self.latest)
            except Exception as e:
                self._err("demo RuneLite feed", e)
        self.ingest_live()
        self.refresh_fill_rates()
        self.rebuild()
        threading.Thread(target=self.refresh_news, kwargs={"full": True}, daemon=True, name="news").start()
        threading.Thread(target=self._loop, daemon=True, name="poller").start()
        threading.Thread(target=self.backfill, daemon=True, name="backfill").start()

    def live_folder(self):
        if self.demo_feed:
            return self.demo_feed.folder
        return self.cfg.get("runelite_folder") or account.default_folder()

    def ingest_live(self, force_worth=False):
        """Read new RuneLite plugin events, rebuild automatic flips, record net worth."""
        try:
            if self.demo_feed:
                self.demo_feed.tick(self.latest)
            folder = self.live_folder()
            n = account.ingest(self.db, folder)
            now = time.time()
            with self.lock:
                self.live["folder"] = folder
                self.live["lastIngest"] = int(now)
                if n:
                    self.live["events"] += n
                    self.live["last"] = int(now)
            if n:
                account.sync_flips(self.db, self.tax)
                self.live_checks(int(now) - 120)
            if self.rows and now - self._last_coach > 300:
                self._last_coach = now
                if self._rates_dirty:
                    self._rates_dirty = False
                    self.refresh_fill_rates()
                self.coach_checks()
                self.statement_check(now)
            if self.demo_feed and not self._demo_seeded and self.rows:
                self._demo_seeded = networth.seed_demo(self.db, self, self.cfg) > 0
            # Net worth: after changes (at most every 5 minutes) and every 15 minutes for price moves.
            ready = self.rows and (self._demo_seeded or not self.demo_feed)
            if ready and ((n and now - self._worth_at > 300) or now - self._worth_at > 900 or force_worth):
                self._worth_at = now
                networth.record(self.db, self, self.cfg)
            return n
        except Exception as e:
            self._err("RuneLite plugin data", e)
            return 0

    def refresh_news(self, full=False, rss_only=False):
        """Game updates, blogs and polls. First run imports two years of posts (about 40 requests)."""
        if self.cfg.get("news_enabled", True) is False:
            return
        try:
            if getattr(self.client, "is_demo", False):
                news.demo_news(self.db, self.mapping)
                return
            if not rss_only:
                have = news.status(self.db).get("n") or 0
                news.fetch_wiki(self.db, self.client, self.mapping, days=800 if (full and not have) else 21)
            news.fetch_rss(self.db, self.client, self.mapping)
            analytics.clear_cache()
        except Exception as e:
            self._err("news", e)

    def stop(self):
        self._stop.set()

    def _loop(self):
        period = max(30, int(self.cfg.get("latest_poll_seconds", 60)))
        next_latest = time.time() + period
        next_5m = time.time() + 5
        last_hour_seen = int(time.time()) // 3600
        last_prune = 0
        last_mapping = time.time()
        last_jobs = time.time() - 3600 + 600  # first run 10 minutes in, after the backfill
        last_live = 0
        last_news = last_wiki = time.time()  # the startup thread just fetched
        while not self._stop.wait(2):
            if time.time() - last_live >= 5:
                last_live = time.time()
                self.ingest_live()
            now = time.time()
            try:
                if now >= next_latest:
                    next_latest = now + period
                    self.refresh_latest()
                    self.target_checks()
            except Exception as e:
                self._err("latest", e)
            try:
                if now >= next_5m:
                    # Windows close on 5 minute boundaries; poll ~40s after each.
                    next_5m = (int(now) // 300 + 1) * 300 + 40
                    self.refresh_5m()
            except Exception as e:
                self._err("5m", e)
            hour = int(now) // 3600
            if hour != last_hour_seen and now % 3600 > 90:
                last_hour_seen = hour
                try:
                    ts, data = self.refresh_1h()
                    with self.lock:
                        self.h1_data = data
                        self.status["last_1h"] = int(now)
                    self.refresh_stats()
                    self.rebuild()
                    analytics.clear_cache()
                except Exception as e:
                    self._err("1h", e)
            if now - last_jobs > 3600 and self.rows:
                last_jobs = now
                self.hourly_jobs()
            if now - last_news > 1800:
                last_news = now
                full_wiki = now - last_wiki > 6 * 3600
                if full_wiki:
                    last_wiki = now
                threading.Thread(target=self.refresh_news, kwargs={"rss_only": not full_wiki}, daemon=True).start()
            if now - last_prune > 6 * 3600:
                last_prune = now
                try:
                    self.db.prune(self.cfg.get("keep_5m_days", 7), self.cfg.get("keep_1h_days", 365))
                except Exception as e:
                    self._err("prune", e)
            if now - last_mapping > 12 * 3600:
                last_mapping = now
                try:
                    self.load_mapping(force=True)
                except Exception as e:
                    self._err("mapping", e)
