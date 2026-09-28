"""Background poller and in-memory market state."""
import json
import logging
import os
import threading
import time

from . import market
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
        self.rows = []
        self.by_id = {}
        self.tax = market.Tax(cfg, {})
        self.status = {"started": int(time.time()), "last_latest": None, "last_5m": None,
                       "last_1h": None, "backfill": "pending", "errors": []}
        self._stop = threading.Event()

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

    def refresh_stats(self):
        now_hour = int(time.time()) // 3600 * 3600
        stats, _ = self.db.hourly_stats(now_hour)
        with self.lock:
            self.stats = stats

    def backfill(self):
        hours = int(self.cfg.get("backfill_hours", 24))
        now_hour = int(time.time()) // 3600 * 3600
        missing = [now_hour - k * 3600 for k in range(2, hours + 2)
                   if not self.db.have_snapshot("h1", now_hour - k * 3600)]
        with self.lock:
            self.status["backfill"] = f"0/{len(missing)}" if missing else "done"
        for i, ts in enumerate(sorted(missing)):
            if self._stop.is_set():
                return
            try:
                self.refresh_1h(ts)
            except Exception as e:
                self._err("backfill", e)
            with self.lock:
                self.status["backfill"] = f"{i + 1}/{len(missing)}"
            time.sleep(1.0)  # be gentle with the Wiki
        with self.lock:
            self.status["backfill"] = "done"
        self.refresh_stats()
        self.rebuild()

    def rebuild(self):
        with self.lock:
            rows = market.build_market(self.mapping, self.latest, self.h1_data, self.stats,
                                       self.tax, self.cfg)
            self.rows = rows
            self.by_id = {r["id"]: r for r in rows}

    def evaluate_alerts(self):
        now = int(time.time())
        cooldown = int(self.cfg.get("alert_cooldown_minutes", 30)) * 60
        for a in self.db.q("SELECT * FROM alerts WHERE enabled=1"):
            if a["last_fired"] and now - a["last_fired"] < cooldown:
                continue
            row = self.by_id.get(a["item_id"])
            msg = market.check_alert(a, row)
            if not msg:
                continue
            self.db.run("INSERT INTO notifications (ts, alert_id, item_id, message) VALUES (?,?,?,?)",
                        (now, a["aid"], a["item_id"], msg))
            if a["once"]:
                self.db.run("UPDATE alerts SET last_fired=?, enabled=0 WHERE aid=?", (now, a["aid"]))
            else:
                self.db.run("UPDATE alerts SET last_fired=? WHERE aid=?", (now, a["aid"]))

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
            self.refresh_latest()
        except Exception as e:
            self._err("latest", e)
        threading.Thread(target=self._loop, daemon=True, name="poller").start()
        threading.Thread(target=self.backfill, daemon=True, name="backfill").start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        period = max(30, int(self.cfg.get("latest_poll_seconds", 60)))
        next_latest = time.time() + period
        next_5m = time.time() + 5
        last_hour_seen = int(time.time()) // 3600
        last_prune = 0
        last_mapping = time.time()
        while not self._stop.wait(2):
            now = time.time()
            try:
                if now >= next_latest:
                    next_latest = now + period
                    self.refresh_latest()
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
                except Exception as e:
                    self._err("1h", e)
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
