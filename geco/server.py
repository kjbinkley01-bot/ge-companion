"""Local HTTP server: JSON API plus the dashboard files. Binds to 127.0.0.1 only."""
import base64
import hmac
import json
import math
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import account, advice, analytics, brief, coach, edge, events, extras, fills, guard, push, targets, wealth, indicators, news, risk, config, features, forecast, forecast_eval, hold, market, networth, recipes
from .wiki import ApiError


SETTING_KEYS = ("user_agent", "latest_poll_seconds", "stale_minutes", "keep_5m_days", "keep_1h_days",
                "alert_cooldown_minutes", "fill_share", "trap_gap_minutes", "stability_hours",
                "history_import_days",
                "backfill_hours", "notify_limit_reset", "auto_backup_days",
                "runelite_folder", "networth_value", "networth_include_manual", "fill_share_measured",
                "push_discord_webhook", "push_ntfy_topic", "push_ntfy_server", "push_events", "statement_time",
                "lan_enabled")


class App:
    def __init__(self, cfg, engine, db, client):
        self.cfg, self.engine, self.db, self.client = cfg, engine, db, client


def make_handler(app):
    E, D = app.engine, app.db

    class Handler(BaseHTTPRequestHandler):
        server_version = "Bankstanding/1.0"

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

        def _allowed(self):
            """Only this computer, or your phone on home Wi-Fi with the password (LAN mode).

            Also blocks other websites from driving the app through your browser: the Host
            header must be this app's (stops DNS rebinding) and changes must come from this
            app's own pages (Origin check).
            """
            ip = self.client_address[0]
            local = ip in ("127.0.0.1", "::1") or ip.startswith("::ffff:127.")
            if not local:
                pw = app.cfg.get("lan_password") or ""
                ok = False
                if app.cfg.get("lan_enabled") and len(pw) >= 8:
                    auth = self.headers.get("Authorization") or ""
                    if auth.startswith("Basic "):
                        try:
                            given = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)[-1]
                            ok = hmac.compare_digest(given.encode(), pw.encode())
                        except (ValueError, UnicodeDecodeError):
                            ok = False
                if not ok:
                    self._send(401, {"error": "password needed"}, extra={"WWW-Authenticate": 'Basic realm="Bankstanding"'})
                    return False
            else:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
                if host not in ("127.0.0.1", "localhost", "::1", ""):
                    self._send(403, {"error": "unexpected host"})
                    return False
            if self.command != "GET":
                origin = self.headers.get("Origin") or self.headers.get("Referer")
                if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
                    self._send(403, {"error": "cross site request refused"})
                    return False
            return True

        def _guard(self, fn):
            if not self._allowed():
                return
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
                table = {"5m": "m5", "1d": "d1"}.get(q.get("res"), "h1")
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
                out = {k: app.cfg.get(k) for k in SETTING_KEYS + ("tax_rate", "tax_cap")}
                out["lanPasswordSet"] = len(app.cfg.get("lan_password") or "") >= 8
                out["lanAddresses"] = lan_addresses()
                out["pushEvents"] = push.EVENTS
                return self._send(200, out)
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
            # Trading terminal, performance, risk, news --------------------------------
            if path == "/api/performance":
                acct = q.get("acct") or None
                days = float(q["days"]) if q.get("days") not in (None, "", "all") else None
                perf = networth.performance(D, acct, days)
                if perf.get("ok"):
                    bench = risk.benchmark(D, E, perf["from"])
                    perf["benchmark"] = bench
                    perf["marketPct"] = bench[-1]["v"] if bench else None
                    if perf["marketPct"] is not None:
                        perf["excess"] = perf["returnPct"] - perf["marketPct"]
                return self._send(200, perf)
            if path == "/api/risk":
                acct = q.get("acct") or None
                return self._send(200, analytics.cached(("risk", acct or "*", int(time.time() // 600)), 600,
                                                        lambda: risk.account_risk(D, E, _view(app, acct), app.cfg)))
            if path == "/api/marketindex":
                days = max(2, min(800, int(float(q.get("days", 365)))))
                return self._send(200, {"series": risk.market_index(D, E, days)["series"]})
            if path == "/api/news":
                held = set()
                try:
                    held = {h["id"] for h in networth.combined_view(D, E, app.cfg)["holdings"]}
                except Exception:
                    pass
                item = int(q["item"]) if q.get("item") else None
                rows = news.feed(D, E.mapping, float(q.get("days", 60)), q.get("kind") or None, item,
                                 int(q.get("limit", 150)), held)
                return self._send(200, {"items": rows, "status": news.status(D)})
            if path == "/api/events/study":
                return self._send(200, _background(("study", E.params_version), 6 * 3600,
                                                   lambda: events.study(D, E)))
            if path == "/api/events/item":
                iid = int(q["id"])
                return self._send(200, analytics.cached(("evitem", iid), 3600, lambda: events.item_history(D, E, iid)))
            if path == "/api/indicators/report":
                return self._send(200, _background(("indicators", E.params_version), 12 * 3600,
                                                   lambda: indicators.report(D, E.tax, E)))
            if path == "/api/wealth":
                return self._send(200, {"goals": wealth.goals(D, E, app.cfg),
                                        "sources": wealth.sources(D, E, app.cfg, int(q.get("days", 30)))})
            if path == "/api/edge":
                return self._send(200, edge.report(D, E, features.flip_rows(D, E), int(q.get("days", 90))))
            if path == "/api/paper":
                return self._send(200, {"rules": [edge.paper_results(D, E, r["pid"])
                                                  for r in D.q("SELECT pid FROM paper_rules ORDER BY pid DESC")],
                                        "tags": edge.TAGS})
            if path == "/api/targets":
                return self._send(200, {"targets": targets.view(D, E)})
            if path == "/api/guard":
                ids = [int(x) for x in q["ids"].split(",") if x] if q.get("ids") else \
                    [h["id"] for h in networth.combined_view(D, E, app.cfg)["holdings"]] + [r["id"] for r in D.q("SELECT id FROM watchlist")]
                res = guard.scan(D, E, ids[:300])
                return self._send(200, {"items": [dict(v, id=k, name=(E.mapping.get(k) or {}).get("name")) for k, v in
                                                  sorted(res.items(), key=lambda kv: -kv[1]["score"])]})
            if path == "/api/guide":
                return self._send(200, extras.guide(D, app.client, int(q["id"])))
            if path == "/api/clues":
                return self._send(200, {"tiers": extras.clues(D, E, networth.Valuer(E, app.cfg.get("networth_value", "sell")))})
            if path == "/api/categories/custom":
                cats = extras.custom_categories(D)
                for c in cats:
                    c["names"] = [(E.mapping.get(i) or {}).get("name") for i in c["items"]]
                return self._send(200, {"categories": cats})
            if path == "/api/plugin/summary":
                return self._send(200, _plugin_summary(app, int(q["item"]) if q.get("item") else None))
            if path == "/api/statement":
                last = brief.last_seen(D)
                since = int(q["since"]) if q.get("since") else (last or int(time.time() - 86400))
                since = max(since, int(time.time() - 14 * 86400))
                st = brief.statement(D, E, app.cfg, since=since)
                st["lastSeen"] = last
                return self._send(200, st)
            if path == "/api/coach":
                return self._send(200, {"items": coach.check(D, E, app.cfg)})
            if path == "/api/fillrates":
                r = fills.rates(D)
                recent = sorted(fills.measure(D), key=lambda x: -x["closed"])[:40]
                for x in recent:
                    x["name"] = (E.mapping.get(x["item"]) or {}).get("name")
                items = [dict(v, id=k, name=(E.mapping.get(k) or {}).get("name")) for k, v in (r.get("items") or {}).items()]
                return self._send(200, {"overall": r.get("overall"), "n": r.get("n"), "sides": r.get("sides"),
                                        "items": sorted(items, key=lambda x: -x["n"]), "recent": recent,
                                        "setting": app.cfg.get("fill_share", 0.2),
                                        "useMeasured": app.cfg.get("fill_share_measured", True)})
            if path == "/api/heatmap":
                rows, _ = self._market_rows()
                liquid = [r for r in rows if r.get("vol24") and r.get("high") and r.get("chg24h") is not None]
                liquid.sort(key=lambda r: -(r["vol24"] * r["high"]))
                out = [{"id": r["id"], "name": r["name"], "icon": r.get("icon"), "value": r["vol24"] * r["high"],
                        "chg": r["chg24h"], "chg7d": r.get("chg7d"), "price": r["high"],
                        "category": networth.category(r["name"])} for r in liquid[:int(q.get("n", 200))]]
                return self._send(200, {"items": out})
            if path == "/api/terminal":
                return self._send(200, _terminal(app, int(q["id"])))
            # Live account (RuneLite plugin) --------------------------------------
            if path == "/api/account/status":
                return self._send(200, _account_status(app))
            if path == "/api/networth":
                return self._send(200, _networth(app, q.get("acct") or None, q.get("days")))
            if path == "/api/networth/advice":
                return self._send(200, analytics.cached(("advice", q.get("acct") or "*", E.params_version), 120,
                                                        lambda: _advice(app, q.get("acct") or None)))
            if path == "/api/slots":
                return self._send(200, _slots(app))
            if path == "/api/account/fills":
                acct = q.get("acct") or None
                rows = account.recent_fills(D, acct, min(500, int(q.get("limit", 100))))
                names = {a["acct"]: a["name"] for a in account.accounts(D)}
                for r in rows:
                    m = E.mapping.get(r["item"], {})
                    r["name"], r["icon"], r["acctName"] = m.get("name"), m.get("icon"), names.get(r["acct"])
                loot = D.q("SELECT * FROM loot" + (" WHERE acct=?" if acct else "") + " ORDER BY t DESC LIMIT 50",
                           (acct,) if acct else ())
                valuer = networth.Valuer(E, app.cfg.get("networth_value", "sell"))
                for l in loot:
                    items = json.loads(l["items"])
                    l["items"] = [{"id": i, "qty": n, "name": E.mapping.get(i, {}).get("name"),
                                   "value": valuer.each(i)[0] * n} for i, n in items]
                    l["value"] = sum(x["value"] for x in l["items"])
                    l["acctName"] = names.get(l["acct"])
                return self._send(200, {"fills": rows, "loot": loot})
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
            if path == "/api/wealth/goals":
                target = int(float(b["target"])) if b.get("target") not in (None, "") else None
                item = int(b["item_id"]) if b.get("item_id") else None
                if not target and not item:
                    raise ValueError("set a target amount or pick an item")
                name = (b.get("name") or "").strip()[:60] or ((E.mapping.get(item) or {}).get("name") if item else "Goal")
                deadline = int(b["deadline"]) if b.get("deadline") else None
                gid = D.run("INSERT INTO wealth_goals (name, target, item_id, qty, deadline, created) VALUES (?,?,?,?,?,?)",
                            (name, target, item, int(b.get("qty") or 1), deadline, now))
                return self._send(200, {"gid": gid})
            if path == "/api/flips/tag":
                row = D.one("SELECT * FROM flips WHERE fid=?", (int(b["fid"]),))
                if not row:
                    raise ValueError("unknown trade")
                edge.set_tag(D, row, b.get("tag"))
                return self._send(200, {"ok": True})
            if path == "/api/paper":
                params = b.get("params") or {}
                if params.get("strategy") not in analytics.STRATEGIES:
                    raise ValueError("pick a rule")
                pid = D.run("INSERT INTO paper_rules (name, params, created) VALUES (?,?,?)",
                            ((b.get("name") or analytics.STRATEGIES[params["strategy"]])[:80], json.dumps(params), now))
                return self._send(200, {"pid": pid})
            if path == "/api/targets":
                iid, side = int(b["item_id"]), b.get("side", "sell")
                if side not in ("sell", "buy"):
                    raise ValueError("side must be buy or sell")
                if b.get("steps"):
                    base = float(b.get("base") or (E.by_id.get(iid) or {}).get("high" if side == "sell" else "low") or 0)
                    plan = targets.ladder(int(b["qty"]), base, [float(x) / 100 for x in b["steps"]], side)
                    if not plan:
                        raise ValueError("enter a quantity and steps")
                    label = "ladder " + ",".join(str(x) for x in b["steps"])
                    for q_, p_ in plan:
                        D.run("INSERT INTO targets (item_id, side, price, qty, note, created, ladder) VALUES (?,?,?,?,?,?,?)",
                              (iid, side, p_, q_, b.get("note"), now, label))
                    return self._send(200, {"added": len(plan)})
                D.run("INSERT INTO targets (item_id, side, price, qty, note, created) VALUES (?,?,?,?,?,?)",
                      (iid, side, int(float(b["price"])), int(b["qty"]) if b.get("qty") else None, b.get("note"), now))
                return self._send(200, {"added": 1})
            if path == "/api/categories/custom":
                cid = extras.add_category(D, b.get("name"), b.get("items") or [])
                analytics.clear_cache()
                return self._send(200, {"cid": cid})
            if path == "/api/statement/seen":
                brief.seen(D)
                return self._send(200, {"ok": True})
            if path == "/api/push/test":
                res = push.send(app.cfg, "Bankstanding test", "Notifications are working. Standing at the bank, professionally.",
                                force=True, wait=True)
                if not res:
                    return self._send(400, {"error": "Add a Discord webhook or an ntfy topic first."})
                return self._send(200, {"results": [{"service": a, "ok": b, "detail": c} for a, b, c in res]})
            if path == "/api/flips/ignore":
                acct, item = b.get("acct"), int(b["item"])
                if b.get("undo"):
                    D.run("DELETE FROM flip_ignore WHERE acct=? AND item=?", (acct, item))
                else:
                    D.run("INSERT OR IGNORE INTO flip_ignore (acct, item) VALUES (?,?)", (acct, item))
                account.sync_flips(D, E.tax)
                return self._send(200, {"ok": True})
            if path == "/api/account/refresh":
                n = E.ingest_live(force_worth=True)
                return self._send(200, {"ok": True, "events": n})
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
                if "runelite_folder" in b:
                    changed["runelite_folder"] = str(b["runelite_folder"] or "").strip()[:500]
                if b.get("networth_value") in ("sell", "market"):
                    changed["networth_value"] = b["networth_value"]
                for k in ("push_discord_webhook", "push_ntfy_topic", "push_ntfy_server"):
                    if k in b:
                        v = str(b[k] or "").strip()[:400]
                        if k == "push_discord_webhook" and v and not v.startswith("https://discord.com/api/webhooks/") \
                                and not v.startswith("https://discordapp.com/api/webhooks/"):
                            raise ValueError("that does not look like a Discord webhook address")
                        changed[k] = v
                if isinstance(b.get("push_events"), dict):
                    changed["push_events"] = {k: bool(b["push_events"].get(k)) for k in push.EVENTS}
                if b.get("statement_time"):
                    hh, mm = str(b["statement_time"]).split(":")
                    changed["statement_time"] = f"{max(0, min(23, int(hh))):02d}:{max(0, min(59, int(mm))):02d}"
                if "lan_enabled" in b:
                    pw = str(b.get("lan_password") or app.cfg.get("lan_password") or "")
                    if b["lan_enabled"] and len(pw) < 8:
                        raise ValueError("choose a password of at least 8 characters for phone access")
                    changed["lan_enabled"] = bool(b["lan_enabled"])
                    if b.get("lan_password"):
                        changed["lan_password"] = pw
                if "fill_share_measured" in b:
                    changed["fill_share_measured"] = bool(b["fill_share_measured"])
                if "networth_include_manual" in b:
                    changed["networth_include_manual"] = bool(b["networth_include_manual"])
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
                      "/api/recipes/custom": ("recipes_custom", "rid", "rid"),
                      "/api/wealth/goals": ("wealth_goals", "gid", "gid"), "/api/paper": ("paper_rules", "pid", "pid"),
                      "/api/targets": ("targets", "tid", "tid"),
                      "/api/categories/custom": ("custom_categories", "cid", "cid")}
            if path not in tables:
                return self._send(404, {"error": "unknown endpoint"})
            table, col, arg = tables[path]
            D.run(f"DELETE FROM {table} WHERE {col}=?", (int(q[arg]),))
            return self._send(200, {"ok": True})

    return Handler


_BG = {}
_BG_LOCK = threading.Lock()


def _background(key, ttl, fn):
    """Run a slow report in a thread; answer {running: true} until it is ready, then cache it."""
    now = time.time()
    with _BG_LOCK:
        hit = _BG.get(key)
        if hit and hit.get("result") is not None and now - hit["at"] < ttl:
            return hit["result"]
        if hit and hit.get("running"):
            return {"running": True, "since": hit["started"]}
        _BG[key] = {"running": True, "started": now, "result": hit.get("result") if hit else None, "at": 0}

    def work():
        try:
            res = fn()
        except Exception as e:  # report the problem instead of spinning forever
            res = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        with _BG_LOCK:
            _BG[key] = {"running": False, "result": res, "at": time.time(), "started": now}
    threading.Thread(target=work, daemon=True).start()
    stale = hit.get("result") if hit else None
    return stale if stale is not None else {"running": True, "since": now}


def _terminal(app, iid):
    """Everything the trading terminal shows for one item."""
    E, D = app.engine, app.db
    m = E.mapping.get(iid)
    if not m:
        return {"error": "unknown item"}
    row = E.by_id.get(iid) or {}
    view = networth.combined_view(D, E, app.cfg)
    pos = next((h for h in view["holdings"] if h["id"] == iid), None)
    slots = []
    for a in account.accounts(D):
        for sl in account.slots(D, a["acct"], E.by_id, E.mapping):
            if sl["item"] == iid and sl["state"] != "EMPTY":
                slots.append(dict(sl, acctName=a["name"]))
    fills = [f for f in account.recent_fills(D, None, 400) if f["item"] == iid][:20]

    def stats():
        series = networth._daily_prices(D, E, [iid], 400)
        grid, vals = risk._grid(series, 365)
        v = vals.get(iid)
        if not v:
            return {}
        known = [x for x in v if x]
        r = risk._rets(v)
        mret = risk.market_index(D, E, 365)["rets"]
        cov, vi, vm = risk._cov(r[-180:], [mret.get(t) for t in grid[1:]][-180:])
        return {"low52": min(known), "high52": max(known), "vol": risk._std(r[-90:]),
                "beta": cov / vm if cov is not None and vm else None,
                "corr": cov / math.sqrt(vi * vm) if cov is not None and vi and vm else None,
                "chg30": (known[-1] / known[-31] - 1) if len(known) > 31 else None,
                "chg90": (known[-1] / known[-91] - 1) if len(known) > 91 else None,
                "chg365": (known[-1] / known[0] - 1) if len(known) > 300 else None}
    return {
        "meta": m, "row": row, "position": pos, "slots": slots, "fills": fills,
        "limit": features.buy_limits(D, E.mapping).get(iid),
        "breakeven": E.tax.breakeven(row.get("low"), iid) if row.get("low") else None,
        "taxEach": E.tax(row["high"], iid) if row.get("high") else None,
        "stats": analytics.cached(("tstats", iid), 1800, stats),
        "news": news.item_news(D, E.mapping, iid, limit=20),
        "watched": D.one("SELECT 1 AS x FROM watchlist WHERE id=?", (iid,)) is not None,
    }


def lan_addresses():
    """This computer's addresses on the home network (for opening the app on a phone)."""
    import socket
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN interface
            ips.add(sk.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127."))
    except OSError:
        pass
    return sorted(ips)


def _plugin_summary(app, item=None):
    """Small summary for the RuneLite panel: net worth, offers needing attention, one item's prices."""
    E, D = app.engine, app.db
    view = networth.combined_view(D, E, app.cfg)
    ch = networth.changes(D, None, view["total"])
    out = {"total": view["total"], "d1": (ch.get("d1") or {}).get("gp"), "cash": view["cash"],
           "coach": [{"title": c["title"], "suggest": c.get("suggest")} for c in coach.check(D, E, app.cfg)][:5]}
    if item:
        r = E.by_id.get(item) or {}
        m = E.mapping.get(item) or {}
        pos = next((h for h in view["holdings"] if h["id"] == item), None)
        lim = features.buy_limits(D, E.mapping).get(item)
        out["item"] = {"id": item, "name": m.get("name"), "high": r.get("high"), "low": r.get("low"),
                       "profit": r.get("profit"), "roi": r.get("roi"), "limit": m.get("limit"),
                       "limitLeft": lim["left"] if lim else m.get("limit"),
                       "suggestBuy": r["low"] + 1 if r.get("low") else None,
                       "suggestSell": r["high"] - 1 if r.get("high") else None,
                       "fillHrs": r.get("fillHrs"), "stability": r.get("stability"),
                       "held": pos["qty"] if pos else 0, "costEach": pos.get("costEach") if pos else None,
                       "chg24h": r.get("chg24h")}
    return out


def _account_status(app):
    E, D = app.engine, app.db
    folder = E.live_folder()
    files = []
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if f.startswith("events-") and f.endswith(".jsonl"):
                p = os.path.join(folder, f)
                files.append({"name": f, "bytes": os.path.getsize(p), "modified": int(os.path.getmtime(p))})
    with E.lock:
        live = dict(E.live)
    return {"folder": folder, "exists": os.path.isdir(folder), "files": files, "accounts": account.accounts(D),
            "live": live, "demo": bool(E.demo_feed),
            "fills": D.one("SELECT COUNT(*) AS n FROM ge_fills")["n"],
            "autoFlips": D.one("SELECT COUNT(*) AS n FROM flips WHERE source='auto'")["n"],
            "ignored": D.q("SELECT * FROM flip_ignore")}


def _view(app, acct):
    E, D, cfg = app.engine, app.db, app.cfg
    return networth.combined_view(D, E, cfg) if not acct else networth.account_view(D, E, acct, cfg)


def _networth(app, acct, days):
    E, D = app.engine, app.db
    view = _view(app, acct)
    days = float(days) if days not in (None, "", "all") else None
    view["changes"] = networth.changes(D, acct, view["total"])
    view["history"] = networth.history(D, acct, days)
    view["backcast"] = analytics.cached(("backcast", acct or "*", int(view["total"]) // 1000000, days), 900,
                                        lambda: networth.backcast(D, E, view, int(min(days or 365, 730))))
    view["accountList"] = account.accounts(D)
    view["holdings"] = view["holdings"][:400]
    return view


def _slots(app):
    E, D = app.engine, app.db
    with E.lock:
        by_id = E.by_id
    out = []
    for a in account.accounts(D):
        out.append({"acct": a["acct"], "name": a["name"], "online": a["online"],
                    "slots": account.slots(D, a["acct"], by_id, E.mapping)})
    return {"accounts": out}


def _advice(app, acct):
    E, D = app.engine, app.db
    view = _view(app, acct)
    by_id = E.by_id
    accts = [acct] if acct else [a["acct"] for a in account.accounts(D)]
    slots = {a: account.slots(D, a, by_id, E.mapping) for a in accts}
    ids = [h["id"] for h in view["holdings"] if h["how"] not in ("cash", "untradeable") and h["share"] >= 0.02][:30]
    records = networth.item_records(D, E, ids)
    recs = advice.recommend(view, E, slots, features.buy_limits(D, E.mapping), records.get)
    return {"items": recs, "total": view["total"]}


def _hold_universe(db, tax):
    """Daily grids for the most traded items and today's market direction (cached)."""
    now = time.time()
    grids = hold.load_grids(db, tax, now)
    mkt = hold.market_series(grids)
    today = max(mkt) if mkt else None
    return {"grids": grids, "market30": mkt.get(today, 0.0) if today else 0.0}


def serve(app, port):
    lan = app.cfg.get("lan_enabled") and len(app.cfg.get("lan_password") or "") >= 8
    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
