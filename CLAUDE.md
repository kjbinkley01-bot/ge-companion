# GE Companion: notes for Claude Code

Personal Old School RuneScape Grand Exchange and account tool that runs locally on Windows.
A free alternative to GE Tracker premium, built only on public, read-only data.

## Hard rules
* Never interact with the game client: no input automation, screen reading, memory reading or
  macros. Jagex rules ban this. Data comes only from public web APIs.
* Keep the backend on the Python standard library (no pip installs). The owner starts it by
  double-clicking `start.bat`.
* Every request to the Wiki must send the configured User-Agent. Never loop `/latest?id=` over
  items; use the bulk endpoints. Keep polling at 30 seconds or slower.
* Do not use em dashes or en dashes anywhere (code comments, UI text, docs).

## Layout
* `run.py`: entry point (`--demo` for offline synthetic data, `--no-browser`, `--port`).
* `geco/wiki.py`: Wiki prices API (`/mapping`, `/latest`, `/5m`, `/1h`, `/timeseries`) and
  hiscores (`index_lite.json`, per account type).
* `geco/engine.py`: background poller, backfills the last 24 hourly windows, runs alerts.
* `geco/market.py`: GE tax (2%, floor, 5m cap, exempt names in config), flip metrics, movers,
  volume spike flags, high alch, decanting.
* `geco/db.py`: SQLite (`data/ge_companion.sqlite3`): `h1` and `m5` price history, watchlist,
  alerts, notifications, flips, hiscore snapshots, goals.
* `geco/features.py`: flip log math, account view, goals.
* `geco/server.py`: JSON API plus static files, bound to 127.0.0.1.
* `web/`: vanilla JS dashboard (`app.js`), no build step. Charts are hand-rolled SVG.
* `data/`: runtime files (config, database, cached mapping). Git ignored.

## Price terms
`high` = latest instant-buy price (what a seller receives), `low` = latest instant-sell price
(what a patient buyer pays). Flip profit = high minus tax(high) minus low.

## Testing
`python run.py --demo --no-browser --port 8799`, then hit `/api/status`, `/api/market`, etc.
Demo data lives in `geco/demo.py` and uses a separate database and mapping cache.

## Status
v1 done: flip finder, movers, watchlist, dashboard alerts, flip log, high alch, decanting,
account (hiscores, XP gains, goals with supply cost), item drawer with charts.
Not yet verified against the live hiscores response format.
Next: money making methods by live GP/hr, boss drop tracker with dry streaks, item set arbitrage.
