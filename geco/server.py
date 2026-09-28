"""Local HTTP server: JSON API plus the dashboard files. Binds to 127.0.0.1 only."""
import json
import mimetypes
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, features, market
from .wiki import ApiError


class App:
    def __init__(self, cfg, engine, db, client):
        self.cfg, self.engine, self.db, self.client = cfg, engine, db, client


def make_handler(app):
    E, D = app.engine, app.db

    class Handler(BaseHTTPRequestHandler):
        server_version = "GECompanion/1.0"

        def log_message(self, fmt, *args):
            pass

        # Helpers ------------------------------------------------------------------
        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(",", ":"))
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}

        def _qs(self):
            p = urllib.parse.urlparse(self.path)
            return p.path, {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}

        def _static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.normpath(os.path.join(config.WEB_DIR, rel))
            if not full.startswith(config.WEB_DIR) or not os.path.isfile(full):
                return self._send(404, {"error": "not found"})
            # Fixed types: the Windows registry can report .js as text/plain.
            ext = os.path.splitext(full)[1].lower()
            ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                     ".svg": "image/svg+xml", ".png": "image/png"}.get(ext) \
                or mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

        def _guard(self, fn):
            try:
                fn()
            except ApiError as e:
                self._send(502, {"error": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": f"Bad request: {e}"})
            except Exception as e:  # keep the server alive no matter what
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        # Routing ------------------------------------------------------------------
        def do_GET(self):
            self._guard(self._get)

        def do_POST(self):
            self._guard(self._post)

        def do_DELETE(self):
            self._guard(self._delete)

        def _get(self):
            path, q = self._qs()
            if not path.startswith("/api/"):
                return self._static(path)
            if path == "/api/status":
                with E.lock:
                    st = dict(E.status)
                st["items"] = len(E.mapping)
                st["priced"] = len(E.rows)
                st["dbBytes"] = os.path.getsize(D.path) if os.path.exists(D.path) else 0
                st["h1Snapshots"] = (D.one("SELECT COUNT(*) AS n FROM snapshots WHERE kind='h1'") or {}).get("n")
                return self._send(200, st)
            if path == "/api/market":
                with E.lock:
                    rows = E.rows
                    nat = E.latest.get(market.NATURE_RUNE, {}).get("high")
                    st = {"last_latest": E.status["last_latest"], "backfill": E.status["backfill"]}
                wl = {r["id"] for r in D.q("SELECT id FROM watchlist")}
                return self._send(200, {"rows": rows, "natureRune": nat, "watchlist": sorted(wl),
                                        "status": st, "now": int(time.time())})
            if path == "/api/item":
                iid = int(q["id"])
                m = E.mapping.get(iid)
                if not m:
                    return self._send(404, {"error": "unknown item"})
                return self._send(200, {
                    "meta": m, "row": E.by_id.get(iid),
                    "watched": D.one("SELECT 1 AS x FROM watchlist WHERE id=?", (iid,)) is not None,
                    "alerts": D.q("SELECT * FROM alerts WHERE item_id=? ORDER BY aid", (iid,)),
                    "flips": D.q("SELECT * FROM flips WHERE item_id=? ORDER BY fid DESC LIMIT 20", (iid,)),
                })
            if path == "/api/timeseries":
                return self._send(200, app.client.timeseries(int(q["id"]), q.get("lookback", "24h")))
            if path == "/api/localhistory":
                table = "m5" if q.get("res") == "5m" else "h1"
                days = float(q.get("days", 30))
                rows = D.history(table, int(q["id"]), int(time.time() - days * 86400))
                return self._send(200, {"data": [{"timestamp": r["ts"], "avgHighPrice": r["ah"],
                                                  "avgLowPrice": r["al"], "highPriceVolume": r["hv"],
                                                  "lowPriceVolume": r["lv"]} for r in rows]})
            if path == "/api/decant":
                with E.lock:
                    return self._send(200, market.decant_opportunities(E.mapping, E.latest, E.tax))
            if path == "/api/alerts":
                rows = D.q("SELECT * FROM alerts ORDER BY aid DESC")
                for r in rows:
                    r["name"] = E.mapping.get(r["item_id"], {}).get("name")
                    r["icon"] = E.mapping.get(r["item_id"], {}).get("icon")
                    r["label"] = market.ALERT_KINDS.get(r["kind"], r["kind"])
                return self._send(200, {"alerts": rows, "kinds": market.ALERT_KINDS})
            if path == "/api/notifications":
                since = int(q.get("since", 0))
                rows = D.q("SELECT * FROM notifications WHERE nid>? ORDER BY nid DESC LIMIT 100", (since,))
                unseen = D.one("SELECT COUNT(*) AS n FROM notifications WHERE seen=0")["n"]
                return self._send(200, {"items": rows, "unseen": unseen})
            if path == "/api/flips":
                rows = features.flip_rows(D, E)
                return self._send(200, {"flips": rows, "summary": features.flip_summary(rows)})
            if path == "/api/flips.csv":
                body = features.flips_csv(features.flip_rows(D, E))
                return self._send(200, body, "text/csv; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=flip_log.csv"})
            if path == "/api/hiscores":
                player, mode = q["player"].strip(), q.get("mode", "normal")
                if not player or len(player) > 12:
                    return self._send(400, {"error": "Enter a player name (12 characters max)."})
                data = app.client.hiscores(player, mode)
                D.add_hiscore_snap(player, mode, data)
                return self._send(200, features.account_view(D, player, mode, data))
            if path == "/api/goals":
                return self._send(200, features.goal_rows(D, E, q["player"], q.get("mode", "normal")))
            if path == "/api/settings":
                keep = ("user_agent", "latest_poll_seconds", "stale_minutes", "keep_5m_days",
                        "keep_1h_days", "alert_cooldown_minutes", "tax_rate", "tax_cap")
                return self._send(200, {k: app.cfg.get(k) for k in keep})
            return self._send(404, {"error": "unknown endpoint"})

        def _post(self):
            path, q = self._qs()
            b = self._json_body()
            now = int(time.time())
            if path == "/api/watchlist":
                D.run("INSERT OR IGNORE INTO watchlist (id, added) VALUES (?,?)", (int(b["id"]), now))
                return self._send(200, {"ok": True})
            if path == "/api/alerts":
                kind = b["kind"]
                if kind not in market.ALERT_KINDS:
                    raise ValueError("unknown alert kind")
                th = float(b.get("threshold") or 0)
                aid = D.run("INSERT INTO alerts (item_id, kind, threshold, once, enabled, created) "
                            "VALUES (?,?,?,?,1,?)", (int(b["item_id"]), kind, th,
                                                     1 if b.get("once") else 0, now))
                E.evaluate_alerts()
                return self._send(200, {"aid": aid})
            if path == "/api/alerts/toggle":
                D.run("UPDATE alerts SET enabled = 1 - enabled, last_fired = NULL WHERE aid=?", (int(b["aid"]),))
                return self._send(200, {"ok": True})
            if path == "/api/notifications/seen":
                D.run("UPDATE notifications SET seen=1 WHERE seen=0")
                return self._send(200, {"ok": True})
            if path == "/api/notifications/clear":
                D.run("DELETE FROM notifications")
                return self._send(200, {"ok": True})
            if path == "/api/flips":
                qty, buy = int(b["qty"]), int(b["buy_price"])
                if qty <= 0 or buy < 0:
                    raise ValueError("quantity and price must be positive")
                sell = b.get("sell_price")
                sell = int(sell) if sell not in (None, "") else None
                buy_ts = int(b.get("buy_ts") or now)
                sell_ts = int(b.get("sell_ts") or now) if sell is not None else None
                if b.get("fid"):
                    D.run("UPDATE flips SET item_id=?, qty=?, buy_price=?, sell_price=?, buy_ts=?, "
                          "sell_ts=?, note=? WHERE fid=?",
                          (int(b["item_id"]), qty, buy, sell, buy_ts, sell_ts, b.get("note"), int(b["fid"])))
                    return self._send(200, {"fid": int(b["fid"])})
                fid = D.run("INSERT INTO flips (item_id, qty, buy_price, sell_price, buy_ts, sell_ts, note) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (int(b["item_id"]), qty, buy, sell, buy_ts, sell_ts, b.get("note")))
                return self._send(200, {"fid": fid})
            if path == "/api/goals":
                skill = b["skill"]
                tl = int(b.get("target_level") or 99)
                if not 2 <= tl <= 126:
                    raise ValueError("target level must be 2 to 126")
                mi = int(b["method_item_id"]) if b.get("method_item_id") else None
                xe = float(b["xp_each"]) if b.get("xp_each") else None
                gid = D.run("INSERT INTO goals (player, mode, skill, target_level, method_item_id, xp_each, created) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (b["player"].lower(), b.get("mode", "normal"), skill, tl, mi, xe, now))
                return self._send(200, {"gid": gid})
            if path == "/api/settings":
                changed = {}
                if "user_agent" in b and str(b["user_agent"]).strip():
                    changed["user_agent"] = str(b["user_agent"]).strip()[:200]
                for k in ("latest_poll_seconds", "stale_minutes", "keep_5m_days", "keep_1h_days",
                          "alert_cooldown_minutes"):
                    if k in b and b[k] not in (None, ""):
                        changed[k] = max(0, int(b[k]))
                if "latest_poll_seconds" in changed:
                    changed["latest_poll_seconds"] = max(30, changed["latest_poll_seconds"])
                app.cfg.update(changed)
                config.save(app.cfg)
                if "user_agent" in changed and hasattr(app.client, "user_agent"):
                    app.client.user_agent = changed["user_agent"]
                return self._send(200, {"ok": True, "note": "Poll interval changes apply after a restart."})
            return self._send(404, {"error": "unknown endpoint"})

        def _delete(self):
            path, q = self._qs()
            if path == "/api/watchlist":
                D.run("DELETE FROM watchlist WHERE id=?", (int(q["id"]),))
            elif path == "/api/alerts":
                D.run("DELETE FROM alerts WHERE aid=?", (int(q["aid"]),))
            elif path == "/api/flips":
                D.run("DELETE FROM flips WHERE fid=?", (int(q["fid"]),))
            elif path == "/api/goals":
                D.run("DELETE FROM goals WHERE gid=?", (int(q["gid"]),))
            else:
                return self._send(404, {"error": "unknown endpoint"})
            return self._send(200, {"ok": True})

    return Handler


def serve(app, port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
