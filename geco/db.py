"""SQLite storage: price history, watchlist, alerts, flip log, hiscore snapshots, goals."""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS h1 (
    id INTEGER NOT NULL, ts INTEGER NOT NULL,
    ah INTEGER, hv INTEGER NOT NULL, al INTEGER, lv INTEGER NOT NULL,
    PRIMARY KEY (id, ts)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS h1_ts ON h1(ts);
CREATE TABLE IF NOT EXISTS m5 (
    id INTEGER NOT NULL, ts INTEGER NOT NULL,
    ah INTEGER, hv INTEGER NOT NULL, al INTEGER, lv INTEGER NOT NULL,
    PRIMARY KEY (id, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS snapshots (kind TEXT NOT NULL, ts INTEGER NOT NULL,
    PRIMARY KEY (kind, ts));
CREATE TABLE IF NOT EXISTS watchlist (id INTEGER PRIMARY KEY, added INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS alerts (
    aid INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, kind TEXT NOT NULL,
    threshold REAL, once INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
    created INTEGER, last_fired INTEGER);
CREATE TABLE IF NOT EXISTS notifications (
    nid INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, alert_id INTEGER, item_id INTEGER,
    message TEXT, seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS flips (
    fid INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, qty INTEGER NOT NULL,
    buy_price INTEGER NOT NULL, sell_price INTEGER, buy_ts INTEGER, sell_ts INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS hiscore_snaps (
    player TEXT NOT NULL, mode TEXT NOT NULL, ts INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (player, mode, ts));
CREATE TABLE IF NOT EXISTS goals (
    gid INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL, mode TEXT NOT NULL,
    skill TEXT NOT NULL, target_level INTEGER, target_xp INTEGER,
    method_item_id INTEGER, xp_each REAL, created INTEGER);
"""


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # Price history ------------------------------------------------------------
    def store_window(self, table, ts, data):
        rows = []
        for sid, v in data.items():
            hv = v.get("highPriceVolume") or 0
            lv = v.get("lowPriceVolume") or 0
            if not hv and not lv:
                continue
            ah = v.get("avgHighPrice")
            al = v.get("avgLowPrice")
            rows.append((int(sid), int(ts), int(round(ah)) if ah is not None else None, hv,
                         int(round(al)) if al is not None else None, lv))
        with self.lock:
            self.conn.executemany(
                f"INSERT OR REPLACE INTO {table} (id, ts, ah, hv, al, lv) VALUES (?,?,?,?,?,?)",
                rows)
            self.conn.execute("INSERT OR REPLACE INTO snapshots (kind, ts) VALUES (?,?)",
                              (table, int(ts)))
            self.conn.commit()
        return len(rows)

    def have_snapshot(self, kind, ts):
        return self.one("SELECT 1 AS x FROM snapshots WHERE kind=? AND ts=?", (kind, int(ts))) is not None

    def prune(self, keep_5m_days, keep_1h_days):
        now = int(time.time())
        with self.lock:
            if keep_5m_days:
                cut = now - int(keep_5m_days * 86400)
                self.conn.execute("DELETE FROM m5 WHERE ts < ?", (cut,))
                self.conn.execute("DELETE FROM snapshots WHERE kind='m5' AND ts < ?", (cut,))
            if keep_1h_days:
                cut = now - int(keep_1h_days * 86400)
                self.conn.execute("DELETE FROM h1 WHERE ts < ?", (cut,))
                self.conn.execute("DELETE FROM snapshots WHERE kind='h1' AND ts < ?", (cut,))
            self.conn.commit()

    def history(self, table, item_id, since):
        return self.q(f"SELECT ts, ah, hv, al, lv FROM {table} WHERE id=? AND ts>=? ORDER BY ts",
                      (int(item_id), int(since)))

    def hourly_stats(self, now_hour):
        """Per-item stats from stored hourly windows, used for volume and movers.

        Returns {id: {...}} with 24h volume, average hourly volume over the prior
        24h (excluding the newest hour), and volume-weighted prices at several points.
        """
        since = now_hour - 8 * 86400 - 3600
        rows = self.q("SELECT id, ts, ah, hv, al, lv FROM h1 WHERE ts >= ? ORDER BY id, ts", (since,))
        newest = self.one("SELECT MAX(ts) AS t FROM snapshots WHERE kind='h1'")
        newest = newest["t"] if newest and newest["t"] else None
        out = {}
        for r in rows:
            s = out.setdefault(r["id"], {"series": {}})
            s["series"][r["ts"]] = r
        result = {}
        for iid, s in out.items():
            ser = s["series"]
            vol24 = 0
            prev_vols = []
            if newest:
                for k in range(24):
                    row = ser.get(newest - k * 3600)
                    v = (row["hv"] + row["lv"]) if row else 0
                    vol24 += v
                    if k >= 1:
                        prev_vols.append(v)
            last = ser.get(newest) if newest else None
            result[iid] = {
                "vol24": vol24,
                "vol_last_hour": (last["hv"] + last["lv"]) if last else 0,
                "avg_hourly_prev": (sum(prev_vols) / len(prev_vols)) if prev_vols else 0,
                "p_now": _wprice_range(ser, newest, 1),
                "p_1h": _wprice_range(ser, newest - 3600, 1) if newest else None,
                "p_6h": _wprice_range(ser, newest - 6 * 3600, 2) if newest else None,
                "p_24h": _wprice_range(ser, newest - 24 * 3600, 3) if newest else None,
                "p_7d": _wprice_range(ser, newest - 7 * 86400, 6) if newest else None,
            }
        return result, newest

    # Hiscores -------------------------------------------------------------------
    def add_hiscore_snap(self, player, mode, data, min_gap=600):
        key = player.lower()
        last = self.one("SELECT ts FROM hiscore_snaps WHERE player=? AND mode=? ORDER BY ts DESC LIMIT 1",
                        (key, mode))
        now = int(time.time())
        if last and now - last["ts"] < min_gap:
            self.run("UPDATE hiscore_snaps SET data=? WHERE player=? AND mode=? AND ts=?",
                     (json.dumps(data), key, mode, last["ts"]))
            return
        self.run("INSERT INTO hiscore_snaps (player, mode, ts, data) VALUES (?,?,?,?)",
                 (key, mode, now, json.dumps(data)))

    def hiscore_snaps(self, player, mode, since=0):
        rows = self.q("SELECT ts, data FROM hiscore_snaps WHERE player=? AND mode=? AND ts>=? ORDER BY ts",
                      (player.lower(), mode, since))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows


def _wprice(row):
    if not row:
        return None
    ah, hv, al, lv = row["ah"], row["hv"], row["al"], row["lv"]
    num = den = 0
    if ah is not None and hv:
        num += ah * hv
        den += hv
    if al is not None and lv:
        num += al * lv
        den += lv
    return num / den if den else None


def _wprice_range(series, end_ts, hours):
    """Volume weighted price over `hours` windows ending at end_ts (smooths thin items)."""
    if end_ts is None:
        return None
    num = den = 0
    for k in range(hours):
        row = series.get(end_ts - k * 3600)
        if not row:
            continue
        p = _wprice(row)
        v = row["hv"] + row["lv"]
        if p is not None and v:
            num += p * v
            den += v
    return num / den if den else None
