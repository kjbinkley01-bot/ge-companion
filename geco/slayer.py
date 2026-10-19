"""Slayer: your task as the plugin reports it, task history, and the block and skip advisor."""
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS slayer_task (acct TEXT NOT NULL, assigned INTEGER NOT NULL, task TEXT, area TEXT,
    amount INTEGER, master INTEGER, streak INTEGER, done INTEGER, finished INTEGER DEFAULT 0,
    PRIMARY KEY (acct, assigned));
"""


def apply(db, acct, t, e):
    """The latest Slayer state, and the task log: a new task opens a row, a finished one
    (count down to 0) closes it as finished, a task replaced by another (a skip) closes it unfinished."""
    db.kv_set(f"slayer:{acct}", {"t": t, "task": e.get("task"), "area": e.get("area"), "count": e.get("count"),
                                 "original": e.get("original"), "master": e.get("master"), "points": e.get("points"),
                                 "streak": e.get("streak"), "wildyStreak": e.get("wildyStreak"),
                                 "blocked": e.get("blocked") or []})
    cur = db.one("SELECT * FROM slayer_task WHERE acct=? AND done IS NULL ORDER BY assigned DESC LIMIT 1", (acct,))
    count, task = e.get("count") or 0, e.get("task")
    if count > 0 and task:
        if cur and cur["task"] == task and cur["amount"] == e.get("original"):
            return
        if cur:
            db.run("UPDATE slayer_task SET done=?, finished=0 WHERE acct=? AND assigned=?", (t, acct, cur["assigned"]))
        db.run("INSERT OR REPLACE INTO slayer_task (acct, assigned, task, area, amount, master, streak) VALUES (?,?,?,?,?,?,?)",
               (acct, t, task, e.get("area"), e.get("original"), e.get("master"), e.get("streak")))
    elif cur:
        db.run("UPDATE slayer_task SET done=?, finished=1 WHERE acct=? AND assigned=?", (t, acct, cur["assigned"]))


def state(db, acct):
    return db.kv_get(f"slayer:{acct}") if acct else None


def history(db, acct, days=180, now=None):
    """Finished tasks with minutes taken and Slayer XP gained (from the XP log)."""
    now = now or int(time.time())
    out = []
    for r in db.q("SELECT * FROM slayer_task WHERE acct=? AND finished=1 AND assigned>? ORDER BY assigned",
                  (acct, now - days * 86400)):
        xp = db.q("SELECT MIN(xp) AS a, MAX(xp) AS b FROM xp_log WHERE acct=? AND skill='Slayer' AND t BETWEEN ? AND ?",
                  (acct, r["assigned"], r["done"]))
        gained = (xp[0]["b"] - xp[0]["a"]) if xp and xp[0]["a"] is not None else None
        out.append(dict(r, minutes=(r["done"] - r["assigned"]) / 60, xp=gained))
    return out
