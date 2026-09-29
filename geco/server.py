"""Local HTTP server: JSON API plus the dashboard files. Binds to 127.0.0.1 only."""
import json
import math
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import analytics, config, features, forecast, forecast_eval, hold, market, recipes
from .wiki import ApiError


SETTING_KEYS = ("user_agent", "latest_poll_seconds", "stale_minutes", "keep_5m_days", "keep_1h_days",
                "alert_cooldown_minutes", "fill_share", "trap_gap_minutes", "stability_hours",
                "history_import_days",
                "backfill_hours", "notify_limit_reset", "auto_backup_days")


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

        def _market_rows(self):
            with E.lock:
                return E.rows, E.by_id

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
                st["h1Snapshots"] = D.snapshot_count("h1")
                st["m5Snapshots"] = D.snapshot_count("m5")
                st["m5Windows"] = E.m5_windows
                st["d1Snapshots"] = D.snapshot_count("d1")
                bdir = os.path.join(config.DATA_DIR, "backups")
                st["backups"] = sorted(os.listdir(bdir))[-5:] if os.path.isdir(bdir) else []
                return self._send(200, st)
            if path == "/api/market":
                with E.lock:
                    rows = E.rows
                    nat = E.latest.get(market.NATURE_RUNE, {}).get("high")
                    st = {"last_latest": E.status["last_latest"], "backfill": E.status["backfill"],
                          "m5Windows": E.m5_windows}
                wl = {r["id"] for r in D.q("SELECT id FROM watchlist")}
                return self._send(200, {"rows": rows, "natureRune": nat, "watchlist": sorted(wl),
                                        "status": st, "now": int(time.time()),
                                        "limits": features.buy_limits(D, E.mapping),
                                        "tax": {"bp": E.tax.bp, "cap": E.tax.cap},
                                        "fillShare": app.cfg.get("fill_share", 0.2)})
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
                    "holdings": D.q("SELECT * FROM holdings WHERE item_id=?", (iid,)),
                    "limit": features.buy_limits(D, E.mapping).get(iid),
                    "breakeven": E.tax.breakeven(E.by_id.get(iid, {}).get("low"), iid),
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
                    if r["item_id"]:
                        r["name"] = E.mapping.get(r["item_id"], {}).get("name")
                        r["icon"] = E.mapping.get(r["item_id"], {}).get("icon")
                    else:
                        r["name"] = "Any watchlist item"
                    r["label"] = market.ALERT_KINDS.get(r["kind"], r["kind"])
                    r["conditions"] = [{"kind": k, "threshold": t, "label": market.ALERT_KINDS.get(k, k)}
                                       for k, t in market.alert_conditions(r)]
                return self._send(200, {"alerts": rows, "kinds": market.ALERT_KINDS,
                                        "pctKinds": sorted(market.PCT_KINDS),
                                        "noValueKinds": sorted(market.NO_VALUE_KINDS)})
            if path == "/api/notifications":
                since = int(q.get("since", 0))
                rows = D.q("SELECT * FROM notifications WHERE nid>? ORDER BY nid DESC LIMIT 100", (since,))
                unseen = D.one("SELECT COUNT(*) AS n FROM notifications WHERE seen=0")["n"]
                return self._send(200, {"items": rows, "unseen": unseen})
            if path == "/api/flips":
                rows = features.flip_rows(D, E)
                return self._send(200, {"flips": rows, "summary": features.flip_summary(rows),
                                        "limits": features.buy_limits(D, E.mapping)})
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
            if path == "/api/boss":
                return self._send(200, features.boss_view(D, E, q["player"], q.get("mode", "normal")))
            if path == "/api/settings":
                return self._send(200, {k: app.cfg.get(k) for k in SETTING_KEYS + ("tax_rate", "tax_cap")})
            # Analytics -----------------------------------------------------------
            if path == "/api/seasonality":
                iid, days = int(q["id"]), max(3, min(365, int(q.get("days", 30))))
                return self._send(200, analytics.cached(("season", iid, days), 600,
                                                        lambda: analytics.seasonality(D, iid, days)))
            if path == "/api/indices":
                days = max(1, min(365, int(q.get("days", 7))))
                _, by_id = self._market_rows()
                return self._send(200, analytics.cached(("idx", days), 600,
                                                        lambda: analytics.indices(D, E.mapping, by_id, days)))
            if path == "/api/correlated":
                iid, days = int(q["id"]), max(2, min(90, int(q.get("days", 7))))
                _, by_id = self._market_rows()
                return self._send(200, analytics.cached(("corr", iid, days), 600,
                                                        lambda: analytics.correlated(D, E.mapping, by_id, iid, days)))
            if path in ("/api/forecast", "/api/forecast/rank"):
                horizon = max(1, min(180, int(q.get("days", 30))))
                share = float(q.get("share") or app.cfg.get("fill_share", 0.2))
                share = max(0.01, min(1.0, share / 100 if share > 1 else share))
                windows = max(0.25, min(6.0, float(q.get("windows", 2))))
                _, by_id = self._market_rows()
                P, ver = E.forecast_params, E.params_version
                if path == "/api/forecast":
                    iid = int(q["id"])
                    return self._send(200, analytics.cached(
                        ("fc", iid, horizon, share, windows, ver), 300,
                        lambda: forecast.item_forecast(D, iid, by_id.get(iid), E.mapping, E.tax, horizon, share,
                                                       windows, params=P)))
                min_vol = float(q.get("minVol", 1000))
                return self._send(200, analytics.cached(
                    ("fcr", horizon, share, windows, min_vol, ver), 600,
                    lambda: forecast.rank(D, E.mapping, by_id, E.tax, horizon, share, windows, min_vol, params=P)))
            if path in ("/api/hold", "/api/hold/scan", "/api/hold/model"):
                bundle, ver = E.hold_model, E.params_version
                if path == "/api/hold/model":
                    with E.lock:
                        st = E.status["holdTrain"]
                    return self._send(200, {"report": bundle["report"] if bundle else None,
                                            "source": bundle["source"] if bundle else None, "status": st,
                                            "dailyDays": D.snapshot_count("d1")})
                if not bundle:
                    return self._send(200, {"ok": False, "reason": "No holding model yet."})
                _, by_id = self._market_rows()
                universe = analytics.cached(("hold-universe", ver), 900, lambda: _hold_universe(D, E.tax))
                if path == "/api/hold":
                    iid = int(q["id"])
                    qty = max(1, int(float(q.get("qty") or 1)))

                    def one():
                        g = universe["grids"].get(iid) or hold.load_grids(D, E.tax, time.time(), ids=[iid]).get(iid)
                        if not g:
                            return {"id": iid, "ok": False,
                                    "reason": "Needs about 180 days of daily history for this item. Import a year of history in Settings."}
                        row = by_id.get(iid) or {}
                        o = hold.outlook(bundle, g, universe["market30"], row.get("high") or row.get("low"), qty,
                                         E.tax, iid)
                        return dict(o, id=iid, ok=True) if o else {"id": iid, "ok": False, "reason": "Not enough history."}
                    return self._send(200, analytics.cached(("hold", iid, qty, ver), 300, one))
                scope = q.get("scope", "market")

                def scan():
                    if scope == "portfolio":
                        ids = [h["item_id"] for h in D.q("SELECT item_id FROM holdings")]
                    elif scope == "watch":
                        ids = [r["id"] for r in D.q("SELECT id FROM watchlist")]
                    else:
                        ids = list(universe["grids"])
                    out = []
                    for iid in dict.fromkeys(ids):
                        g = universe["grids"].get(iid)
                        if g is None and scope != "market":
                            g = hold.load_grids(D, E.tax, time.time(), ids=[iid]).get(iid)
                        row = by_id.get(iid) or {}
                        o = hold.outlook(bundle, g, universe["market30"], row.get("high") or row.get("low"), 1,
                                         E.tax, iid) if g else None
                        m = E.mapping.get(iid, {})
                        item = {"id": iid, "name": m.get("name", f"Item {iid}"), "icon": m.get("icon"),
                                "members": m.get("members"), "vol24": row.get("vol24")}
                        if not o:
                            out.append(dict(item, ok=False))
                            continue
                        pts = {p["h"]: p for p in o["points"]}
                        out.append(dict(item, ok=True, verdict=o["verdict"], price=o["priceNow"],
                                        ret7=pts.get(7, {}).get("ret"), ret30=pts.get(30, {}).get("ret"),
                                        ret90=pts.get(90, {}).get("ret"), lo30=pts.get(30, {}).get("lo"),
                                        hi30=pts.get(30, {}).get("hi"), pUp30=pts.get(30, {}).get("pUp"),
                                        pUp90=pts.get(90, {}).get("pUp"), chg30=o["facts"]["chg30"],
                                        chg90=o["facts"]["chg90"], monthlyMove=o["facts"]["monthlyMove"],
                                        reason=o["reasons"][0]["signal"] if o["reasons"] else None,
                                        own90up=(o["facts"].get("own90") or {}).get("up"),
                                        own90med=(o["facts"].get("own90") or {}).get("median")))
                    return {"scope": scope, "items": out, "market30": math.exp(universe["market30"]) - 1}
                return self._send(200, analytics.cached(("hold-scan", scope, ver), 300, scan))
            if path == "/api/forecast/report":
                with E.lock:
                    st = {"tune": E.status["tune"], "import": E.status["import"]}
                return self._send(200, {"report": forecast_eval.load_report(config.DATA_DIR), "status": st,
                                        "params": E.forecast_params, "defaults": forecast.DEFAULT_PARAMS,
                                        "dailyDays": D.snapshot_count("d1")})
            if path == "/api/backtest/strategies":
                return self._send(200, {"strategies": analytics.STRATEGIES,
                                        "hoursOfData": D.snapshot_count("h1")})
            # Recipes, sets, portfolio ----------------------------------------------
            if path == "/api/recipes":
                patient = q.get("patient", "1") != "0"
                pricer = E.pricer()
                return self._send(200, {
                    "methods": recipes.money_making(pricer, D, patient,
                                                    smithing=int(q["smithing"]) if q.get("smithing") else None),
                    "sets": recipes.set_arbitrage(pricer, pricer.stats, patient),
                    "custom": D.q("SELECT * FROM recipes_custom ORDER BY rid"),
                })
            if path == "/api/recipes/item":
                return self._send(200, recipes.recipes_using(E.pricer(), int(q["id"]), D))
            if path == "/api/portfolio":
                return self._send(200, features.portfolio(D, E))
            if path == "/api/limits":
                return self._send(200, features.buy_limits(D, E.mapping))
            if path == "/api/export":
                tables = ("watchlist", "alerts", "flips", "holdings", "goals", "drops", "chase",
                          "recipes_custom", "networth")
                body = {t: D.q(f"SELECT * FROM {t}") for t in tables}
                body["coins"] = D.kv_get("coins", 0)
                body["exported"] = int(time.time())
                return self._send(200, json.dumps(body, indent=1), "application/json; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=ge_companion_export.json"})
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
                extra = []
                for c in b.get("extra") or []:
                    if c.get("kind") not in market.ALERT_KINDS:
                        raise ValueError("unknown alert kind")
                    extra.append({"kind": c["kind"], "threshold": float(c.get("threshold") or 0)})
                item = int(b.get("item_id") or 0)
                if item and item not in E.mapping:
                    raise ValueError("unknown item")
                aid = D.run("INSERT INTO alerts (item_id, kind, threshold, once, enabled, created, extra) "
                            "VALUES (?,?,?,?,1,?,?)", (item, kind, th, 1 if b.get("once") else 0, now,
                                                       json.dumps(extra) if extra else None))
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
                    # Record the market at close time for the sell side edge, if not set yet.
                    live = E.by_id.get(int(b["item_id"])) or {}
                    D.run("UPDATE flips SET ref_high=COALESCE(ref_high, ?) WHERE fid=?",
                          (live.get("high"), int(b["fid"])))
                    return self._send(200, {"fid": int(b["fid"])})
                live = E.by_id.get(int(b["item_id"])) or {}
                fid = D.run("INSERT INTO flips (item_id, qty, buy_price, sell_price, buy_ts, sell_ts, note, "
                            "ref_low, ref_high) VALUES (?,?,?,?,?,?,?,?,?)",
                            (int(b["item_id"]), qty, buy, sell, buy_ts, sell_ts, b.get("note"),
                             live.get("low"), live.get("high")))
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
            if path == "/api/holdings":
                iid, qty = int(b["item_id"]), int(b["qty"])
                if iid not in E.mapping or qty <= 0:
                    raise ValueError("pick an item and a positive quantity")
                cost = b.get("cost_each")
                cost = int(cost) if cost not in (None, "") else None
                if b.get("hid"):
                    D.run("UPDATE holdings SET item_id=?, qty=?, cost_each=?, note=? WHERE hid=?",
                          (iid, qty, cost, b.get("note"), int(b["hid"])))
                    return self._send(200, {"hid": int(b["hid"])})
                hid = D.run("INSERT INTO holdings (item_id, qty, cost_each, note, added) VALUES (?,?,?,?,?)",
                            (iid, qty, cost, b.get("note"), now))
                return self._send(200, {"hid": hid})
            if path == "/api/holdings/import":
                by_name = {m["name"].lower(): iid for iid, m in E.mapping.items()}
                parsed, problems = features.parse_holdings(str(b.get("text", "")), by_name)
                live = E.by_id
                rows = []
                for h in parsed:
                    cost = h["cost_each"]
                    if cost is None and b.get("use_live_cost"):
                        cost = (live.get(h["item_id"]) or {}).get("low")
                    rows.append((h["item_id"], h["qty"], cost, None, now))
                D.many("INSERT INTO holdings (item_id, qty, cost_each, note, added) VALUES (?,?,?,?,?)", rows)
                features.networth_snapshot(D, E)
                return self._send(200, {"added": len(rows), "problems": problems})
            if path == "/api/coins":
                D.kv_set("coins", max(0, int(b.get("coins") or 0)))
                features.networth_snapshot(D, E)
                return self._send(200, {"ok": True})
            if path == "/api/drops":
                name = str(b.get("item_name") or "").strip()
                iid = int(b["item_id"]) if b.get("item_id") else None
                if iid and not name:
                    name = E.mapping.get(iid, {}).get("name", "")
                if not iid and name:
                    iid = E.pricer().iid(name)
                if not name or not str(b.get("boss") or "").strip():
                    raise ValueError("enter a boss and an item")
                kc = int(b["kc"]) if b.get("kc") not in (None, "") else None
                did = D.run("INSERT INTO drops (player, mode, boss, item_id, item_name, qty, kc, ts, note) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (b["player"].lower(), b.get("mode", "normal"), b["boss"].strip(), iid, name,
                             max(1, int(b.get("qty") or 1)), kc, now, b.get("note")))
                return self._send(200, {"did": did})
            if path == "/api/chase":
                rate = float(b["rate_n"])
                if rate < 1:
                    raise ValueError("drop rate must be 1 in N with N at least 1")
                cid = D.run("INSERT INTO chase (player, mode, boss, item_name, rate_n, start_kc, created) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (b["player"].lower(), b.get("mode", "normal"), b["boss"].strip(),
                             b["item_name"].strip(), rate, int(b.get("start_kc") or 0), now))
                return self._send(200, {"cid": cid})
            if path == "/api/recipes/custom":
                def legs(key):
                    out = []
                    for leg in b.get(key) or []:
                        name, qty = str(leg[0]).strip(), float(leg[1])
                        if not name or qty <= 0:
                            continue
                        if not any(m["name"].lower() == name.lower() for m in E.mapping.values()):
                            raise ValueError(f"unknown item: {name}")
                        out.append([name, qty])
                    return out
                ins, outs = legs("inputs"), legs("outputs")
                if not ins or not str(b.get("name") or "").strip():
                    raise ValueError("a recipe needs a name and at least one input")
                rid = D.run("INSERT INTO recipes_custom (name, skill, level, xp, per_hour, inputs, outputs, "
                            "coins, created) VALUES (?,?,?,?,?,?,?,?,?)",
                            (b["name"].strip(), (b.get("skill") or "Custom").strip(), int(b.get("level") or 1),
                             float(b.get("xp") or 0), float(b.get("per_hour") or 0), json.dumps(ins),
                             json.dumps(outs), float(b.get("coins") or 0), now))
                return self._send(200, {"rid": rid})
            if path == "/api/hold/train":
                return self._send(200, {"started": E.train_hold()})
            if path == "/api/forecast/tune":
                started = E.tune_forecast(max(7, min(90, int(b.get("horizon") or 30))))
                return self._send(200, {"started": started})
            if path == "/api/import":
                days = max(1, min(1095, int(b.get("days") or app.cfg.get("history_import_days", 365))))
                threading.Thread(target=E.import_daily, args=(days,), daemon=True).start()
                return self._send(200, {"started": True, "days": days})
            if path == "/api/backtest":
                _, by_id = self._market_rows()
                return self._send(200, analytics.backtest(D, E.mapping, by_id, E.tax, b))
            if path == "/api/backup":
                dest = D.backup(os.path.join(config.DATA_DIR, "backups"),
                                keep=max(1, int(app.cfg.get("auto_backup_days", 7) or 7)))
                return self._send(200, {"ok": True, "file": os.path.basename(dest)})
            if path == "/api/settings":
                changed = {}
                if "user_agent" in b and str(b["user_agent"]).strip():
                    changed["user_agent"] = str(b["user_agent"]).strip()[:200]
                for k in ("latest_poll_seconds", "stale_minutes", "keep_5m_days", "keep_1h_days",
                          "alert_cooldown_minutes", "trap_gap_minutes", "stability_hours",
                          "backfill_hours", "auto_backup_days", "history_import_days"):
                    if k in b and b[k] not in (None, ""):
                        changed[k] = max(0, int(float(b[k])))
                if "latest_poll_seconds" in changed:
                    changed["latest_poll_seconds"] = max(30, changed["latest_poll_seconds"])
                if "backfill_hours" in changed:
                    changed["backfill_hours"] = max(1, min(720, changed["backfill_hours"]))
                if "stability_hours" in changed:
                    changed["stability_hours"] = max(1, min(48, changed["stability_hours"]))
                if "fill_share" in b and b["fill_share"] not in (None, ""):
                    v = float(b["fill_share"])
                    changed["fill_share"] = max(0.01, min(1.0, v / 100 if v > 1 else v))
                if "notify_limit_reset" in b:
                    changed["notify_limit_reset"] = bool(b["notify_limit_reset"])
                app.cfg.update(changed)
                config.save(app.cfg)
                if "user_agent" in changed and hasattr(app.client, "user_agent"):
                    app.client.user_agent = changed["user_agent"]
                if "stability_hours" in changed:
                    E.refresh_m5_stats()
                if changed.get("history_import_days"):
                    threading.Thread(target=E.import_daily, daemon=True).start()
                E.rebuild()
                return self._send(200, {"ok": True, "note": "Poll interval and backfill changes apply after a restart."})
            return self._send(404, {"error": "unknown endpoint"})

        def _delete(self):
            path, q = self._qs()
            tables = {"/api/watchlist": ("watchlist", "id", "id"), "/api/alerts": ("alerts", "aid", "aid"),
                      "/api/flips": ("flips", "fid", "fid"), "/api/goals": ("goals", "gid", "gid"),
                      "/api/holdings": ("holdings", "hid", "hid"), "/api/drops": ("drops", "did", "did"),
                      "/api/chase": ("chase", "cid", "cid"),
                      "/api/recipes/custom": ("recipes_custom", "rid", "rid")}
            if path not in tables:
                return self._send(404, {"error": "unknown endpoint"})
            table, col, arg = tables[path]
            D.run(f"DELETE FROM {table} WHERE {col}=?", (int(q[arg]),))
            return self._send(200, {"ok": True})

    return Handler


def _hold_universe(db, tax):
    """Daily grids for the most traded items and today's market direction (cached)."""
    now = time.time()
    grids = hold.load_grids(db, tax, now)
    mkt = hold.market_series(grids)
    today = max(mkt) if mkt else None
    return {"grids": grids, "market30": mkt.get(today, 0.0) if today else 0.0}


def serve(app, port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
