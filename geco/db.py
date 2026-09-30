"""SQLite storage: price history, watchlist, alerts, flip log, portfolio, hiscores, goals, drops."""
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
CREATE INDEX IF NOT EXISTS m5_ts ON m5(ts);
CREATE TABLE IF NOT EXISTS d1 (
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
CREATE TABLE IF NOT EXISTS holdings (
    hid INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, qty INTEGER NOT NULL,
    cost_each INTEGER, note TEXT, added INTEGER);
CREATE TABLE IF NOT EXISTS networth (
    ts INTEGER PRIMARY KEY, total INTEGER, holdings INTEGER, flips INTEGER, coins INTEGER,
    cost INTEGER);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS drops (
    did INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL, mode TEXT NOT NULL,
    boss TEXT NOT NULL, item_id INTEGER, item_name TEXT NOT NULL, qty INTEGER DEFAULT 1,
    kc INTEGER, ts INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS chase (
    cid INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL, mode TEXT NOT NULL,
    boss TEXT NOT NULL, item_name TEXT NOT NULL, rate_n REAL NOT NULL, start_kc INTEGER,
    created INTEGER);
CREATE TABLE IF NOT EXISTS recipes_custom (
    rid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, skill TEXT, level INTEGER,
    xp REAL, per_hour REAL, inputs TEXT NOT NULL, outputs TEXT NOT NULL, coins REAL,
    created INTEGER);
"""

# Columns added after v1. Each is applied once to databases created by older versions.
MIGRATIONS = [
    ("flips", "ref_low", "INTEGER"),
    ("flips", "ref_high", "INTEGER"),
    ("alerts", "extra", "TEXT"),
    ("flips", "source", "TEXT"),   # 'auto' rows are rebuilt from real GE trades (RuneLite plugin)
    ("flips", "acct", "TEXT"),
    ("flips", "tag", "TEXT"),      # strategy tag (manual rows directly, auto rows via trade_tags)
]


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
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
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

    def many(self, sql, rows):
        with self.lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    # Key/value settings stored with the data (cash stack and similar) ----------
    def kv_get(self, key, default=None):
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except ValueError:
            return default

    def kv_set(self, key, value):
        self.run("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, json.dumps(value)))

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

    def snapshot_count(self, kind, since=0):
        r = self.one("SELECT COUNT(*) AS n FROM snapshots WHERE kind=? AND ts>=?", (kind, int(since)))
        return r["n"] if r else 0

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

    def history_many(self, item_ids, since, table="h1"):
        """{id: [rows ordered by ts]} for a set of items (chunked to stay under SQLite's limits)."""
        out = {}
        ids = [int(i) for i in item_ids]
        for k in range(0, len(ids), 400):
            chunk = ids[k:k + 400]
            marks = ",".join("?" * len(chunk))
            for r in self.q(f"SELECT id, ts, ah, hv, al, lv FROM {table} WHERE ts>=? AND id IN ({marks}) "
                            "ORDER BY id, ts", [int(since)] + chunk):
                out.setdefault(r["id"], []).append(r)
        return out

    def bucketed(self, item_ids, since, bucket):
        """Volume weighted price and volume per item per time bucket, aggregated in SQL."""
        out = {}
        ids = [int(i) for i in item_ids]
        b = int(bucket)
        for k in range(0, len(ids), 400):
            chunk = ids[k:k + 400]
            marks = ",".join("?" * len(chunk))
            sql = (f"SELECT id, (ts / {b}) * {b} AS bt, "
                   f"SUM({_NUM}) AS num, SUM({_DEN}) AS den, SUM(hv + lv) AS vol "
                   f"FROM h1 WHERE ts>=? AND id IN ({marks}) GROUP BY id, bt ORDER BY id, bt")
            for r in self.q(sql, [int(since)] + chunk):
                out.setdefault(r["id"], []).append(r)
        return out

    def hourly_stats(self, now_hour):
        """Per-item stats from stored hourly windows, used for volume, fills and movers.

        Everything is aggregated inside SQLite in one grouped query, so the cost stays
        flat as history grows. Returns ({id: {...}}, newest_ts) with 24h volume per side,
        the average hourly volume over the prior 23 hours, and volume weighted prices
        at several points in time.
        """
        newest = self.one("SELECT MAX(ts) AS t FROM snapshots WHERE kind='h1'")
        newest = newest["t"] if newest and newest["t"] else None
        if not newest:
            return {}, None
        n = int(newest)
        H = 3600
        # (name, window end, number of hourly windows ending there)
        points = [("p_now", n, 1), ("p_1h", n - H, 1), ("p_6h", n - 6 * H, 2),
                  ("p_24h", n - 24 * H, 3), ("p_7d", n - 7 * 86400, 6)]
        cols = [
            f"SUM(CASE WHEN ts > {n - 24 * H} THEN hv ELSE 0 END) AS hv24",
            f"SUM(CASE WHEN ts > {n - 24 * H} THEN lv ELSE 0 END) AS lv24",
            f"SUM(CASE WHEN ts = {n} THEN hv + lv ELSE 0 END) AS vlast",
            f"SUM(CASE WHEN ts > {n - 24 * H} AND ts < {n} THEN hv + lv ELSE 0 END) AS vprev",
        ]
        for name, end, hours in points:
            cond = f"ts > {end - hours * H} AND ts <= {end}"
            cols.append(f"SUM(CASE WHEN {cond} THEN {_NUM} ELSE 0 END) AS {name}_n")
            cols.append(f"SUM(CASE WHEN {cond} THEN {_DEN} ELSE 0 END) AS {name}_d")
        since = n - 7 * 86400 - 6 * H
        rows = self.q(f"SELECT id, {', '.join(cols)} FROM h1 WHERE ts > ? GROUP BY id", (since,))
        result = {}
        for r in rows:
            s = {
                "hv24": r["hv24"] or 0, "lv24": r["lv24"] or 0,
                "vol24": (r["hv24"] or 0) + (r["lv24"] or 0),
                "vol_last_hour": r["vlast"] or 0,
                "avg_hourly_prev": (r["vprev"] or 0) / 23.0,
            }
            for name, _, _ in points:
                d = r[name + "_d"]
                s[name] = (r[name + "_n"] / d) if d else None
            result[r["id"]] = s
        return result, newest

    def m5_stats(self, since, tax_bp, tax_cap):
        """Margin stability and volatility per item from saved 5 minute windows.

        held     windows where the average instant-buy minus tax still beat the average
                 instant-sell (a flip would have worked in that window)
        held_raw the same without tax, used for tax exempt items
        cv       coefficient of variation of the mid price (volatility)
        """
        bp, cap = int(tax_bp), int(tax_cap)
        both = "ah IS NOT NULL AND al IS NOT NULL"
        taxed = f"(ah - MIN(ah * {bp} / 10000, {cap}) - al)"
        mid = "((ah + al) / 2.0)"
        sql = (f"SELECT id, COUNT(*) AS traded, "
               f"SUM(CASE WHEN {both} THEN 1 ELSE 0 END) AS both_n, "
               f"SUM(CASE WHEN {both} AND {taxed} > 0 THEN 1 ELSE 0 END) AS held, "
               f"SUM(CASE WHEN {both} AND ah - al > 0 THEN 1 ELSE 0 END) AS held_raw, "
               f"AVG(CASE WHEN {both} THEN {taxed} END) AS avg_margin, "
               f"AVG(CASE WHEN {both} THEN ah - al END) AS avg_margin_raw, "
               f"AVG(CASE WHEN {both} THEN {mid} END) AS mid_mean, "
               f"AVG(CASE WHEN {both} THEN {mid} * {mid} END) AS mid_sq, "
               f"SUM(hv) AS hv, SUM(lv) AS lv "
               f"FROM m5 WHERE ts >= ? GROUP BY id")
        out = {}
        for r in self.q(sql, (int(since),)):
            mean, sq = r["mid_mean"], r["mid_sq"]
            cv = None
            if mean and sq is not None and r["both_n"] >= 3:
                var = max(0.0, sq - mean * mean)
                cv = (var ** 0.5) / mean
            out[r["id"]] = {"traded": r["traded"], "both": r["both_n"], "held": r["held"],
                            "held_raw": r["held_raw"], "avg_margin": r["avg_margin"],
                            "avg_margin_raw": r["avg_margin_raw"], "cv": cv,
                            "hv": r["hv"], "lv": r["lv"]}
        return out

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

    # Backups --------------------------------------------------------------------
    def backup(self, folder, keep=10):
        """Consistent copy of the live database (safe while the app is writing)."""
        os.makedirs(folder, exist_ok=True)
        base = os.path.splitext(os.path.basename(self.path))[0]
        dest = os.path.join(folder, f"{base}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3")
        target = sqlite3.connect(dest)
        try:
            with self.lock:
                self.conn.backup(target)
        finally:
            target.close()
        olds = sorted(f for f in os.listdir(folder) if f.startswith(base + "-") and f.endswith(".sqlite3"))
        for f in olds[:-keep] if keep else []:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
        return dest


# Volume weighted price parts for one row: numerator and the matching volume. A side
# only counts when it has a price, which mirrors how the Wiki reports empty windows.
_NUM = "(COALESCE(ah * hv, 0) + COALESCE(al * lv, 0))"
_DEN = "((CASE WHEN ah IS NOT NULL THEN hv ELSE 0 END) + (CASE WHEN al IS NOT NULL THEN lv ELSE 0 END))"


def wprice(row):
    """Volume weighted average of both sides of one window (dict with ah, hv, al, lv)."""
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
