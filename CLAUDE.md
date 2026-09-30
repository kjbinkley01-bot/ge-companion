# GE Companion: notes for Claude Code

Personal Old School RuneScape Grand Exchange and account tool that runs locally on Windows.
A free alternative to GE Tracker premium, built only on public, read-only data.

## Hard rules
* Never automate the game: no input automation, screen reading, memory reading or macros.
  Jagex rules ban this. Market data comes only from public web APIs. Account data comes only
  from the listen-only RuneLite plugin (`runelite-plugin/`), which reads RuneLite's plugin API
  events and writes local files; it must never send input, click, or change game state.
* Keep the backend on the Python standard library (no pip installs). The owner starts it by
  double-clicking `start.bat`.
* Every request to the Wiki must send the configured User-Agent. Never loop `/latest?id=` over
  items; use the bulk endpoints. Keep polling at 30 seconds or slower.
* Do not use em dashes or en dashes anywhere (code comments, UI text, docs).

## Layout
* `run.py`: entry point (`--demo` for offline synthetic data, `--no-browser`, `--port`).
* `geco/wiki.py`: Wiki prices API (`/mapping`, `/latest`, `/5m`, `/1h`, `/timeseries`) and
  hiscores (`index_lite.json`, per account type).
* `geco/engine.py`: background poller. Backfills missing hourly windows (`backfill_hours`,
  max 720) newest first, then the 5 minute windows the stability score needs, one bulk
  request a second. Runs alerts, limit reset notices, hourly net worth snapshots, daily backups.
* `geco/market.py`: GE tax (2%, floor, 5m cap, exempt names in config), fill model, flip
  metrics, margin stability and trap flags, movers, volume spikes, decanting, alert conditions.
* `geco/db.py`: SQLite (`data/ge_companion.sqlite3`): `h1` and `m5` price history, watchlist,
  alerts, notifications, flips, holdings, networth, drops, chase, custom recipes, goals.
  Hourly and 5 minute stats aggregate in SQL. Column additions go in `MIGRATIONS`.
* `geco/features.py`: flip log math and analytics, buy limit windows, portfolio, account view,
  goals, boss drops and dry streaks, holdings paste parser.
* `geco/recipes.py`: recipe engine (processing methods, item sets, custom recipes), priced live.
  Recipe item names must match the Wiki mapping exactly.
* `geco/analytics.py`: seasonality, category indices, correlation, backtester. Reads only saved
  history, never the Wiki. `geco/categories.py`: name rules for index categories.
* `geco/forecast.py`: long term profit forecast. Daily series from `h1`, damped trend (Holt)
  on log price kept only if it beats a flat forecast on held out days, smoothed per side
  volume, live ROI fading to the historical ROI, Monte Carlo bands from resampled past days.
* `geco/forecast_eval.py`: walk-forward backtest and tuner for the forecast on imported daily
  history (`d1` table, filled from the Wiki's bulk `/24h` windows by `Engine.import_daily`).
  Tunes on older dates, adopts new settings only if they also win on held out newer dates.
  The shipped `DEFAULT_PARAMS` and `geco/forecast_baseline.json` come from a run on a year of
  real data; re-run and update both together if the model changes.
* `geco/hold.py`: holding outlook. Trend features from daily grids, ridge regression per
  horizon (7/30/90 days), walk-forward tested; a horizon's signal is only used if it beat
  "no change" on held out months, otherwise the outlook centers on no change with odds and
  ranges from real moves. Ships `geco/hold_model.json` (two years, Sep 2026: no signal passed).
* `runelite-plugin/`: RuneLite plugin (Java 11, Gradle, `run-plugin.bat` or `gradlew run`
  sideloads it). Writes JSON lines to `~/.runelite/ge-companion/events-YYYY-MM.jsonl`:
  `{v, t (ms), type, acct (rsprofile key), name, ...}` with types login, logout, offer (slot,
  state, item, price, total, done, spent: cumulative), container (bank, inventory, equipment,
  looting_bag, seed_vault, rune_pouch, death_storage, ge_collect_0..7; items as [[id, qty]],
  noted ids canonicalized), loot, xp. JUnit tests: `gradle test` in that folder.
* `geco/account.py`: ingests plugin files incrementally (byte offsets, whole lines only), keeps
  latest GE offers, reconstructs fills from cumulative offer changes, FIFO matches them into
  automatic flip log rows (`flips.source='auto'`, rebuilt each time; `flip_ignore` opts out).
* `geco/networth.py`: account valuation (sell price after tax or mid), GE escrow (reserved
  coins, listed items, collection boxes), cost basis from open lots plus starting costs
  (`cost_seed`: items first seen with no cost get that day's value, topped up the first time a
  new storage is seen), portfolio categories,
  history in `account_worth` (5 minute buckets, per account and `*` combined), backcast of
  today's holdings at past prices.
* `geco/advice.py`: recommendations from what an account holds, each with a confidence label.
* `geco/demo_feed.py`: writes plugin format events in demo mode (`data/demo-runelite`).
* `geco/server.py`: JSON API plus static files, bound to 127.0.0.1.
* `web/`: vanilla JS dashboard (`app.js`), no build step. Charts are hand-rolled SVG
  (`priceChart`, `lineChart`, `barChart`, `heatmap`), colors are CSS tokens in `style.css`.
* `tests/`: `python -m unittest discover -s tests` (standard library only).
* `data/`: runtime files (config, database, backups, cached mapping). Git ignored.

## Price terms
`high` = latest instant-buy price (what a seller receives), `low` = latest instant-sell price
(what a patient buyer pays). Flip profit = high minus tax(high) minus low.

## Fill model
A patient buy at `low` fills against instant sells (`lv` volume); a sell at `high` fills against
instant buys (`hv`). Estimates use `fill_share` (default 20%) of the slower side. Stability is the
share of recent 5 minute windows where avg high minus tax beat avg low.

## Testing
`python -m unittest discover -s tests`, then `python run.py --demo --no-browser --port 8799` and
hit `/api/status`, `/api/market`, `/api/indices`, `/api/recipes`, etc. Demo data lives in
`geco/demo.py` and uses a separate database and mapping cache; demo mode backfills 14 days
instantly so every analytics view has data.

## Status
v2: everything in v1 plus a realistic fill model, margin stability and trap flags, buy limit
tracking, slot planner, market indices, portfolio and net worth, money making by GP/hr, item set
arbitrage, backtester, seasonality heatmap, correlations, composite and watchlist alerts, desktop
notifications, KC gains, drop log with dry streaks, flip analytics, backups and export,
and a Forecast tab (expected profit with likely range over 7/30/90 days).
v3: RuneLite plugin for live account data, Net worth tab (portfolio style valuation, history,
allocation, live GE slots, recommendations), automatic flip log from real trades.
Open questions to verify in game: whether a sell offer's `spent` is before or after tax
(`account.sell_split` handles both), and how promptly collection box containers update.
Hiscores response format verified against the live endpoint (Sep 2026).
Next: guide price tracking, clue reward values, custom index categories.
